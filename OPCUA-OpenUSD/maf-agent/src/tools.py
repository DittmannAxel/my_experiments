"""Tool implementations available to the agent."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import httpx
from asyncua import Client, ua

log = logging.getLogger("tools")

RAG_URL = os.environ.get("RAG_URL", "http://rag-mcp:49322/api/specification/query")
OPCUA_ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://opcua-server:4840/axel/robot")
OPCUA_USER = os.environ.get("OPCUA_USER", "axel")
OPCUA_PASSWORD = os.environ.get("OPCUA_PASSWORD", "changeme-please")


async def query_specification(question: str, part_filter: str | None = None) -> dict:
    """HTTP call to rag-mcp."""
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(RAG_URL, json={"question": question, "part_filter": part_filter})
        r.raise_for_status()
        return r.json()


async def write_recommendation_to_opcua(
    *,
    title: str,
    rationale: str,
    actions: list[dict],
    spec_citation: str | None = None,
) -> str:
    """Publish a recommendation to the OPC UA RobotRecommendations namespace."""
    payload = {
        "id": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "rationale": rationale,
        "actions": actions,
        "spec_citation": spec_citation,
        "approved": False,
    }
    blob = json.dumps(payload, ensure_ascii=False)

    async with Client(url=OPCUA_ENDPOINT) as client:
        # Use authenticated session so we have write rights.
        client.set_user(OPCUA_USER)
        client.set_password(OPCUA_PASSWORD)
        await client.connect()  # not needed inside async-with but harmless
        ns = await client.get_namespace_index("urn:axel:robot:recommendations")
        node = client.get_node(f"ns={ns};s=RobotRecommendations.ActiveRecommendation")
        await node.write_value(ua.Variant(blob, ua.VariantType.String))
        log.info("Wrote recommendation: %s", title)
    return f"Recommendation '{title}' published to OPC UA. Awaiting operator approval."
