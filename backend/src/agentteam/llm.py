"""LLM clients. Every call is a traced span with full request, response, tokens and cost.

Structured output uses a forced tool call so the model returns schema-valid JSON.
"""

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

from .schemas import DesignSpecBody, PlannedChange
from .tracing import Tracer

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

T = TypeVar("T", bound=BaseModel)


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

    @abstractmethod
    def _call(
        self, name: str, system: str, user: str, schema: type[BaseModel]
    ) -> tuple[dict[str, Any], int, int]:
        """Return (structured data, input tokens, output tokens)."""


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


class FakeLLM(LLM):
    """Deterministic stand-in so the whole pipeline runs with no API key or spend."""

    model = "fake"

    def __init__(self, tracer: Tracer, responses: dict[str, dict[str, Any]] | None = None) -> None:
        super().__init__(tracer)
        self.responses = responses or {}

    def _call(self, name, system, user, schema):
        if name in self.responses:
            data = self.responses[name]
        elif schema is DesignSpecBody:
            data = DesignSpecBody(
                summary=f"[fake] plan for: {user[:80]}",
                changes=[
                    PlannedChange(
                        path="src/example.py", action="modify", description="Placeholder change"
                    )
                ],
                acceptance_criteria=["Existing tests still pass", "New behaviour has a test"],
            ).model_dump()
        else:
            raise NotImplementedError(f"FakeLLM has no canned response for {name!r}")
        return data, len(system + user) // 4, len(str(data)) // 4
