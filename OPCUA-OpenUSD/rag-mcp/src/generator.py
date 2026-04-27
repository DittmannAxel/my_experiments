"""LLM-backed generator. Uses vLLM via OpenAI-compatible client."""
from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

from .retriever import Chunk

log = logging.getLogger("generator")

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://host.docker.internal:8000/v1")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")

SYSTEM_PROMPT = (
    "You are an OPC UA specification expert. Answer the user's question using "
    "ONLY the spec excerpts provided in the context. Cite each statement with "
    "[<part>#<chunk_id>] inline. If the excerpts don't cover the question, say "
    "so plainly — do not invent. Keep the answer concise (≤6 sentences)."
)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=VLLM_BASE_URL, api_key="not-used")
    return _client


async def answer(question: str, chunks: list[Chunk]) -> str:
    if not chunks:
        return "No relevant spec excerpts were found for that question."
    context = "\n\n".join(
        f"[{c.part}#{c.chunk_id}] {c.content}" for c in chunks
    )
    user_msg = f"Context (OPC UA spec excerpts):\n{context}\n\nQuestion: {question}"

    client = _get_client()
    resp = await client.chat.completions.create(
        model=VLLM_MODEL,
        messages=[
            # Nemotron reasoning toggle. "off" → direct answer, "on" → CoT first.
            {"role": "system", "content": "detailed thinking off"},
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning", None) or "").strip()
