"""Generic tool-use loop with a hard step cap. Used by every tool-using agent."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .deadline import check_deadline
from .llm import LLM
from .mcp_toolbox import MAX_RESULT_CHARS_FOR_LLM, Toolbox, ToolResult, coerce_args
from .tracing import Tracer

MAX_NUDGES = 2  # times a model that stops without submitting is told to call the submit tool

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
    validate_submit: Callable[[dict[str, Any]], object] | None = None,
) -> LoopResult:
    """Drive model turns until it stops calling tools (or calls the submit tool).

    submit_tool is a client-side tool whose input is the agent's structured result.
    Its input is first repaired against the tool's schema (models sometimes send a list as a
    JSON string); if validate_submit is given and raises ValueError (pydantic's
    ValidationError is one), the error goes back to the model as a failed tool result so it can
    fix the submission, instead of failing the whole run. Each retry costs a step, so the step
    cap still bounds it.
    on_tool_result sees every real tool result, so callers can record facts (exit codes)
    instead of trusting what the model claims.
    """
    tools = toolbox.definitions() + ([submit_tool] if submit_tool else [])
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    nudges = 0

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
            submit_call = next((c for c in turn.tool_calls if c.name == submit_tool["name"]), None)
            if submit_call is not None:
                fixed = coerce_args(submit_tool.get("input_schema"), submit_call.input)
                problem = None
                if validate_submit is not None:
                    try:
                        validate_submit(fixed)
                    except ValueError as exc:
                        problem = str(exc)
                if problem is None:
                    return LoopResult(turn.text, fixed, step)
                tracer.emit(
                    "agent.submit_rejected", {"agent": name, "step": step, "error": problem[:500]}
                )
                results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": c.id,
                        "content": (
                            f"Invalid submission, fix it and call {submit_tool['name']} again:\n"
                            f"{problem[:1500]}"
                            if c is submit_call
                            else "Not executed: fix the invalid submission first."
                        ),
                        "is_error": True,
                    }
                    for c in turn.tool_calls
                ]
                messages.append({"role": "user", "content": results})
                continue

        if not turn.tool_calls:
            if submit_tool and nudges < MAX_NUDGES:
                nudges += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"You must finish by calling {submit_tool['name']} now. "
                            "Do not reply with plain text."
                        ),
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
