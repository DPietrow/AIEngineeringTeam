"""Running more than one worker against the same database.

Claims, recovery and delivery hand-off must stay exclusive when workers race (threads here, and
real OS processes for the claim), and a worker that stalls past its lease must stop and write
nothing once another worker has taken its run over (fencing).
"""

import subprocess
import sys
import threading

import pytest

from agentteam.db import connect
from agentteam.deadline import Fence, LeaseLost, check_deadline, lease_fence
from agentteam.llm import FakeLLM
from agentteam.orchestrator import Orchestrator
from agentteam.tracing import Tracer, set_tracer
from agentteam.worker import Heartbeat

N_THREADS = 4


@pytest.fixture
def tr(settings):
    t = Tracer(settings.database_path)
    set_tracer(t)
    return t


def race(fn, n=N_THREADS):
    """Run fn(i) in n threads released together; returns the list of results."""
    start, results, errors = threading.Barrier(n), [None] * n, []

    def go(i):
        try:
            start.wait()
            results[i] = fn(i)
        except BaseException as exc:  # surfaced below so a failure is not silently lost
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    return results


def row(settings, run_id):
    conn = connect(settings.database_path)
    try:
        return dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


def event_types(settings, run_id):
    conn = connect(settings.database_path)
    try:
        rows = conn.execute("SELECT type, data FROM events WHERE run_id = ?", (run_id,)).fetchall()
    finally:
        conn.close()
    return [(r["type"], r["data"]) for r in rows]


# --- exclusive claims ------------------------------------------------------------------


def drain(tr, claim, worker):
    got = []
    while (item := claim(worker, 60.0)) is not None:
        got.append(item[0] if isinstance(item, tuple) else item)
    return got


def test_concurrent_workers_never_claim_the_same_run(tr):
    ids = [tr.create_run(f"task {i}") for i in range(16)]
    results = race(lambda i: drain(tr, tr.claim_next_run, f"w{i}"))
    claimed = [rid for r in results for rid in r]
    assert sorted(claimed) == sorted(ids)  # every run claimed, none twice, none lost
    assert len(claimed) == len(set(claimed))


def test_claims_are_exclusive_across_real_processes(settings):
    tr = Tracer(settings.database_path)
    ids = [tr.create_run(f"task {i}") for i in range(20)]
    script = (
        "import sys\n"
        "from agentteam.tracing import Tracer\n"
        "t = Tracer(sys.argv[1])\n"
        "ids = []\n"
        "while (c := t.claim_next_run('proc-' + sys.argv[2], 60)) is not None:\n"
        "    ids.append(c[0])\n"
        "print(','.join(ids))\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, settings.database_path, str(i)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(3)
    ]
    claimed: list[str] = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        claimed += [x for x in out.strip().split(",") if x]
    assert sorted(claimed) == sorted(ids)


def test_delivery_claims_are_exclusive(tr):
    ids = [tr.create_run(f"task {i}") for i in range(10)]
    for rid in ids:
        tr.set_run_status(rid, "approved")
    results = race(lambda i: drain(tr, tr.claim_next_delivery, f"w{i}"))
    claimed = [rid for r in results for rid in r]
    assert sorted(claimed) == sorted(ids) and len(claimed) == len(set(claimed))


# --- recovery --------------------------------------------------------------------------


def test_concurrent_recovery_sweeps_recover_each_run_exactly_once(tr, settings):
    ids = [tr.create_run(f"task {i}") for i in range(6)]
    for _ in ids:
        tr.claim_next_run("dead-worker", -1.0)  # lease already expired
    results = race(lambda i: tr.recover_orphans())
    recovered = [r["run_id"] for batch in results for r in batch]
    assert sorted(recovered) == sorted(ids)
    for rid in ids:
        assert row(settings, rid)["status"] == "error"
        recovered_events = [t for t, _ in event_types(settings, rid) if t == "run.recovered"]
        assert len(recovered_events) == 1


def test_a_live_workers_run_is_not_recovered_by_another_workers_sweep(tr, settings):
    rid = tr.create_run("t")
    tr.claim_next_run("alive", 60.0)
    assert tr.recover_orphans() == []
    assert row(settings, rid)["status"] == "running"


# --- fencing: a worker that lost its lease must not write -------------------------------


def test_stale_worker_cannot_overwrite_a_recovered_run(tr, settings):
    rid = tr.create_run("t")
    tr.claim_next_run("A", -1.0)
    assert tr.recover_orphans()  # another worker's sweep: running -> error
    fence = Fence("A")
    with lease_fence(fence), pytest.raises(LeaseLost):
        tr.set_run_status(rid, "done")
    assert row(settings, rid)["status"] == "error"  # recovery's verdict stands
    assert fence.lost.is_set()
    assert not [d for t, d in event_types(settings, rid) if t == "run.status" and '"done"' in d]


def test_stale_worker_cannot_write_after_the_run_was_reassigned(tr, settings):
    rid = tr.create_run("t")
    tr.set_run_status(rid, "approved")
    tr.claim_next_delivery("A", -1.0)  # A starts delivering, then stalls
    assert tr.recover_orphans()[0]["now"] == "approved"  # requeued for delivery
    assert tr.claim_next_delivery("B", 60.0) == rid  # B takes it
    with lease_fence(Fence("A")), pytest.raises(LeaseLost):
        tr.set_run_status(rid, "done")
    assert row(settings, rid)["status"] == "delivering" and row(settings, rid)["claimed_by"] == "B"
    with lease_fence(Fence("B")):
        tr.set_run_status(rid, "done")  # the real owner can still finish
    assert row(settings, rid)["status"] == "done"


def test_the_owner_can_write_normally_and_unleased_code_is_unaffected(tr, settings):
    rid = tr.create_run("t")
    tr.claim_next_run("A", 60.0)
    with lease_fence(Fence("A")):
        tr.set_run_status(rid, "awaiting_approval")
    assert row(settings, rid)["status"] == "awaiting_approval"
    other = tr.create_run("t2")
    tr.set_run_status(other, "running")  # tests and the eval harness have no fence
    assert row(settings, other)["status"] == "running"


def test_heartbeat_notices_a_lost_lease_quickly(tr, settings):
    rid = tr.create_run("t")
    tr.claim_next_run("A", 0.6)
    with Heartbeat(tr, rid, "A", 0.6) as hb:
        assert not hb.fence.lost.is_set()
        conn = connect(settings.database_path)
        conn.execute("UPDATE runs SET claimed_by = 'B' WHERE id = ?", (rid,))
        conn.close()
        assert hb.fence.lost.wait(timeout=5)  # next beat (every 0.2 s) fails to renew
        with lease_fence(hb.fence), pytest.raises(LeaseLost):
            check_deadline()


def test_a_run_is_abandoned_without_a_trace_when_its_worker_loses_the_lease(tr, settings):
    """End to end: the pipeline stops at the next safe point and never touches the run again."""
    rid = tr.create_run("t")
    tr.claim_next_run("A", 60.0)
    fence = Fence("A")
    inner, calls = FakeLLM(tr), []

    def script(name, system, messages, tools):
        calls.append(name)
        if len(calls) == 1:
            fence.lost.set()  # what the heartbeat does the moment a renewal fails
        return inner._converse(name, system, messages, tools)

    orch = Orchestrator(tr, FakeLLM(tr, script=script), settings)
    with lease_fence(fence):
        orch.execute(rid, "task")  # must return quietly, not raise
    assert len(calls) == 1  # stopped before the next model call
    assert row(settings, rid)["status"] == "running"  # nothing was written for it
    statuses = [d for t, d in event_types(settings, rid) if t == "run.status"]
    assert len(statuses) == 1  # only the original claim ("running")


def test_a_worker_that_never_noticed_still_cannot_finish_a_run_it_lost(tr, settings):
    """The window before the first missed heartbeat: the final status write is fenced in SQL."""
    rid = tr.create_run("t")
    tr.claim_next_run("A", 60.0)
    inner = FakeLLM(tr)

    def script(name, system, messages, tools):
        conn = connect(settings.database_path)  # recovery + another worker take the run
        conn.execute("UPDATE runs SET claimed_by = 'B' WHERE id = ?", (rid,))
        conn.close()
        return inner._converse(name, system, messages, tools)

    orch = Orchestrator(tr, FakeLLM(tr, script=script), settings)
    with lease_fence(Fence("A")):
        orch.execute(rid, "task")  # runs to the end, then the final write is refused
    final = row(settings, rid)
    assert final["status"] == "running" and final["claimed_by"] == "B"
