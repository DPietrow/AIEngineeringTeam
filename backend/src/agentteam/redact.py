"""Redaction applied to every payload BEFORE it is written to SQLite."""

import re
from typing import Any

REDACTED = "[REDACTED]"

# Dict keys whose values are always redacted. "token" only matches as a whole key
# or suffix so counters like input_tokens / max_tokens are left alone.
_SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|authorization|credential|private[_-]?key"
    r"|set-cookie|^cookie$|access[_-]?token|refresh[_-]?token|(^|[_-])token$|bearer)",
    re.IGNORECASE,
)

_VALUE_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=\-]{10,}"),
]

# key=value / key: value inside free text; keeps the key, redacts the value.
_ASSIGNMENT = re.compile(
    r"(\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[=:]\s*['\"]?)[^\s'\",;&]{4,}",
    re.IGNORECASE,
)

_literal_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register an exact secret (e.g. the API key) to be scrubbed wherever it appears."""
    if value and len(value) >= 8:
        _literal_secrets.add(value)


def redact_text(text: str) -> str:
    for secret in _literal_secrets:
        text = text.replace(secret, REDACTED)
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _ASSIGNMENT.sub(lambda m: m.group(1) + REDACTED, text)


def redact(obj: Any) -> Any:
    """Return a redacted copy of a JSON-compatible structure."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {
            k: REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else redact(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list | tuple):
        return [redact(v) for v in obj]
    return obj
