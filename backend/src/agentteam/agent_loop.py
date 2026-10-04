"""Generic tool-use loop with a hard step cap. Used by every tool-using agent."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .deadline import check_deadline
from .llm import LLM
from .mcp_toolbox import MAX_RESULT_CHARS_FOR_LLM, Toolbox, ToolResult
from .tracing import Tracer

ToolResultHook = Callable[[str, dict[str, Any], ToolResult], None]


class StepLimitExceeded(RuntimeError):
    pass


class AgentError(RuntimeError):
    pass


@dataclass
class LoopResult:
    final_text: str
    submitted: dict[str, Any] | None
    steps: int


def _for_llm(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS_FOR_LLM:
        return text
    return (
        text[:MAX_RESULT_CHARS_FOR_LLM]
        + f"\n...[truncated {len(text) - MAX_RESULT_CHARS_FOR_LLM} chars]"
    )


async def run_agent_loop(
    *,
    llm: LLM,
    tracer: Tracer,
    name: str,
    system: str,
    user: str,
    toolbox: Toolbox,
    max_steps: int,
    submit_tool: dict[str, Any] | None = None,
    on_tool_result: ToolResultHook | None = None,
) -> LoopResult:
    """Drive model turns until it stops calling tools (or calls the submit tool).

    submit_tool is a client-side tool whose input is the agent's structured result.
    on_tool_result sees every real tool result, so callers can record facts (exit codes)
    instead of trusting what the model claims.
    """
    tools = toolbox.definitions() + ([submit_tool] if submit_tool else [])
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    nudged = False

    for step in range(1, max_steps + 1):
        check_deadline()  # stop between steps, never in the middle of a call
        turn = llm.converse(
            name=f"{name}.step{step}", system=system, messages=messages, tools=tools
        )
        messages.append({"role": "assistant", "content": turn.assistant_content})
        tracer.emit(
            "agent.step",
            {"agent": name, "step": step, "tool_calls": [c.name for c in turn.tool_calls]},
        )

        if submit_tool:
            for call in turn.tool_calls:
                if call.name == submit_tool["name"]:
                    return LoopResult(turn.text, call.input, step)

        if not turn.tool_calls:
            if submit_tool and not nudged:
                nudged = True
                messages.append(
                    {
                        "role": "user",
                        "content": f"You must finish by calling {submit_tool['name']}.",
                    }
                )
                continue
            return LoopResult(turn.text, None, step)

        results = []
        for call in turn.tool_calls:
            res = await toolbox.call(call.name, call.input)
            if on_tool_result is not None:
                on_tool_result(call.name, call.input, res)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": _for_llm(res.text),
                    "is_error": res.is_error,
                }
            )
        messages.append({"role": "user", "content": results})

    tracer.emit("agent.step_limit", {"agent": name, "max_steps": max_steps})
    raise StepLimitExceeded(f"{name} exceeded {max_steps} steps")
