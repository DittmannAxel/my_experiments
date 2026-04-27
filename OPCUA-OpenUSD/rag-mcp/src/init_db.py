"""One-shot embedding job — embed every chunk in ua-spec-source/specifications.

Idempotent: skips if the spec_chunks table already has rows. Otherwise walks
every rag-chunks.json under /app/ua-spec-source/specifications/.

Each rag-chunks.json is a JSON list of objects shaped (best effort) like:
    {"id": "...", "title": "...", "content": "..."}

The relative path under specifications/ becomes the "part" tag, e.g.
"Core/Part4".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import asyncpg

from . import embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("init_db")

SPEC_ROOT = Path(os.environ.get("UA_SPEC_ROOT", "/app/ua-spec-source/specifications"))
PG_DSN = os.environ.get("PGVECTOR_DSN", "postgresql://rag:rag@pgvector:5432/rag")
BATCH_SIZE = 32


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def _extract_chunks(path: Path) -> list[dict]:
    """Load rag-chunks.json. UA-for-AI-Prototype's shape is:
        {"Title": "Part N - Foo", "Chunks": [{"Id":..., "Header":..., "Content":...}, ...]}
    We also tolerate a few alternate shapes for forward-compat.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [_normalize(c) for c in raw]
    if isinstance(raw, dict):
        for k in ("Chunks", "chunks", "items", "data"):
            if k in raw and isinstance(raw[k], list):
                return [_normalize(c) for c in raw[k]]
    log.warning("Unknown shape in %s; skipping", path)
    return []


def _normalize(chunk: dict) -> dict:
    """Map upstream's PascalCase keys to our snake_case schema."""
    return {
        "id":      chunk.get("Id")     or chunk.get("id")     or chunk.get("chunk_id"),
        "title":   chunk.get("Header") or chunk.get("title"),
        "content": chunk.get("Content") or chunk.get("content") or chunk.get("text") or "",
    }


async def populate_once():
    log.info("Connecting to %s ...", PG_DSN)
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval("SELECT COUNT(*) FROM spec_chunks")
            if n and n > 0:
                log.info("spec_chunks already has %d rows; skipping init.", n)
                return

        # Find all rag-chunks.json under SPEC_ROOT.
        chunk_files = sorted(SPEC_ROOT.rglob("rag-chunks.json"))
        log.info("Found %d rag-chunks.json files under %s", len(chunk_files), SPEC_ROOT)

        total = 0
        t0 = time.monotonic()
        for cf in chunk_files:
            rel = cf.parent.relative_to(SPEC_ROOT).as_posix()  # e.g. "Core/Part4"
            chunks = _extract_chunks(cf)
            if not chunks:
                continue
            log.info("Embedding %d chunks from %s ...", len(chunks), rel)
            for i in range(0, len(chunks), BATCH_SIZE):
                batch = chunks[i : i + BATCH_SIZE]
                texts = [c["content"] for c in batch]
                vecs = embedder.embed_batch(texts)
                rows = []
                for c, v in zip(batch, vecs):
                    rows.append((
                        rel,
                        str(c.get("id") or f"{rel}-{total}"),
                        c.get("title"),
                        c["content"],
                        _vec_literal(v),
                    ))
                    total += 1
                async with pool.acquire() as conn:
                    await conn.executemany(
                        "INSERT INTO spec_chunks (part, chunk_id, title, content, embedding) "
                        "VALUES ($1, $2, $3, $4, $5::vector) "
                        "ON CONFLICT (part, chunk_id) DO NOTHING",
                        rows,
                    )
            log.info("  %s done (cumulative %d, %.1fs)", rel, total, time.monotonic() - t0)
        log.info("Embedding complete: %d chunks in %.1f s", total, time.monotonic() - t0)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(populate_once())
