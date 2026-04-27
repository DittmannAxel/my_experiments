"""top-k cosine retrieval over spec_chunks in pgvector."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg

from . import embedder

log = logging.getLogger("retriever")


@dataclass
class Chunk:
    part: str
    chunk_id: str
    title: str | None
    content: str
    score: float  # 1 - cosine_distance, so higher is better


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


async def retrieve(
    pool: asyncpg.Pool,
    query: str,
    k: int = 6,
    part_filter: str | None = None,
) -> list[Chunk]:
    qvec = embedder.embed(query)
    qvec_lit = _vec_literal(qvec)

    sql = (
        "SELECT part, chunk_id, title, content, "
        "       1 - (embedding <=> $1::vector) AS score "
        "FROM spec_chunks "
    )
    args: list = [qvec_lit]
    if part_filter:
        sql += " WHERE part LIKE $2 "
        args.append(part_filter)
    sql += " ORDER BY embedding <=> $1::vector LIMIT $%d" % (len(args) + 1)
    args.append(k)

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [
        Chunk(
            part=r["part"],
            chunk_id=r["chunk_id"],
            title=r["title"],
            content=r["content"],
            score=float(r["score"]),
        )
        for r in rows
    ]
