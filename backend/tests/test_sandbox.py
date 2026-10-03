import subprocess
import sys

import pytest

from agentteam import sandbox
from agentteam.mcp_servers.terminal_server import CommandRejected, parse_command
from agentteam.sandbox import SandboxConfig, build_docker_command, run_in_sandbox


def test_docker_command_is_locked_down(tmp_path):
    cfg = SandboxConfig(image="img:1", memory="256m", cpus="0.5", pids_limit=64, timeout_s=10)
    cmd = build_docker_command(tmp_path, ["pytest", "-q"], cfg, "agentteam-abc")
    joined = " ".join(cmd)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--memory 256m",
        "--memory-swap 256m",
        "--cpus 0.5",
        "--pids-limit 64",
        "--user 10001:10001",
        "--rm",
    ):
        assert flag in joined
    mount = cmd[cmd.index("--mount") + 1]
    assert mount.endswith("target=/workspace,readonly")  # workspace is read-only
    assert cmd[-3:] == ["img:1", "pytest", "-q"]


def test_timeout_kills_the_container(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], output=b"partial", stderr=b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = run_in_sandbox(tmp_path, ["pytest"], SandboxConfig(timeout_s=1))

    assert result.timed_out and result.exit_code == 124
    assert "partial" in result.stdout
    removal = calls[-1]
    assert removal[:3] == ["docker", "rm", "-f"] and removal[3].startswith("agentteam-")


def test_unknown_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        run_in_sandbox(tmp_path, ["pytest"], SandboxConfig(mode="vm"))


def test_local_mode_runs_and_times_out(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    ok = run_in_sandbox(
        tmp_path, ["pytest", "-q", "-p", "no:cacheprovider"], SandboxConfig(mode="local")
    )
    assert ok.exit_code == 0 and "1 passed" in ok.stdout

    slow = run_in_sandbox(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SandboxConfig(mode="local", timeout_s=1),
    )
    assert slow.timed_out and slow.exit_code == 124


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "pytest -q tests/test_x.py -x",
        "pytest -q -k 'not slow'",
        "ruff check .",
        "ruff check src --select E",
        "ruff format --check .",
        "ruff format --diff src",
    ],
)
def test_allowed_commands(command):
    assert parse_command(command)[0] in ("pytest", "ruff")


@pytest.mark.parametrize(
    "command",
    [
        "",
        "ls -la",
        "rm -rf /",
        "python -c 'print(1)'",
        "curl http://example.com",
        "pytest; rm -rf /",  # no shell: "pytest;" is not an allowed program
        "ruff format .",  # would modify files
        "ruff check . --fix",
        "ruff check --unsafe-fixes .",
        "ruff",
        "pytest /etc/passwd",
        "pytest ../other",
        "pytest --rootdir=/",
        "pytest -c /tmp/evil.ini",
        "pytest -p evil_plugin",
        "pytest ~/secrets",
        "x" * 600,
        "pytest 'unterminated",
    ],
)
def test_rejected_commands(command):
    with pytest.raises(CommandRejected):
        parse_command(command)
