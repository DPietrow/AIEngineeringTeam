import asyncio
import sys

from agentteam.db import connect
from agentteam.mcp_toolbox import (
    ServerSpec,
    Toolbox,
    coerce_args,
    forbid_git_paths,
    only,
    read_only,
)
from agentteam.workspace import collect_patch, create_workspace, remove_workspace

from .conftest import needs_npx


def docs_spec(repo):
    return ServerSpec(
        name="docs",
        command=sys.executable,
        args=["-m", "agentteam.mcp_servers.docs_server", "--root", str(repo)],
        policy=read_only,
    )


def span_rows(db_path):
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM spans ORDER BY started_at")]
    finally:
        conn.close()


def test_coerce_args_decodes_stringified_arrays_and_objects():
    schema = {
        "properties": {
            "edits": {"type": "array"},
            "opts": {"type": "object"},
            "path": {"type": "string"},
        }
    }
    out = coerce_args(
        schema, {"edits": '[{"a": 1}]', "opts": '{"b": 2}', "path": '["not", "touched"]'}
    )
    assert out == {"edits": [{"a": 1}], "opts": {"b": 2}, "path": '["not", "touched"]'}
    # Invalid JSON, or JSON of the wrong shape, is left alone for the server to reject.
    assert coerce_args(schema, {"edits": "[oops"}) == {"edits": "[oops"}
    assert coerce_args(schema, {"edits": '{"a": 1}'}) == {"edits": '{"a": 1}'}


def test_docs_server_skips_hidden_dirs_and_searches_code(tracer, toy_repo):
    (toy_repo / ".pytest_cache").mkdir()
    (toy_repo / ".pytest_cache" / "README.md").write_text("cache readme")

    async def main():
        run_id = tracer.create_run("t")
        with tracer.run(run_id):
            async with Toolbox(tracer, [docs_spec(toy_repo)]) as tb:
                names = {d["name"] for d in tb.definitions()}
                listing = await tb.call("docs__list_docs", {})
                code = await tb.call("docs__search_code", {"query": "print"})
                nothing = await tb.call("docs__search_docs", {"query": "zzzz-no-match"})
        return names, listing, code, nothing

    names, listing, code, nothing = asyncio.run(main())
    assert "docs__search_code" in names
    assert ".pytest_cache" not in listing.text
    assert "app.py" in code.text
    assert nothing.text == "[]"  # clean JSON, not a Python repr


def test_docs_server_is_read_only_and_scoped(tracer, toy_repo, db_path):
    async def main():
        run_id = tracer.create_run("t")
        with tracer.run(run_id):
            async with Toolbox(tracer, [docs_spec(toy_repo)]) as tb:
                names = {d["name"] for d in tb.definitions()}
                listing = await tb.call("docs__list_docs", {})
                found = await tb.call("docs__search_docs", {"query": "pytest"})
                doc = await tb.call("docs__read_doc", {"path": "docs/guide.md"})
                escape = await tb.call("docs__read_doc", {"path": "../outside.md"})
                code = await tb.call("docs__read_doc", {"path": "app.py"})
                src = await tb.call("docs__read_file", {"path": "app.py"})
                src_escape = await tb.call("docs__read_file", {"path": "../outside.py"})
                src_git = await tb.call("docs__read_file", {"path": ".git/config"})
                unknown = await tb.call("docs__write_doc", {})
        return names, listing, found, doc, escape, code, unknown, src, src_escape, src_git

    names, listing, found, doc, escape, code, unknown, src, src_escape, src_git = asyncio.run(
        main()
    )
    assert names == {
        "docs__list_docs",
        "docs__search_docs",
        "docs__search_code",
        "docs__read_doc",
        "docs__read_file",
    }
    assert "print('hi')" in src.text and not src.is_error
    assert src_escape.is_error and src_git.is_error
    assert "docs/guide.md" in listing.text and "README.md" in listing.text
    assert "guide.md" in found.text
    assert "Routes live in app.py" in doc.text
    assert escape.is_error and code.is_error  # outside root / not a doc
    assert unknown.is_error and "not permitted" in unknown.text
    assert any(s["name"] == "mcp.docs.read_doc" and s["kind"] == "mcp" for s in span_rows(db_path))


@needs_npx
def test_filesystem_policy_guard_and_workspace(tracer, toy_repo, db_path, tmp_path):
    ws = create_workspace(toy_repo, tmp_path / "workspaces", "abc123def4567890")
    fs_args = ["-y", "@modelcontextprotocol/server-filesystem", str(ws.path)]

    async def main():
        run_id = tracer.create_run("t")
        with tracer.run(run_id):
            # Read-only policy (as the Review agent will use): no write tools are exposed.
            ro = ServerSpec("filesystem", "npx", fs_args, policy=read_only)
            async with Toolbox(tracer, [ro]) as tb:
                ro_names = {d["name"] for d in tb.definitions()}
                denied = await tb.call("filesystem__write_file", {"path": "x", "content": "y"})

            rw = ServerSpec(
                "filesystem",
                "npx",
                fs_args,
                policy=only("read_text_file", "write_file", "edit_file"),
                guard=forbid_git_paths,
            )
            async with Toolbox(tracer, [rw]) as tb:
                ok = await tb.call(
                    "filesystem__write_file",
                    {"path": str(ws.path / "new.txt"), "content": "hello"},
                )
                git_write = await tb.call(
                    "filesystem__write_file", {"path": str(ws.path / ".git"), "content": "x"}
                )
                outside = await tb.call(
                    "filesystem__write_file",
                    {"path": str(tmp_path / "escape.txt"), "content": "x"},
                )
        return ro_names, denied, ok, git_write, outside

    ro_names, denied, ok, git_write, outside = asyncio.run(main())

    assert "read_text_file" in {n.split("__", 1)[1] for n in ro_names}
    assert not {"filesystem__write_file", "filesystem__edit_file"} & ro_names
    assert denied.is_error
    assert not ok.is_error and (ws.path / "new.txt").read_text() == "hello"
    assert git_write.is_error and ".git" in git_write.text
    assert outside.is_error and not (tmp_path / "escape.txt").exists()

    statuses = {s["name"]: s["status"] for s in span_rows(db_path) if s["kind"] == "mcp"}
    assert statuses["mcp.filesystem.write_file"] in {"ok", "error"}

    diff, files = collect_patch(ws, "test commit")
    assert files == ["new.txt"] and "+hello" in diff
    assert not (toy_repo / "new.txt").exists()  # real checkout untouched
    remove_workspace(ws)
