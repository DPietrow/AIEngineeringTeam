from agentteam.redact import REDACTED, redact, register_secret


def test_key_based_redaction_keeps_token_counts():
    out = redact({"api_key": "abc", "input_tokens": 12, "nested": {"Authorization": "x"}})
    assert out["api_key"] == REDACTED
    assert out["input_tokens"] == 12
    assert out["nested"]["Authorization"] == REDACTED


def test_value_patterns():
    text = (
        "key sk-ant-api03-abcdefghijklmnop and ghp_abcdefghijklmnopqrstuv "
        "and AKIAABCDEFGHIJKLMNOP and Bearer abcdefghijklmnop"
    )
    out = redact(text)
    for leaked in ("sk-ant-api03", "ghp_abcdef", "AKIAABCDEF", "Bearer abcdef"):
        assert leaked not in out


def test_assignment_in_text_keeps_key_hides_value():
    out = redact("password=hunter2hunter2 and max_tokens: 100")
    assert "hunter2" not in out
    assert "password=" in out
    assert "max_tokens: 100" in out


def test_registered_literal_secret():
    register_secret("my-very-special-literal")
    assert "special-literal" not in redact("value is my-very-special-literal here")
