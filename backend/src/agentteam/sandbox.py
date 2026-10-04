"""Sandboxed command execution.

Docker mode (the default and the only safe one): each command runs in a fresh container with
no network, a read-only root filesystem, the workspace mounted READ-ONLY, all capabilities
dropped, a non-root user, and CPU / memory / pid / wall-clock limits.

Local mode runs the command directly on the host. It exists only so tests and quick local
experiments work without Docker; it provides NO isolation.
"""

import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT_CHARS = 20_000
DEFAULT_IMAGE = "agentteam-sandbox:py312"


@dataclass(frozen=True)
class SandboxConfig:
    image: str = DEFAULT_IMAGE
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 128
    timeout_s: int = 120
    mode: str = "docker"  # docker | local


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return text[:half] + f"\n...[{len(text) - MAX_OUTPUT_CHARS} chars omitted]...\n" + text[-half:]


def build_docker_command(
    workspace: Path, argv: list[str], config: SandboxConfig, name: str
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        config.memory,
        "--memory-swap",
        config.memory,
        "--cpus",
        config.cpus,
        "--pids-limit",
        str(config.pids_limit),
        "--user",
        "10001:10001",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        # The workspace is read-only, so tools must not try to write caches into it.
        "--env",
        "PYTEST_ADDOPTS=-p no:cacheprovider",
        "--env",
        "RUFF_NO_CACHE=true",
        "--mount",
        f"type=bind,source={workspace},target=/workspace,readonly",
        "--workdir",
        "/workspace",
        config.image,
        *argv,
    ]


def _local_argv(argv: list[str]) -> list[str]:
    """Map allowlisted tool names onto this interpreter's environment (local mode only)."""
    if argv and argv[0] in ("pytest", "ruff"):
        return [sys.executable, "-m", argv[0], *argv[1:]]
    return argv


def run_in_sandbox(workspace: Path, argv: list[str], config: SandboxConfig) -> SandboxResult:
    started = time.perf_counter()
    if config.mode == "local":
        cmd, name = _local_argv(argv), None
    elif config.mode == "docker":
        name = f"agentteam-{uuid.uuid4().hex[:12]}"
        cmd = build_docker_command(workspace.resolve(), argv, config, name)
    else:
        raise ValueError(f"unknown sandbox mode: {config.mode!r}")

    try:
        proc = subprocess.run(
            cmd,
            cwd=workspace if config.mode == "local" else None,
            # The Terminal MCP server speaks JSON-RPC over its own stdin/stdout. A child that
            # inherited that stdin could read (and corrupt) the protocol stream.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        if name:  # killing the docker CLI does not stop the container; remove it explicitly
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        return SandboxResult(
            exit_code=124,
            stdout=_clip(_decode(exc.stdout)),
            stderr=_clip(_decode(exc.stderr)) + f"\nTimed out after {config.timeout_s}s",
            duration_s=time.perf_counter() - started,
            timed_out=True,
        )
    return SandboxResult(
        exit_code=proc.returncode,
        stdout=_clip(proc.stdout),
        stderr=_clip(proc.stderr),
        duration_s=time.perf_counter() - started,
    )


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def main() -> None:
    """Manual smoke test:  python -m agentteam.sandbox <workspace> -- pytest -q"""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--mode", default="docker")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("command", nargs="+")
    args = parser.parse_args()
    cfg = SandboxConfig(image=args.image, timeout_s=args.timeout, mode=args.mode)
    result = run_in_sandbox(args.workspace, args.command, cfg)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    print(
        f"[exit={result.exit_code} duration={result.duration_s:.1f}s timed_out={result.timed_out}]"
    )
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
