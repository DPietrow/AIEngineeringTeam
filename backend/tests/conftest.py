import pytest

from agentteam.budget import SpendGuard
from agentteam.tracing import Tracer, set_tracer


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def tracer(db_path):
    t = Tracer(db_path, guard=SpendGuard(db_path, run_cap_usd=0.05, global_cap_usd=1.00))
    set_tracer(t)
    return t
