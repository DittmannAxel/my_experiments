"""Live dashboard service.

Subscribes to OPC UA on startup; pushes value updates to every connected
WebSocket client. Static frontend lives under /app/web and is served at /.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from asyncua import Client, ua
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dashboard")

OPCUA_ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://opcua-server:4840/axel/robot")
PORT = int(os.environ.get("PORT", "8080"))
WEB_DIR = Path(os.environ.get("WEB_DIR", "/app/web"))

PROGRAM_STATE_LABELS = {
    0: "Idle", 1: "Starting", 2: "Running", 3: "Stopping",
    4: "Stopped", 5: "Aborted", 6: "MaintenanceRequired",
}


class State:
    """In-memory live snapshot."""
    def __init__(self):
        self.connected: bool = False
        self.connected_since: float | None = None
        self.last_change_ts: float = 0.0
        self.axes: dict[int, dict] = {
            i: {"position": 0.0, "speed": 0.0, "actual_temp": 25.0, "motor_temp": 28.0}
            for i in range(1, 7)
        }
        self.program_state: int = 0
        self.cycle_counter: int = 0
        self.active_recommendation: dict | None = None
        # Rolling window of motor-temp samples per axis: (timestamp_ms, value)
        self.temp_history: dict[int, list[tuple[int, float]]] = {i: [] for i in range(1, 7)}

    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "connected_since": self.connected_since,
            "axes": self.axes,
            "program_state": self.program_state,
            "program_state_label": PROGRAM_STATE_LABELS.get(self.program_state, "Unknown"),
            "cycle_counter": self.cycle_counter,
            "active_recommendation": self.active_recommendation,
            "ts_ms": int(time.time() * 1000),
        }


state = State()
clients: set[WebSocket] = set()


async def broadcast_loop():
    """Push the latest snapshot every 250 ms to all subscribers."""
    while True:
        await asyncio.sleep(0.25)
        if not clients:
            continue
        msg = json.dumps({"type": "snapshot", "data": state.snapshot()})
        dead = []
        for ws in list(clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)


async def opcua_loop():
    """Subscribe to OPC UA monitored items and update the State on push."""
    while True:
        try:
            async with Client(url=OPCUA_ENDPOINT) as client:
                ns_p = await client.get_namespace_index("urn:axel:robot")
                ns_r = await client.get_namespace_index("urn:axel:robot:recommendations")
                state.connected = True
                state.connected_since = time.time()
                log.info("OPC UA connected, primary ns=%d, reco ns=%d", ns_p, ns_r)

                # Build node→setter map
                setters = {}
                nodes_to_sub = []
                for i in range(1, 7):
                    base = f"RobotController.MotionDevice.Axis{i}"
                    pos_id = f"ns={ns_p};s={base}.ActualPosition"
                    spd_id = f"ns={ns_p};s={base}.ActualSpeed"
                    at_id  = f"ns={ns_p};s={base}.ActualTemperature"
                    mt_id  = f"ns={ns_p};s={base}.MotorTemperature"

                    def make_setter(field, ax):
                        def _set(v):
                            state.axes[ax][field] = float(v)
                            if field == "motor_temp":
                                hist = state.temp_history[ax]
                                hist.append((int(time.time() * 1000), float(v)))
                                if len(hist) > 480:  # ~2 min @ 250ms
                                    del hist[0:len(hist) - 480]
                            state.last_change_ts = time.time()
                        return _set

                    setters[pos_id] = make_setter("position", i)
                    setters[spd_id] = make_setter("speed", i)
                    setters[at_id]  = make_setter("actual_temp", i)
                    setters[mt_id]  = make_setter("motor_temp", i)

                ps_id = f"ns={ns_p};s=RobotController.ProgramState"
                cc_id = f"ns={ns_p};s=RobotController.CycleCounter"
                reco_id = f"ns={ns_r};s=RobotRecommendations.ActiveRecommendation"
                def _set_ps(v):
                    state.program_state = int(v)
                    state.last_change_ts = time.time()
                def _set_cc(v):
                    state.cycle_counter = int(v)
                    state.last_change_ts = time.time()
                def _set_reco(v):
                    raw = str(v) if v is not None else ""
                    if raw:
                        try:
                            state.active_recommendation = json.loads(raw)
                        except Exception:
                            state.active_recommendation = {"raw": raw}
                    else:
                        state.active_recommendation = None
                    state.last_change_ts = time.time()
                setters[ps_id] = _set_ps
                setters[cc_id] = _set_cc
                setters[reco_id] = _set_reco

                for nid in setters:
                    nodes_to_sub.append(client.get_node(nid))

                class Handler:
                    def datachange_notification(self, node, val, data):  # noqa: ARG002
                        s = setters.get(node.nodeid.to_string())
                        if s is None:
                            return
                        try:
                            s(val)
                        except Exception:
                            log.exception("setter failed")

                sub = await client.create_subscription(100, Handler())
                await sub.subscribe_data_change(nodes_to_sub)
                log.info("Subscribed to %d items", len(nodes_to_sub))
                while True:
                    await asyncio.sleep(5)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:
            state.connected = False
            log.warning("OPC UA disconnect (%s); retrying in 3 s", e)
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    opc_task = asyncio.create_task(opcua_loop(), name="opcua")
    bcast_task = asyncio.create_task(broadcast_loop(), name="broadcast")
    log.info("dashboard up on :%d (OPC UA → %s)", PORT, OPCUA_ENDPOINT)
    try:
        yield
    finally:
        for t in (opc_task, bcast_task):
            t.cancel()
        await asyncio.gather(opc_task, bcast_task, return_exceptions=True)


app = FastAPI(title="rt-dashboard", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "opcua_connected": state.connected}


@app.get("/api/snapshot")
async def snapshot() -> dict:
    return state.snapshot()


@app.get("/api/temp-history")
async def temp_history() -> dict:
    return {"axes": {str(i): state.temp_history[i] for i in range(1, 7)}}


@app.post("/api/inject-anomaly")
async def inject_anomaly() -> dict:
    """Trigger axis4_overheat via OPC UA method."""
    async with Client(url=OPCUA_ENDPOINT) as c:
        ns = await c.get_namespace_index("urn:axel:robot")
        tc = c.get_node(f"ns={ns};s=RobotController.TaskControl")
        method = c.get_node(f"ns={ns};s=RobotController.TaskControl.InjectAnomaly")
        await tc.call_method(method, ua.Variant("axis4_overheat", ua.VariantType.String))
    return {"status": "ok"}


@app.post("/api/clear-anomaly")
async def clear_anomaly() -> dict:
    async with Client(url=OPCUA_ENDPOINT) as c:
        ns = await c.get_namespace_index("urn:axel:robot")
        tc = c.get_node(f"ns={ns};s=RobotController.TaskControl")
        method = c.get_node(f"ns={ns};s=RobotController.TaskControl.InjectAnomaly")
        await tc.call_method(method, ua.Variant("", ua.VariantType.String))
    return {"status": "ok"}


@app.post("/api/reset")
async def reset() -> dict:
    """Recover from MaintenanceRequired/Aborted by clearing the active
    recommendation, clearing any anomaly, and writing ProgramState=2 (Running).
    The simulator picks up the external write and resumes the trajectory.
    """
    async with Client(url=OPCUA_ENDPOINT) as c:
        ns = await c.get_namespace_index("urn:axel:robot")
        ns_r = await c.get_namespace_index("urn:axel:robot:recommendations")
        # Clear anomaly (so the agent doesn't immediately re-recommend).
        method_inj = c.get_node(f"ns={ns};s=RobotController.TaskControl.InjectAnomaly")
        tc = c.get_node(f"ns={ns};s=RobotController.TaskControl")
        try:
            await tc.call_method(method_inj, ua.Variant("", ua.VariantType.String))
        except Exception:
            pass
        # Clear active recommendation.
        try:
            active = c.get_node(f"ns={ns_r};s=RobotRecommendations.ActiveRecommendation")
            await active.write_value(ua.Variant("", ua.VariantType.String))
        except Exception:
            pass
        # Force ProgramState back to Running (=2). The simulator now honors
        # external writes from any state, so this resumes motion.
        ps = c.get_node(f"ns={ns};s=RobotController.ProgramState")
        await ps.write_value(ua.Variant(2, ua.VariantType.Int32))
    return {"status": "ok"}


@app.post("/api/approve")
async def approve(approved: bool = True) -> dict:
    async with Client(url=OPCUA_ENDPOINT) as c:
        ns_r = await c.get_namespace_index("urn:axel:robot:recommendations")
        obj = c.get_node(f"ns={ns_r};s=RobotRecommendations")
        method = c.get_node(f"ns={ns_r};s=RobotRecommendations.ApproveRecommendation")
        await obj.call_method(
            method,
            ua.Variant("current", ua.VariantType.String),
            ua.Variant(bool(approved), ua.VariantType.Boolean),
        )
    return {"status": "ok", "approved": approved}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        # send initial snapshot immediately
        await websocket.send_text(json.dumps({"type": "snapshot", "data": state.snapshot()}))
        while True:
            # accept pings or commands
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)


# Static frontend (served LAST so /api/* and /ws win above)
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
