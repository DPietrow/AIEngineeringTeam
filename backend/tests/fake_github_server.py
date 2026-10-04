"""Stand-in for the official GitHub MCP server, for offline tests.

Exposes create_pull_request (returns a GitHub-shaped JSON result) plus a dangerous tool that the
Delivery agent's policy must never surface. Calls are appended to the file given as `--log <path>`.
"""

import json
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-github")
LOG_PATH: str | None = None


def _log(name: str, args: dict) -> None:
    # MCP subprocesses get a scrubbed environment, so the log path arrives as an argument.
    path = LOG_PATH
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": name, "args": args}) + "\n")


@mcp.tool()
def create_pull_request(
    owner: str, repo: str, title: str, head: str, base: str, body: str = "", draft: bool = False
) -> str:
    _log("create_pull_request", dict(owner=owner, repo=repo, title=title, head=head, base=base))
    return json.dumps(
        {"number": 7, "html_url": f"https://github.com/{owner}/{repo}/pull/7", "title": title}
    )


@mcp.tool()
def delete_repository(owner: str, repo: str) -> str:
    _log("delete_repository", dict(owner=owner, repo=repo))
    return "deleted"


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--log":
        LOG_PATH = sys.argv[2]
    mcp.run()
