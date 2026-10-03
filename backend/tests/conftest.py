import shutil
import subprocess

import pytest

from agentteam.budget import SpendGuard
from agentteam.config import Settings
from agentteam.tracing import Tracer, set_tracer

needs_npx = pytest.mark.skipif(shutil.which("npx") is None, reason="npx (Node) not available")


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def tracer(db_path):
    t = Tracer(db_path, guard=SpendGuard(db_path, run_cap_usd=0.05, global_cap_usd=1.00))
    set_tracer(t)
    return t


@pytest.fixture
def toy_repo(tmp_path):
    repo = tmp_path / "toy"
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("# Toy\nA tiny Flask app.\n")
    (repo / "docs" / "guide.md").write_text("# Guide\nRoutes live in app.py. Run pytest to test.\n")
    (repo / "app.py").write_text("print('hi')\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    git(repo, "init", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "init")
    return repo


@pytest.fixture
def settings(tmp_path, toy_repo):
    return Settings(
        database_path=str(tmp_path / "app.db"),
        toy_repo_path=str(toy_repo),
        workspaces_dir=str(tmp_path / "workspaces"),
        sandbox_mode="local",  # no Docker in tests
        sandbox_timeout_s=60,
    )
