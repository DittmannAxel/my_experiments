"""maf-agent main loop.

Subscribes OPC UA monitored items (motor temperatures + ProgramState),
runs the anomaly detector in the subscription handler, and dispatches to the
agent on detected events. Heartbeats /tmp/agent.heartbeat for healthcheck.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from pathlib import Path

from asyncua import Client

from . import agent
from .anomaly_detector import AnomalyEvent, TemperatureAnomalyDetector

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")

OPCUA_ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://opcua-server:4840/axel/robot")
HEARTBEAT_PATH = Path(os.environ.get("AGENT_HEARTBEAT_PATH", "/tmp/agent.heartbeat"))


def axis_motor_path(ns: int, axis: int) -> str:
    return f"ns={ns};s=RobotController.MotionDevice.Axis{axis}.MotorTemperature"


def program_state_path(ns: int) -> str:
    return f"ns={ns};s=RobotController.ProgramState"


class _SubHandler:
    def __init__(self, detector: TemperatureAnomalyDetector, axis_by_nid: dict[str, int]):
        self.detector = detector
        self.axis_by_nid = axis_by_nid

    def datachange_notification(self, node, val, data):  # noqa: ARG002
        nid = node.nodeid.to_string()
        ax = self.axis_by_nid.get(nid)
        if ax is None:
            return
        try:
            self.detector.observe(ax, float(val))
        except Exception:
            log.exception("detector.observe error")


async def _opcua_loop(queue: asyncio.Queue[AnomalyEvent]):
    detector = TemperatureAnomalyDetector(queue)
    while True:
        try:
            async with Client(url=OPCUA_ENDPOINT) as client:
                ns = await client.get_namespace_index("urn:axel:robot")
                axis_by_nid: dict[str, int] = {axis_motor_path(ns, i): i for i in range(1, 7)}
                handler = _SubHandler(detector, axis_by_nid)
                sub = await client.create_subscription(100, handler)
                nodes = [client.get_node(nid) for nid in axis_by_nid]
                await sub.subscribe_data_change(nodes)
                log.info("Subscribed to %d motor-temp nodes", len(nodes))
                while True:
                    HEARTBEAT_PATH.touch()
                    await asyncio.sleep(2)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:
            log.warning("OPC UA client lost (%s); reconnecting in 3 s", e)
            await asyncio.sleep(3)


async def _agent_worker(queue: asyncio.Queue[AnomalyEvent]):
    while True:
        ev = await queue.get()
        try:
            await agent.handle_anomaly(ev)
        except Exception as e:
            log.exception("Agent handler failed: %s", e)


async def main():
    HEARTBEAT_PATH.touch()
    queue: asyncio.Queue[AnomalyEvent] = asyncio.Queue(maxsize=10)

    stop = asyncio.Event()

    def _on_sig():
        log.info("Shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _on_sig)

    opc_task = asyncio.create_task(_opcua_loop(queue), name="opcua_loop")
    agent_task = asyncio.create_task(_agent_worker(queue), name="agent_worker")

    log.info("maf-agent running. endpoint=%s", OPCUA_ENDPOINT)
    try:
        await stop.wait()
    finally:
        opc_task.cancel()
        agent_task.cancel()
        await asyncio.gather(opc_task, agent_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
