import base64
import json

import pytest

from agentteam.app import create_app
from agentteam.auth import (
    InvalidToken,
    RateLimiter,
    bearer_token,
    issue_token,
    password_matches,
    verify_token,
)

SECRET = "s" * 48
PASSWORD = "correct horse battery staple"


def b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


# --- the token itself ------------------------------------------------------------------


def test_token_roundtrip_and_default_lifetime_is_a_year(settings):
    from agentteam.config import Settings

    assert Settings().jwt_ttl_s == 365 * 24 * 3600
    token, expires = issue_token(SECRET, 3600, now=1_000_000)
    claims = verify_token(SECRET, token, now=1_000_001)
    assert claims["sub"] == "dashboard" and claims["exp"] == expires == 1_003_600


def test_token_rejections():
    token, _ = issue_token(SECRET, 3600, now=1_000_000)
    with pytest.raises(InvalidToken, match="expired"):
        verify_token(SECRET, token, now=1_003_600)
    with pytest.raises(InvalidToken, match="signature"):
        verify_token("x" * 48, token, now=1_000_001)
    head, claims, sig = token.split(".")
    with pytest.raises(InvalidToken):  # tampered claims keep the old signature
        verify_token(SECRET, f"{head}.{b64({'exp': 9_999_999_999})}.{sig}")
    for junk in ["", "a.b", "a.b.c.d", "....", "!!!.@@@.###"]:
        with pytest.raises(InvalidToken):
            verify_token(SECRET, junk)


def test_alg_none_and_other_algorithms_are_rejected():
    claims = {
        "iss": "agentteam",
        "aud": "agentteam-dashboard",
        "exp": 9_999_999_999,
    }
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
    with pytest.raises(InvalidToken, match="algorithm"):
        verify_token(SECRET, unsigned)
    with pytest.raises(InvalidToken, match="algorithm"):
        verify_token(SECRET, f"{b64({'alg': 'HS512'})}.{b64(claims)}.xx")


def test_wrong_audience_and_missing_expiry_are_rejected():
    import hashlib
    import hmac

    def forge(claims):
        head, body = b64({"alg": "HS256", "typ": "JWT"}), b64(claims)
        sig = hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
        return f"{head}.{body}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"

    good = {"iss": "agentteam", "aud": "agentteam-dashboard", "exp": 9_999_999_999}
    assert verify_token(SECRET, forge(good))["iss"] == "agentteam"
    with pytest.raises(InvalidToken, match="issuer or audience"):
        verify_token(SECRET, forge({**good, "aud": "someone-else"}))
    with pytest.raises(InvalidToken, match="expiry"):
        verify_token(SECRET, forge({k: v for k, v in good.items() if k != "exp"}))
    with pytest.raises(InvalidToken, match="expiry"):
        verify_token(SECRET, forge({**good, "exp": True}))


def test_password_and_header_helpers():
    assert password_matches(PASSWORD, PASSWORD)
    assert not password_matches(PASSWORD + "x", PASSWORD) and not password_matches("", PASSWORD)
    assert bearer_token("Bearer abc") == "abc" and bearer_token("bearer abc") == "abc"
    assert bearer_token("Basic abc") is None and bearer_token("Bearer ") is None
    assert bearer_token(None) is None


def test_rate_limiter_window():
    rl = RateLimiter(limit=2, window_s=10)
    assert rl.retry_after("k", now=0) == 0
    rl.hit("k", now=0)
    rl.hit("k", now=1)
    assert rl.retry_after("k", now=2) == pytest.approx(8)  # oldest hit leaves at t=10
    assert rl.retry_after("other", now=2) == 0
    assert rl.retry_after("k", now=10.5) == 0  # window has moved on
    rl.reset("k")
    assert rl.retry_after("k", now=2) == 0


# --- the API ---------------------------------------------------------------------------


@pytest.fixture
def open_app(settings):
    return create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})


@pytest.fixture
def secured(settings):
    return create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": settings.database_path,
            "API_PASSWORD": PASSWORD,
            "JWT_SECRET": SECRET,
            "JWT_TTL_S": 3600,
        }
    )


def login(client, password=PASSWORD):
    return client.post("/api/login", json={"password": password})


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_without_a_password_the_api_stays_open_for_local_use(open_app):
    c = open_app.test_client()
    assert c.get("/api/runs").status_code == 200
    assert c.get("/api/auth/status").get_json() == {"auth_required": False}
    assert login(c).status_code == 404  # nothing to log in to


def test_protected_routes_need_a_valid_token(secured):
    c = secured.test_client()
    for method, url in [
        ("get", "/api/runs"),
        ("post", "/api/runs"),
        ("get", "/api/runs/abc"),
        ("get", "/api/runs/abc/events"),
        ("post", "/api/runs/abc/approve"),
        ("post", "/api/runs/abc/reject"),
    ]:
        res = getattr(c, method)(url)
        assert res.status_code == 401, url
        assert res.headers["WWW-Authenticate"].startswith("Bearer")
    assert c.get("/api/runs", headers=auth("garbage")).status_code == 401


def test_public_routes_stay_public(secured):
    c = secured.test_client()
    assert c.get("/health").status_code == 200
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/status").get_json() == {"auth_required": True}


def test_login_issues_a_token_that_unlocks_the_api(secured):
    c = secured.test_client()
    res = login(c)
    assert res.status_code == 200
    body = res.get_json()
    assert body["expires_at"] > 0
    assert c.get("/api/runs", headers=auth(body["token"])).status_code == 200
    created = c.post("/api/runs", json={"task": "t"}, headers=auth(body["token"]))
    assert created.status_code == 202


def test_a_token_signed_with_another_secret_or_expired_is_refused(secured):
    c = secured.test_client()
    forged, _ = issue_token("z" * 48, 3600)
    expired, _ = issue_token(SECRET, 3600, now=1)
    assert c.get("/api/runs", headers=auth(forged)).status_code == 401
    assert c.get("/api/runs", headers=auth(expired)).status_code == 401


def test_wrong_password_is_rejected_and_never_echoed(secured):
    res = login(secured.test_client(), "nope")
    assert res.status_code == 401 and "nope" not in res.get_data(as_text=True)
    assert secured.test_client().post("/api/login", json={}).status_code == 401
    assert secured.test_client().post("/api/login", json={"password": 5}).status_code == 401


def test_login_locks_out_after_five_failures_even_for_the_right_password(secured):
    c = secured.test_client()
    for _ in range(5):
        assert login(c, "wrong").status_code == 401
    locked = login(c, PASSWORD)  # correct, but the client is locked out
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    # a different client address is unaffected
    other = secured.test_client()
    assert (
        other.post(
            "/api/login", json={"password": PASSWORD}, environ_base={"REMOTE_ADDR": "9.9.9.9"}
        ).status_code
        == 200
    )


def test_successful_login_clears_earlier_failures(secured):
    c = secured.test_client()
    for _ in range(4):
        login(c, "wrong")
    assert login(c).status_code == 200
    for _ in range(4):
        assert login(c, "wrong").status_code == 401  # counter was reset, not at 8


def test_approve_and_create_are_rate_limited(secured):
    c = secured.test_client()
    h = auth(login(c).get_json()["token"])
    codes = [c.post("/api/runs/nope/approve", headers=h).status_code for _ in range(22)]
    assert codes[:20] == [409] * 20 and codes[20:] == [429, 429]  # 409 = not awaiting approval
    created = [c.post("/api/runs", json={"task": "t"}, headers=h).status_code for _ in range(22)]
    assert created[:20] == [202] * 20 and created[20:] == [429, 429]


# --- startup safety --------------------------------------------------------------------


def test_password_without_a_strong_secret_refuses_to_start(settings):
    base = {"TESTING": True, "DATABASE_PATH": settings.database_path, "API_PASSWORD": PASSWORD}
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(base)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app({**base, "JWT_SECRET": "short"})


def test_auth_required_refuses_to_start_open(settings):
    with pytest.raises(RuntimeError, match="refusing to start open"):
        create_app(
            {"TESTING": True, "DATABASE_PATH": settings.database_path, "AUTH_REQUIRED": True}
        )


# --- CORS ------------------------------------------------------------------------------

ORIGIN = "https://dashboard.example.com"


@pytest.fixture
def cors_app(settings):
    return create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": settings.database_path,
            "API_PASSWORD": PASSWORD,
            "JWT_SECRET": SECRET,
            "CORS_ORIGINS": (ORIGIN,),
        }
    )


def test_preflight_from_an_allowed_origin_succeeds_without_a_token(cors_app):
    res = cors_app.test_client().options(
        "/api/runs",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert res.status_code == 204
    assert res.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Authorization" in res.headers["Access-Control-Allow-Headers"]
    assert "Last-Event-ID" in res.headers["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Credentials" not in res.headers  # bearer tokens, not cookies


def test_401s_carry_cors_headers_so_the_browser_can_read_them(cors_app):
    res = cors_app.test_client().get("/api/runs", headers={"Origin": ORIGIN})
    assert res.status_code == 401 and res.headers["Access-Control-Allow-Origin"] == ORIGIN


def test_other_origins_get_no_cors_headers(cors_app):
    c = cors_app.test_client()
    res = c.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in res.headers
    pre = c.options("/api/runs", headers={"Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in pre.headers
    assert "Access-Control-Allow-Origin" not in c.get("/api/health").headers


def test_streaming_endpoint_accepts_the_token_header_for_fetch_based_sse(secured, settings):
    from agentteam.tracing import set_tracer

    tracer = secured.extensions["tracer"]
    set_tracer(tracer)
    run_id = tracer.create_run("t")
    tracer.set_run_status(run_id, "done")
    c = secured.test_client()
    h = auth(login(c).get_json()["token"])
    res = c.get(f"/api/runs/{run_id}/events", headers={**h, "Last-Event-ID": "0"})
    assert res.status_code == 200 and res.mimetype == "text/event-stream"
    assert "event: end" in res.get_data(as_text=True)
