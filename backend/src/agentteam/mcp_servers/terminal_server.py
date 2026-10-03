"""Terminal MCP server: run allowlisted commands in the sandbox. No shell.

Run:  python -m agentteam.mcp_servers.terminal_server --workspace <dir> [--mode docker|local]

The model can only run `pytest ...` and `ruff check ...` / `ruff format --check|--diff ...`.
Commands are parsed into an argv (no shell, so no pipes, redirects or chaining) and arguments
that could reach outside the workspace are rejected. The workspace is mounted read-only in
the container, so even a bad command cannot change the code under test.
"""

import argparse
import shlex
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..sandbox import DEFAULT_IMAGE, SandboxConfig, run_in_sandbox

MAX_COMMAND_CHARS = 500


class CommandRejected(ValueError):
    pass


def parse_command(command: str) -> list[str]:
    """Validate a model-supplied command and return its argv, or raise CommandRejected."""
    if len(command) > MAX_COMMAND_CHARS:
        raise CommandRejected("command too long")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise CommandRejected(f"could not parse command: {exc}") from exc
    if not argv:
        raise CommandRejected("empty command")

    tool, rest = argv[0], argv[1:]
    if tool == "pytest":
        pass
    elif tool == "ruff":
        if not (rest[:1] == ["check"] or rest[:2] in (["format", "--check"], ["format", "--diff"])):
            raise CommandRejected("only 'ruff check' and 'ruff format --check|--diff' are allowed")
        if any(a in ("--fix", "--unsafe-fixes", "--add-noqa") for a in rest):
            raise CommandRejected("ruff may not modify files")
    else:
        raise CommandRejected(f"command not allowed: {tool!r} (allowed: pytest, ruff)")

    for arg in rest:
        path_like = arg.split("=", 1)[-1] if arg.startswith("-") else arg
        if path_like.startswith(("/", "\\", "~")) or ".." in path_like.replace("\\", "/").split(
            "/"
        ):
            raise CommandRejected(f"argument escapes the workspace: {arg!r}")
        if arg in ("-c", "-p", "--rootdir", "--config", "--confcutdir", "--basetemp"):
            raise CommandRejected(f"option not allowed: {arg}")
    return argv


def build_server(workspace: Path, config: SandboxConfig) -> FastMCP:
    mcp = FastMCP("terminal")

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    )
    def run_command(command: str) -> dict:
        """Run `pytest ...`, `ruff check ...` or `ruff format --check ...` against the project in
        a network-isolated sandbox. Returns exit_code, stdout, stderr, duration_s, timed_out.
        Exit code 0 means success. Use relative paths only."""
        argv = parse_command(command)
        result = run_in_sandbox(workspace, argv, config)
        return {
            "command": command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_s": round(result.duration_s, 2),
            "timed_out": result.timed_out,
        }

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--mode", default="docker", choices=["docker", "local"])
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--memory", default="512m")
    parser.add_argument("--cpus", default="1.0")
    parser.add_argument("--pids-limit", type=int, default=128)
    args = parser.parse_args()
    config = SandboxConfig(
        image=args.image,
        memory=args.memory,
        cpus=args.cpus,
        pids_limit=args.pids_limit,
        timeout_s=args.timeout,
        mode=args.mode,
    )
    build_server(args.workspace.resolve(), config).run()


if __name__ == "__main__":
    main()
