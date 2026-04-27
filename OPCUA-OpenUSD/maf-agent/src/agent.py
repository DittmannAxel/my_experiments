"""Tool-calling-driven advisory agent.

Uses the OpenAI SDK pointed at the bare-metal vLLM (Qwen3.6-35B-A3B) which has
tool calling enabled via --tool-call-parser qwen3_xml. Microsoft Agent
Framework would be the BUILD.md preference, but plain OpenAI tool calling is
functionally equivalent for the PoC and avoids a pre-release dependency.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI

from . import tools
from .anomaly_detector import AnomalyEvent

log = logging.getLogger("agent")

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://host.docker.internal:8000/v1")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
SYSTEM_PROMPT = Path(os.environ.get("PROMPTS_DIR", "/app/prompts")).joinpath("system.md").read_text()

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_specification",
            "description": "Query the OPC UA specification corpus for guidance on standard-compliant modeling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "part_filter": {"type": "string", "description": "Optional SQL LIKE filter, e.g. 'Core/%' or 'DI/%'."},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_recommendation_to_opcua",
            "description": "Publish an advisory recommendation to OPC UA RobotRecommendations namespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "node_id": {"type": "string"},
                                "value": {},
                            },
                            "required": ["node_id", "value"],
                        },
                    },
                    "spec_citation": {"type": "string"},
                },
                "required": ["title", "rationale", "actions"],
            },
        },
    },
]

_client: AsyncOpenAI | None = None


def _llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=VLLM_BASE_URL, api_key="not-used")
    return _client


async def _dispatch_tool(name: str, args: dict) -> str:
    if name == "query_specification":
        result = await tools.query_specification(**args)
        return json.dumps(result)
    if name == "write_recommendation_to_opcua":
        return await tools.write_recommendation_to_opcua(**args)
    return json.dumps({"error": f"unknown tool {name}"})


def _format_anomaly(ev: AnomalyEvent) -> str:
    return (
        f"ANOMALY DETECTED:\n"
        f"  axis: {ev.axis}\n"
        f"  metric: {ev.metric}\n"
        f"  value: {ev.value:.2f} C\n"
        f"  threshold: {ev.threshold:.1f} C\n"
        f"  duration_above: {ev.duration_above:.1f} s\n\n"
        "Investigate and recommend a standard-compliant action."
    )


async def handle_anomaly(ev: AnomalyEvent) -> None:
    """One-shot agent run for an anomaly. Loops while the model emits tool calls."""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _format_anomaly(ev)},
    ]
    log.info("Agent reasoning over anomaly axis=%d", ev.axis)

    spec_query_count = 0
    # Nemotron-style reasoning toggle: "off" keeps responses tight + tool-calling
    # focused. (Qwen used `enable_thinking`; Nemotron uses a system message.)
    messages.insert(0, {"role": "system", "content": "detailed thinking off"})
    for step in range(12):
        # After 2 spec queries, force the model to write a recommendation.
        force_write = spec_query_count >= 2
        resp = await _llm().chat.completions.create(
            model=VLLM_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice=(
                {"type": "function", "function": {"name": "write_recommendation_to_opcua"}}
                if force_write
                else "auto"
            ),
            temperature=0.2,
            max_tokens=1024,
        )
        msg = resp.choices[0].message

        # Append assistant message (with tool_calls) to the running transcript.
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (msg.tool_calls or [])
            ],
        })

        if not msg.tool_calls:
            log.info("Agent reasoning done (no tool calls): %s", (msg.content or "")[:240])
            return

        wrote_reco = False
        # Execute each tool call and append its result.
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            log.info("Tool: %s args=%s", tc.function.name, args)
            if tc.function.name == "query_specification":
                spec_query_count += 1
            if tc.function.name == "write_recommendation_to_opcua":
                wrote_reco = True
            try:
                tool_out = await _dispatch_tool(tc.function.name, args)
            except Exception as e:
                log.exception("Tool '%s' raised: %s", tc.function.name, e)
                tool_out = json.dumps({"error": str(e)})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_out,
            })

        if wrote_reco:
            log.info("Recommendation written; agent run done.")
            return

    log.warning("Agent loop hit step cap without finishing.")
