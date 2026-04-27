"""Microsoft Agent Framework advisory agent.

Uses `agent-framework` (GA) with the OpenAIChatClient pointed at the
bare-metal vLLM serving an OpenAI-compatible endpoint
(default model: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8, served with
`--tool-call-parser qwen3_coder` and the nano_v3 reasoning parser plugin).

The two advisory tools are decorated with @tool. Both are
`approval_mode="never_require"` because the human-in-the-loop in this
PoC is the operator clicking Approve in the Robotics Dashboard, which
calls the `ApproveRecommendation` method on the OPC UA server — that
operator action is what actually applies the recommended state change,
not the agent's tool call.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any

from agent_framework import Agent, tool
from agent_framework.openai import OpenAIChatClient

from . import tools as ops
from .anomaly_detector import AnomalyEvent

log = logging.getLogger("agent")

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://host.docker.internal:8000/v1")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8")
# Nemotron-3 uses the nano_v3 reasoning parser server-side, so we don't
# need the `detailed thinking off` prelude that Nemotron-Nano-8B-v1 took
# in the system message — we control thinking via chat_template_kwargs
# (enable_thinking=False) on the request instead.
SYSTEM_PROMPT = (
    Path(os.environ.get("PROMPTS_DIR", "/app/prompts"))
    .joinpath("system.md")
    .read_text()
)


# ─────────── Tools ───────────


@tool(approval_mode="never_require")
async def query_specification(
    question: Annotated[str, "Free-form question about the OPC UA spec"],
    part_filter: Annotated[
        str | None,
        "Optional SQL LIKE filter on the spec part, e.g. 'Core/%' or 'DI/%'",
    ] = None,
) -> dict:
    """Query the OPC UA specification corpus for guidance on standard-compliant modeling."""
    return await ops.query_specification(question=question, part_filter=part_filter)


@tool(approval_mode="never_require")
async def write_recommendation_to_opcua(
    title: Annotated[str, "Short title (≤80 chars)"],
    rationale: Annotated[str, "2–4 sentences citing the spec excerpt"],
    actions: Annotated[
        list[dict],
        "List of {node_id, value} dicts the operator should approve. "
        "Use node_id='RobotController.ProgramState' with value=6 for "
        "MaintenanceRequired thermal anomalies.",
    ],
    spec_citation: Annotated[
        str | None, "Spec part/section, e.g. 'Core/Part4#5.2'"
    ] = None,
) -> str:
    """Publish an advisory recommendation to the OPC UA RobotRecommendations namespace."""
    return await ops.write_recommendation_to_opcua(
        title=title,
        rationale=rationale,
        actions=actions,
        spec_citation=spec_citation,
    )


# ─────────── Agent ───────────


_chat_client: OpenAIChatClient | None = None
_agent: Agent | None = None


def _get_agent() -> Agent:
    global _chat_client, _agent
    if _agent is None:
        _chat_client = OpenAIChatClient(
            model=VLLM_MODEL,
            api_key="not-used",
            base_url=VLLM_BASE_URL,
        )
        _agent = Agent(
            _chat_client,
            SYSTEM_PROMPT,
            name="robot-twin-agent",
            tools=[query_specification, write_recommendation_to_opcua],
        )
        log.info(
            "Microsoft Agent Framework agent ready (model=%s, base_url=%s)",
            VLLM_MODEL,
            VLLM_BASE_URL,
        )
    return _agent


def _format_anomaly(ev: AnomalyEvent) -> str:
    return (
        f"ANOMALY DETECTED:\n"
        f"  axis: {ev.axis}\n"
        f"  metric: {ev.metric}\n"
        f"  value: {ev.value:.2f} C\n"
        f"  threshold: {ev.threshold:.1f} C\n"
        f"  duration_above: {ev.duration_above:.1f} s\n\n"
        "Call query_specification ONCE for context, then call "
        "write_recommendation_to_opcua exactly once with:\n"
        "  - title: short label of the issue\n"
        "  - rationale: 2-3 sentences citing the spec\n"
        "  - actions: [{\"node_id\": \"RobotController.ProgramState\", \"value\": 6}]\n"
        "  - spec_citation: the relevant Part/section\n"
        "Do not respond with prose — call the tools."
    )


async def handle_anomaly(ev: AnomalyEvent) -> None:
    """One-shot agent run for an anomaly event.

    Nemotron's reasoning trace alone runs ~300–500 tokens; the tool
    arguments take another ~200; we set max_tokens=2048 to give the model
    headroom for both. Temperature is low so the recommendation stays
    deterministic across reruns.
    """
    agent = _get_agent()
    log.info("Agent reasoning over anomaly axis=%d", ev.axis)
    try:
        result = await agent.run(
            _format_anomaly(ev),
            options={
                "max_tokens": 4096,
                "temperature": 0.2,
                "tool_choice": "auto",
                # Nemotron-3 ships a thinking mode that's on by default.
                # For a single anomaly → recommendation hop we want a fast,
                # deterministic tool call, so disable it via the model's
                # native chat-template flag.
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            },
        )
    except Exception:
        log.exception("Agent run failed")
        return

    final_text = getattr(result, "text", None) or ""
    log.info("Agent run done: %s", (final_text or "(no text)")[:240])
