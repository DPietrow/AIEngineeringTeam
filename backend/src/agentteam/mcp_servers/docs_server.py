"""Documentation MCP server: read-only search over a repo's markdown/text docs.

Run:  python -m agentteam.mcp_servers.docs_server --root <repo>
Every tool is annotated readOnlyHint=true, and paths cannot escape --root.
"""

import argparse
from collections.abc import Iterator
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

DOC_SUFFIXES = {".md", ".rst", ".txt"}
CODE_SUFFIXES = {".py", ".toml", ".yml", ".yaml", ".json", ".js", ".ts", ".tsx", ".cfg", ".ini"}
SKIP_DIRS = {"node_modules", "__pycache__", "dist", "build", "venv"}
MAX_DOC_CHARS = 100_000


def build_server(root: Path) -> FastMCP:
    root = root.resolve()
    mcp = FastMCP("docs")
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    def iter_files(suffixes: set[str]) -> Iterator[Path]:
        for path in sorted(root.rglob("*")):
            parts = path.relative_to(root).parts
            hidden = any(part.startswith(".") for part in parts[:-1])  # .git, .pytest_cache, ...
            if (
                path.is_file()
                and path.suffix.lower() in suffixes
                and not hidden
                and not SKIP_DIRS.intersection(parts)
            ):
                yield path

    def iter_docs() -> Iterator[Path]:
        return iter_files(DOC_SUFFIXES)

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    def grep(paths: Iterator[Path], query: str, max_results: int) -> list[dict]:
        needle = query.lower()
        hits: list[dict] = []
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    hits.append({"path": rel(path), "line": number, "text": line.strip()[:300]})
                    if len(hits) >= max_results:
                        return hits
        return hits

    @mcp.tool(annotations=read_only)
    def list_docs() -> list[str]:
        """List documentation files (relative paths) available in the repository."""
        return [rel(p) for p in iter_docs()]

    @mcp.tool(annotations=read_only)
    def search_docs(query: str, max_results: int = 10) -> list[dict]:
        """Case-insensitive text search across docs. Returns path, line number and line text."""
        return grep(iter_docs(), query, max_results)

    @mcp.tool(annotations=read_only)
    def search_code(query: str, max_results: int = 10) -> list[dict]:
        """Case-insensitive search across source and config files. Returns matching lines only
        (path, line number, text); use it to locate where code lives, not to read whole files."""
        return grep(iter_files(CODE_SUFFIXES), query, max_results)

    @mcp.tool(annotations=read_only)
    def read_doc(path: str) -> str:
        """Read one documentation file by its relative path (as returned by list_docs)."""
        target = (root / path).resolve()
        if root not in target.parents or target.suffix.lower() not in DOC_SUFFIXES:
            raise ValueError(f"not a readable doc inside the repository: {path}")
        if not target.is_file():
            raise ValueError(f"no such doc: {path}")
        return target.read_text(encoding="utf-8", errors="replace")[:MAX_DOC_CHARS]

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    build_server(args.root).run()


if __name__ == "__main__":
    main()
