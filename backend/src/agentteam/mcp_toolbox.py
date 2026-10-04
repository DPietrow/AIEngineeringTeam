"""MCP client layer: connects to stdio MCP servers, scopes tools per agent, traces every call.

Least privilege is enforced twice: tools an agent may not use are never shown to the model
(policy), and every call is re-checked before it reaches the server (guard).
"""

from __future__ import annotations

import ast
import json
import os
import shutil
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import get_default_environment, stdio_client

from .tracing import Tracer

NAME_SEP = "__"
MAX_RESULT_CHARS_FOR_LLM = 50_000
CALL_TIMEOUT = timedelta(seconds=60)

Policy = Callable[[types.Tool], bool]
Guard = Callable[[str, dict[str, Any]], None]


class ToolNotAllowed(RuntimeError):
    pass


def read_only(tool: types.Tool) -> bool:
    """Allow only tools the server itself marks readOnlyHint=true."""
    return bool(tool.annotations and tool.annotations.readOnlyHint)


def only(*names: str) -> Policy:
    allowed = set(names)
    return lambda tool: tool.name in allowed


def forbid_git_paths(_tool: str, args: dict[str, Any]) -> None:
    """Never let an agent touch .git (the worktree's link back to the real repo)."""
    candidates: list[str] = []
    for key in ("path", "source", "destination"):
        if isinstance(args.get(key), str):
            candidates.append(args[key])
    if isinstance(args.get("paths"), list):
        candidates.extend(p for p in args["paths"] if isinstance(p, str))
    for path in candidates:
        if ".git" in path.replace("\\", "/").split("/"):
            raise ToolNotAllowed(f"access to .git is not allowed: {path}")


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str]
    policy: Policy
    guard: Guard | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class ToolResult:
    text: str
    is_error: bool


def _result_text(result: types.CallToolResult) -> str:
    parts = [c.text for c in result.content if isinstance(c, types.TextContent)]
    if parts:
        return "\n".join(parts)
    structured = result.structuredContent
    if isinstance(structured, dict) and set(structured) == {"result"}:
        structured = structured["result"]  # FastMCP wraps bare lists/values as {"result": ...}
    return json.dumps(structured) if structured is not None else ""


def coerce_args(schema: dict[str, Any] | None, args: dict[str, Any]) -> dict[str, Any]:
    """Small models sometimes send arrays/objects as JSON-encoded strings. Decode those
    when the tool's schema says the argument should be an array or object."""
    properties = (schema or {}).get("properties", {})
    return {key: _coerce_value(properties.get(key, {}), value) for key, value in args.items()}


def _schema_types(schema: dict[str, Any]) -> set[str]:
    """Types a schema accepts, looking through anyOf/oneOf and `type: [..]` forms."""
    types: set[str] = set()
    t = schema.get("type")
    if isinstance(t, str):
        types.add(t)
    elif isinstance(t, list):
        types.update(x for x in t if isinstance(x, str))
    for key in ("anyOf", "oneOf"):
        for sub in schema.get(key, []) or []:
            if isinstance(sub, dict):
                types |= _schema_types(sub)
    return types


def _decode_container(text: str, want: type) -> Any:
    """Parse a string as JSON, then as a Python literal (models often send single quotes).
    Returns None if it is not a container of the wanted kind."""
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(text.strip())
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(parsed, want):
            return parsed
    return None


def _coerce_value(schema: dict[str, Any], value: Any) -> Any:
    types = _schema_types(schema)
    if isinstance(value, str):
        if "string" in types:
            return value  # a string is acceptable as-is; never second-guess it
        for name, want in (("array", list), ("object", dict)):
            if name in types:
                parsed = _decode_container(value, want)
                if parsed is not None:
                    return _coerce_value(schema, parsed)
        return value
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_coerce_value(schema["items"], v) for v in value]
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        props = schema["properties"]
        return {k: _coerce_value(props.get(k, {}), v) for k, v in value.items()}
    return value


_PASSTHROUGH_EXACT = {"PYTHONPATH", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}


def _server_env(extra: dict[str, str]) -> dict[str, str]:
    """Minimal environment for MCP subprocesses.

    Starts from the MCP SDK's safe defaults and adds only what servers need to run and
    reach the network (proxy, npm and CA-bundle settings). API keys are deliberately excluded.
    """
    env = dict(get_default_environment())
    for key, value in os.environ.items():
        lower = key.lower()
        if key in _PASSTHROUGH_EXACT or "proxy" in lower or lower.startswith("npm_config"):
            env[key] = value
    env.update(extra)
    return env


class Toolbox:
    def __init__(self, tracer: Tracer, servers: list[ServerSpec]) -> None:
        self.tracer = tracer
        self.servers = servers
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, tuple[ServerSpec, types.Tool]] = {}

    async def __aenter__(self) -> Toolbox:
        await self._stack.__aenter__()
        try:
            for spec in self.servers:
                params = StdioServerParameters(
                    command=shutil.which(spec.command) or spec.command,
                    args=spec.args,
                    env=_server_env(spec.env),
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._sessions[spec.name] = session
                for tool in (await session.list_tools()).tools:
                    if spec.policy(tool):
                        self._tools[f"{spec.name}{NAME_SEP}{tool.name}"] = (spec, tool)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    def definitions(self) -> list[dict[str, Any]]:
        """Tools in Anthropic tool-use format, namespaced as <server>__<tool>."""
        return [
            {
                "name": qualified,
                "description": tool.description or tool.name,
                "input_schema": tool.inputSchema,
            }
            for qualified, (_, tool) in self._tools.items()
        ]

    async def call(self, qualified_name: str, args: dict[str, Any]) -> ToolResult:
        entry = self._tools.get(qualified_name)
        if entry is None:
            return ToolResult(f"Unknown or not permitted tool: {qualified_name}", True)
        spec, tool = entry
        try:
            with self.tracer.span(
                f"mcp.{spec.name}.{tool.name}", kind="mcp", input=args, stacklevel=1
            ) as span:
                call_args = coerce_args(tool.inputSchema, args)
                if spec.guard is not None:
                    spec.guard(tool.name, call_args)
                result = await self._sessions[spec.name].call_tool(
                    tool.name, call_args, read_timeout_seconds=CALL_TIMEOUT
                )
                text = _result_text(result)
                span.set_output(
                    {
                        "is_error": bool(result.isError),
                        "text": text,
                        "arguments_coerced": call_args != args,
                    }
                )
                return ToolResult(text, bool(result.isError))
        except ToolNotAllowed as exc:  # recorded as an error span, reported back to the model
            return ToolResult(str(exc), True)
