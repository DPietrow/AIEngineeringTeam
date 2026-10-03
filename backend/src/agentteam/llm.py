"""LLM clients. Every call is a traced span with full request, response, tokens and cost.

Two call styles:
- structured(): one forced tool call, returns a validated pydantic model.
- converse(): one turn of a tool-use conversation (the agent loop drives it).
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .schemas import DesignSpecBody, PlannedChange
from .tracing import Tracer

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

T = TypeVar("T", bound=BaseModel)


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    assistant_content: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


class LLM(ABC):
    model: str

    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer

    def structured(self, *, name: str, system: str, user: str, schema: type[T]) -> T:
        with self.tracer.span(
            f"llm.{name}",
            kind="llm",
            input={"model": self.model, "system": system, "user": user},
            stacklevel=1,  # attribute the call-site to our caller, not this helper
        ) as span:
            data, tokens_in, tokens_out = self._call(name, system, user, schema)
            span.set_usage(model=self.model, input_tokens=tokens_in, output_tokens=tokens_out)
            span.set_output(data)
            return schema.model_validate(data)

    def converse(
        self, *, name: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Turn:
        with self.tracer.span(
            f"llm.{name}",
            kind="llm",
            input={
                "model": self.model,
                "system": system,
                "messages": messages,
                "tools": [t["name"] for t in tools],
            },
            stacklevel=1,
        ) as span:
            turn = self._converse(name, system, messages, tools)
            span.set_usage(
                model=self.model, input_tokens=turn.tokens_in, output_tokens=turn.tokens_out
            )
            span.set_output(
                {
                    "text": turn.text,
                    "tool_calls": [{"name": c.name, "input": c.input} for c in turn.tool_calls],
                    "stop_reason": turn.stop_reason,
                }
            )
            return turn

    @abstractmethod
    def _call(
        self, name: str, system: str, user: str, schema: type[BaseModel]
    ) -> tuple[dict[str, Any], int, int]:
        """Return (structured data, input tokens, output tokens)."""

    @abstractmethod
    def _converse(
        self, name: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Turn:
        """One model turn."""


class AnthropicLLM(LLM):
    def __init__(
        self,
        tracer: Tracer,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
    ) -> None:
        super().__init__(tracer)
        import anthropic  # imported lazily so tests and fake mode need no SDK/key

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def _call(self, name, system, user, schema):
        tool = {
            "name": name,
            "description": f"Return the {name} as structured data.",
            "input_schema": schema.model_json_schema(),
        }
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": name},
        )
        block = next((b for b in resp.content if b.type == "tool_use"), None)
        if block is None:
            raise RuntimeError(f"model returned no tool call (stop_reason={resp.stop_reason})")
        return dict(block.input), resp.usage.input_tokens, resp.usage.output_tokens

    def _converse(self, name, system, messages, tools):
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            **kwargs,
        )
        content: list[dict[str, Any]] = []
        texts: list[str] = []
        calls: list[ToolCall] = []
        for b in resp.content:
            if b.type == "text":
                content.append({"type": "text", "text": b.text})
                texts.append(b.text)
            elif b.type == "tool_use":
                content.append(
                    {"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)}
                )
                calls.append(ToolCall(b.id, b.name, dict(b.input)))
        return Turn(
            text="\n".join(texts),
            tool_calls=calls,
            stop_reason=resp.stop_reason or "end_turn",
            assistant_content=content,
            tokens_in=resp.usage.input_tokens,
            tokens_out=resp.usage.output_tokens,
        )


def _tool_turn(call_id: str, name: str, tool_input: dict[str, Any], text: str = "") -> Turn:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({"type": "tool_use", "id": call_id, "name": name, "input": tool_input})
    return Turn(
        text=text,
        tool_calls=[ToolCall(call_id, name, tool_input)],
        stop_reason="tool_use",
        assistant_content=content,
        tokens_in=100,
        tokens_out=40,
    )


def _text_turn(text: str) -> Turn:
    return Turn(
        text=text,
        stop_reason="end_turn",
        assistant_content=[{"type": "text", "text": text}],
        tokens_in=100,
        tokens_out=20,
    )


FakeScript = Callable[[str, str, list[dict[str, Any]], list[dict[str, Any]]], Turn]


class FakeLLM(LLM):
    """Deterministic stand-in so the whole pipeline runs with no API key or spend.

    Pass `script` (name, system, messages, tools) -> Turn to drive custom behaviour in tests.
    """

    model = "fake"

    def __init__(
        self,
        tracer: Tracer,
        responses: dict[str, dict[str, Any]] | None = None,
        script: FakeScript | None = None,
    ) -> None:
        super().__init__(tracer)
        self.responses = responses or {}
        self.script = script

    def _call(self, name, system, user, schema):
        if name in self.responses:
            data = self.responses[name]
        elif schema is DesignSpecBody:
            data = self._default_spec(user).model_dump()
        else:
            raise NotImplementedError(f"FakeLLM has no canned response for {name!r}")
        return data, len(system + user) // 4, len(str(data)) // 4

    @staticmethod
    def _default_spec(task: str) -> DesignSpecBody:
        return DesignSpecBody(
            summary=f"[fake] plan for: {task[:80]}",
            changes=[
                PlannedChange(
                    path="AGENT_NOTES.md", action="create", description="Placeholder change"
                )
            ],
            acceptance_criteria=["Existing tests still pass", "New behaviour has a test"],
        )

    def _converse(self, name, system, messages, tools):
        if self.script is not None:
            return self.script(name, system, messages, tools)
        agent = name.split(".")[0]
        assistant_turns = sum(1 for m in messages if m["role"] == "assistant")
        tool_names = {t["name"] for t in tools}
        if agent == "architect":
            task = str(messages[0]["content"])
            return _tool_turn(
                "fake-spec", "submit_design_spec", self._default_spec(task).model_dump()
            )
        if agent == "implementation":
            match = re.search(r"Workspace root: (.+)", system)
            if assistant_turns == 0 and match and "filesystem__write_file" in tool_names:
                path = str(Path(match.group(1).strip()) / "AGENT_NOTES.md")
                return _tool_turn(
                    "fake-write",
                    "filesystem__write_file",
                    {"path": path, "content": "# Agent notes\n\nWritten by the fake LLM.\n"},
                )
            return _text_turn("Done (fake LLM): created AGENT_NOTES.md.")
        if agent == "testing":
            if assistant_turns == 0:
                return _tool_turn("fake-pytest", "terminal__run_command", {"command": "pytest -q"})
            if assistant_turns == 1:
                return _tool_turn("fake-ruff", "terminal__run_command", {"command": "ruff check ."})
            return _text_turn("Ran pytest and ruff (fake LLM).")
        if agent == "review":
            return _tool_turn(
                "fake-verdict",
                "submit_verdict",
                {"decision": "approved", "summary": "Looks good (fake LLM).", "comments": []},
            )
        raise NotImplementedError(f"FakeLLM has no script for {name!r}")
