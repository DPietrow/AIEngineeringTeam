"""API authentication: one shared password, exchanged for a signed bearer token (JWT, HS256).

Why this shape, for a single-user tool:
- No user table, roles or sessions in the database. The server holds two secrets: API_PASSWORD
  (what you type) and JWT_SECRET (what signs tokens). Rotate either one and old tokens stop
  working (rotating the password alone does not revoke tokens already issued; rotate JWT_SECRET
  to log everyone out).
- Tokens are stateless, so the API needs no storage to check one, and the browser sends it as an
  `Authorization: Bearer` header. That works cross-origin with no cookies and no CSRF surface.
- The token is a standard JWT, written out here with the standard library instead of adding a
  dependency. It is deliberately minimal: HS256 only, the algorithm is never taken from the
  token, `exp` is mandatory, and the signature is compared in constant time.
"""

import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque

ISSUER = "agentteam"
AUDIENCE = "agentteam-dashboard"
MIN_SECRET_CHARS = 32
_HEADER = {"alg": "HS256", "typ": "JWT"}


class InvalidToken(Exception):
    """The token is malformed, forged, expired or not meant for this API."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise InvalidToken("bad base64") from exc


def _sign(secret: str, signing_input: bytes) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def issue_token(secret: str, ttl_s: int, *, now: float | None = None) -> tuple[str, int]:
    """Returns (token, expires_at as a unix timestamp)."""
    issued = int(now if now is not None else time.time())
    expires = issued + ttl_s
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "dashboard", "iat": issued, "exp": expires}
    signing_input = (
        _b64e(json.dumps(_HEADER, separators=(",", ":")).encode())
        + "."
        + _b64e(json.dumps(claims, separators=(",", ":")).encode())
    )
    signature = _b64e(_sign(secret, signing_input.encode("ascii")))
    return f"{signing_input}.{signature}", expires


def verify_token(secret: str, token: str, *, now: float | None = None) -> dict:
    """Returns the claims, or raises InvalidToken. Never trusts the token's own `alg`."""
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidToken("not a JWT")
    header_b64, claims_b64, signature_b64 = parts
    try:
        header = json.loads(_b64d(header_b64))
    except ValueError as exc:
        raise InvalidToken("bad header") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise InvalidToken("unsupported algorithm")  # rejects alg=none and algorithm confusion
    expected = _sign(secret, f"{header_b64}.{claims_b64}".encode("ascii", "ignore"))
    if not hmac.compare_digest(expected, _b64d(signature_b64)):
        raise InvalidToken("bad signature")
    try:
        claims = json.loads(_b64d(claims_b64))
    except ValueError as exc:
        raise InvalidToken("bad claims") from exc
    if not isinstance(claims, dict):
        raise InvalidToken("bad claims")
    if claims.get("iss") != ISSUER or claims.get("aud") != AUDIENCE:
        raise InvalidToken("wrong issuer or audience")
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or isinstance(exp, bool):
        raise InvalidToken("missing expiry")
    if (now if now is not None else time.time()) >= exp:
        raise InvalidToken("expired")
    return claims


def password_matches(supplied: str, expected: str) -> bool:
    """Constant-time comparison that does not leak the length either (compares SHA-256s)."""
    a = hashlib.sha256(supplied.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


class RateLimiter:
    """Sliding-window limiter, in memory and per process (fine for one API process; with several,
    each enforces its own limit, so the real ceiling is limit x processes)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)  # do not grow without bound
            return deque()
        return hits

    def retry_after(self, key: str, *, now: float | None = None) -> float:
        """Seconds until `key` may act again; 0 if it is allowed now. Does not record a hit."""
        now = now if now is not None else time.monotonic()
        hits = self._trim(key, now)
        if len(hits) < self.limit:
            return 0.0
        return max(0.0, self.window_s - (now - hits[0]))

    def hit(self, key: str, *, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._trim(key, now)
        self._hits[key].append(now)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
