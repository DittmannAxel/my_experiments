"""rag-mcp HTTP + MCP server.

- HTTP API on :49322
    POST /api/specification/query  {"question": str, "part_filter": str | None}
    GET  /health
- MCP SSE on /sse endpoint, exposing one tool: specificationQuery
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

import asyncpg
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from . import generator, init_db, retriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rag-mcp")

PG_DSN = os.environ.get("PGVECTOR_DSN", "postgresql://rag:rag@pgvector:5432/rag")
PORT = int(os.environ.get("PORT", "49322"))

# ─────────── DB pool ───────────
_pool: asyncpg.Pool | None = None


async def _ensure_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
    return _pool


# ─────────── Initialization (run before HTTP starts) ───────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    log.info("Initial DB seed (idempotent) ...")
    try:
        await init_db.populate_once()
    except Exception as e:
        log.exception("init_db failed: %s", e)
        # Don't crash the server — let the user see the error in /health.
    await _ensure_pool()
    log.info("rag-mcp ready on :%d", PORT)
    try:
        yield
    finally:
        if _pool is not None:
            await _pool.close()


app = FastAPI(title="rag-mcp", lifespan=lifespan)


class QueryRequest(BaseModel):
    question: str
    part_filter: str | None = None
    k: int = 6


class Citation(BaseModel):
    part: str
    chunk_id: str
    snippet: str
    score: float


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]


@app.get("/health")
async def health() -> dict:
    pool = await _ensure_pool()
    async with pool.acquire() as c:
        n = await c.fetchval("SELECT COUNT(*) FROM spec_chunks")
    return {"status": "ok", "spec_chunks": int(n or 0)}


@app.post("/api/specification/query", response_model=QueryResponse)
async def specification_query(req: QueryRequest) -> QueryResponse:
    pool = await _ensure_pool()
    chunks = await retriever.retrieve(
        pool, req.question, k=req.k, part_filter=req.part_filter
    )
    answer = await generator.answer(req.question, chunks)
    return QueryResponse(
        answer=answer,
        citations=[
            Citation(
                part=c.part,
                chunk_id=c.chunk_id,
                snippet=c.content[:240],
                score=c.score,
            )
            for c in chunks
        ],
    )


# ─────────── MCP/SSE (mounted under /mcp) ───────────
# Built lazily so we only depend on `mcp` at runtime, and so missing pieces
# of the SDK don't block the HTTP path.
def _mount_mcp(app: FastAPI):
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as e:
        log.warning("MCP SDK unavailable (%s); SSE endpoint disabled.", e)
        return

    mcp_server = FastMCP("rag-mcp")

    @mcp_server.tool(name="specificationQuery", description="Query the OPC UA spec corpus.")
    async def _spec_query(question: str, part_filter: str | None = None) -> dict:
        pool = await _ensure_pool()
        chunks = await retriever.retrieve(pool, question, k=6, part_filter=part_filter)
        ans = await generator.answer(question, chunks)
        return {
            "answer": ans,
            "citations": [
                {"part": c.part, "chunk_id": c.chunk_id, "snippet": c.content[:240], "score": c.score}
                for c in chunks
            ],
        }

    # FastMCP exposes both Streamable HTTP and SSE app variants.
    try:
        sse_app = mcp_server.sse_app()
        app.mount("/mcp", sse_app)
        log.info("Mounted MCP SSE app at /mcp")
    except Exception as e:
        log.warning("Could not mount MCP SSE app: %s", e)


_mount_mcp(app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
