import copy
import sqlite3
from types import SimpleNamespace

import pytest

from agentteam.app import create_app
from agentteam.config import Settings
from agentteam.db import connect, init_db
from agentteam.evals.store import run_metrics
from agentteam.llm import AnthropicLLM, FakeLLM, with_cache_breakpoint
from agentteam.pricing import compute_cost
from agentteam.prompts import config_hash
from agentteam.tracing import set_tracer

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5-5"


@pytest.fixture
def app(settings):
    return create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})


@pytest.fixture
def tracer(app):
    t = app.extensions["tracer"]
    set_tracer(t)
    return t


# --- pricing ----------------------------------------------------------------


def test_cost_without_caching_is_unchanged():
    assert compute_cost(HAIKU, 1000, 500) == pytest.approx((1000 * 1 + 500 * 5) / 1e6)


def test_cache_writes_cost_more_and_reads_much_less_than_uncached_input():
    # 1000 uncached in, 500 out, 2000 written to cache, 10000 read from cache (Haiku 4.5)
    cost = compute_cost(HAIKU, 1000, 500, cache_write_tokens=2000, cache_read_tokens=10000)
    assert cost == pytest.approx((1000 * 1 + 500 * 5 + 2000 * 1.25 + 10000 * 0.1) / 1e6)
    # The point of caching: re-reading 10k tokens costs a tenth of sending them fresh.
    assert compute_cost(HAIKU, 0, 0, cache_read_tokens=10_000) == pytest.approx(
        compute_cost(HAIKU, 10_000, 0) / 10
    )


def test_models_have_their_own_cache_read_discount():
    read = {
        m: compute_cost(m, 0, 0, cache_read_tokens=1_000_000)
        for m in (HAIKU, SONNET, "claude-opus-5-5", "claude-fable-5-1")
    }
    assert read[HAIKU] == pytest.approx(0.10)
    assert read[SONNET] == pytest.approx(0.20)
    assert read["claude-opus-5-5"] == pytest.approx(0.20)  # 0.05x of $4
    assert read["claude-fable-5-1"] == pytest.approx(0.25)  # 0.025x of $10


def test_unknown_models_fall_back_to_a_high_price():
    assert compute_cost("mystery-model", 1000, 0) > compute_cost(SONNET, 1000, 0)


# --- cache breakpoint --------------------------------------------------------


def test_breakpoint_marks_only_the_last_block_of_the_last_message():
    msgs = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "x"},
                {"type": "tool_result", "tool_use_id": "2", "content": "y"},
            ],
        },
    ]
    original = copy.deepcopy(msgs)
    out = with_cache_breakpoint(msgs)
    assert msgs == original  # the stored history is never mutated
    assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in out[-1]["content"][0]
    assert sum("cache_control" in str(m) for m in out) == 1


def test_breakpoint_wraps_a_plain_string_message():
    out = with_cache_breakpoint([{"role": "user", "content": "hello"}])
    assert out == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}],
        }
    ]


def test_breakpoint_tolerates_empty_input():
    assert with_cache_breakpoint([]) == []
    msgs = [{"role": "user", "content": []}]
    assert with_cache_breakpoint(msgs) == msgs


# --- AnthropicLLM: wiring, usage, cost -----------------------------------------


def fake_client(usage=None):
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=usage
            or SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=900,
                cache_creation_input_tokens=50,
            ),
            stop_reason="end_turn",
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


MSGS = [{"role": "user", "content": "do the thing"}]


def converse(llm, tracer, name="architect.step1", messages=None):
    run_id = tracer.create_run("t")
    with tracer.run(run_id):
        llm.converse(name=name, system="s", messages=messages or list(MSGS), tools=[])
    return run_id


def span_row(app, run_id):
    conn = connect(app.config["DATABASE_PATH"])
    try:
        return dict(conn.execute("SELECT * FROM spans WHERE run_id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


def test_caching_sends_a_breakpoint_but_traces_clean_messages(app, tracer):
    client, calls = fake_client()
    llm = AnthropicLLM(tracer, client=client, model=HAIKU)
    run_id = converse(llm, tracer)
    sent = calls[0]["messages"][-1]["content"][-1]
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert MSGS == [{"role": "user", "content": "do the thing"}]  # caller's list untouched
    assert "cache_control" not in str(span_row(app, run_id)["input"])  # trace stays readable


def test_caching_can_be_turned_off(app, tracer):
    client, calls = fake_client()
    converse(AnthropicLLM(tracer, client=client, model=HAIKU, prompt_caching=False), tracer)
    assert "cache_control" not in str(calls[0]["messages"])


def test_cache_usage_is_recorded_and_priced(app, tracer):
    client, _ = fake_client()
    run_id = converse(AnthropicLLM(tracer, client=client, model=HAIKU), tracer)
    row = span_row(app, run_id)
    assert (row["input_tokens"], row["output_tokens"]) == (100, 20)
    assert (row["cache_read_tokens"], row["cache_write_tokens"]) == (900, 50)
    assert row["cost_usd"] == pytest.approx(compute_cost(HAIKU, 100, 20, 50, 900))


def test_responses_without_cache_fields_count_as_zero(app, tracer):
    client, _ = fake_client(usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    run_id = converse(AnthropicLLM(tracer, client=client, model=HAIKU), tracer)
    row = span_row(app, run_id)
    assert (row["cache_read_tokens"], row["cache_write_tokens"]) == (0, 0)
    assert row["cost_usd"] == pytest.approx(compute_cost(HAIKU, 10, 5))


# --- per-agent models ----------------------------------------------------------


def test_agent_override_picks_the_model_and_prices_each_call_by_its_own_model(app, tracer):
    client, calls = fake_client(usage=SimpleNamespace(input_tokens=1000, output_tokens=100))
    llm = AnthropicLLM(tracer, client=client, model=HAIKU, model_overrides={"review": SONNET})
    run_id = tracer.create_run("t")
    with tracer.run(run_id):
        llm.converse(name="architect.step1", system="s", messages=list(MSGS), tools=[])
        llm.converse(name="review.step3", system="s", messages=list(MSGS), tools=[])
    assert [c["model"] for c in calls] == [HAIKU, SONNET]

    conn = connect(app.config["DATABASE_PATH"])
    try:
        rows = conn.execute(
            "SELECT name, model, cost_usd FROM spans WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [(r["name"], r["model"]) for r in rows] == [
        ("llm.architect.step1", HAIKU),
        ("llm.review.step3", SONNET),
    ]
    assert rows[0]["cost_usd"] == pytest.approx(compute_cost(HAIKU, 1000, 100))
    assert rows[1]["cost_usd"] == pytest.approx(compute_cost(SONNET, 1000, 100))
    assert llm.model_label == f"{HAIKU} (review={SONNET})"


def test_fake_llm_keeps_a_single_model():
    llm = FakeLLM(tracer=None)
    assert llm.model_for("review.step1") == "fake" and llm.model_label == "fake"


def test_settings_read_agent_models_and_caching_from_the_environment(monkeypatch):
    for k in ("MODEL_REVIEW", "MODEL_ARCHITECT", "PROMPT_CACHING"):
        monkeypatch.delenv(k, raising=False)
    plain = Settings.from_env()
    assert plain.model_overrides == () and plain.prompt_caching is True
    assert plain.model_signature == plain.llm_model  # no overrides: hashes do not change

    monkeypatch.setenv("MODEL_REVIEW", SONNET)
    monkeypatch.setenv("PROMPT_CACHING", "0")
    tuned = Settings.from_env()
    assert tuned.model_overrides == (("review", SONNET),)
    assert tuned.model_for("review") == SONNET and tuned.model_for("architect") == tuned.llm_model
    assert tuned.prompt_caching is False
    assert tuned.model_signature == f"{tuned.llm_model}|review={SONNET}"


def test_a_different_model_setup_gets_a_different_config_hash():
    base = Settings()
    tuned = Settings(model_overrides=(("review", SONNET),))
    assert config_hash(base.model_signature) != config_hash(tuned.model_signature)
    assert config_hash(base.model_signature) == config_hash(base.llm_model)


# --- storage and eval metrics ----------------------------------------------------


def test_run_metrics_report_cache_tokens(app, tracer, settings):
    run_id = tracer.create_run("t")
    with tracer.run(run_id), tracer.span("llm.x", kind="llm") as sp:
        sp.set_usage(
            model=HAIKU,
            input_tokens=100,
            output_tokens=10,
            cache_read_tokens=900,
            cache_write_tokens=50,
        )
    m = run_metrics(settings.database_path, run_id)
    assert (m["tokens_in"], m["cache_read_tokens"], m["cache_write_tokens"]) == (100, 900, 50)


def test_existing_databases_gain_the_cache_columns(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE spans (id TEXT PRIMARY KEY, run_id TEXT, parent_id TEXT, name TEXT, "
        "kind TEXT, status TEXT, started_at TEXT, cost_usd REAL NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()
    init_db(path)
    conn = connect(path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(spans)")}
    finally:
        conn.close()
    assert {"cache_read_tokens", "cache_write_tokens"} <= cols
