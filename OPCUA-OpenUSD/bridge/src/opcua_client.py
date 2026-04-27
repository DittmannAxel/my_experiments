"""Subscribe to all axis positions, motor temps, and ProgramState."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from asyncua import Client, ua

log = logging.getLogger("opcua_client")

NS_PRIMARY = "urn:axel:robot"


def axis_pos_path(ns: int, axis: int) -> str:
    return f"ns={ns};s=RobotController.MotionDevice.Axis{axis}.ActualPosition"


def axis_motor_path(ns: int, axis: int) -> str:
    return f"ns={ns};s=RobotController.MotionDevice.Axis{axis}.MotorTemperature"


def program_state_path(ns: int) -> str:
    return f"ns={ns};s=RobotController.ProgramState"


@dataclass
class Snapshot:
    """Latest known values, populated by the subscription handler."""
    joint_angles_deg: dict[int, float]
    motor_temps_c: dict[int, float]
    program_state: int | None
    last_change_ts: float = 0.0

    def is_dirty_since(self, ts: float) -> bool:
        return self.last_change_ts > ts


class _SubHandler:
    """Pushes incoming monitored item updates into a Snapshot."""
    def __init__(self, snap: Snapshot, label_to_setter):
        self.snap = snap
        self.label_to_setter = label_to_setter
        self._seen = 0
        self._misses = 0
        self._last_log = time.monotonic()

    def datachange_notification(self, node, val, data):  # noqa: ARG002
        nid = node.nodeid.to_string()
        setter = self.label_to_setter.get(nid)
        if setter is None:
            self._misses += 1
        else:
            try:
                setter(val)
                self.snap.last_change_ts = time.monotonic()
                self._seen += 1
            except Exception:
                log.exception("setter for %s failed", nid)
        # Periodic liveness log so we can tell apart "no notifications" from
        # "notifications arrive but routes nowhere".
        now = time.monotonic()
        if now - self._last_log > 30.0:
            log.info(
                "sub handler: %d routed, %d unrouted in last %.0fs (last nid=%s)",
                self._seen, self._misses, now - self._last_log, nid,
            )
            self._seen = 0
            self._misses = 0
            self._last_log = now


async def run_client(endpoint: str, snap: Snapshot, ns_uri: str = NS_PRIMARY):
    """Open a long-running OPC UA client; keep snap up to date until cancelled."""
    while True:
        try:
            async with Client(url=endpoint) as client:
                ns = await client.get_namespace_index(ns_uri)
                log.info("Connected to %s, primary ns=%d", endpoint, ns)

                node_setters = {}
                for i in range(1, 7):
                    nid_pos = axis_pos_path(ns, i)
                    nid_mot = axis_motor_path(ns, i)
                    node_setters[nid_pos] = (lambda v, ax=i: snap.joint_angles_deg.__setitem__(ax, float(v)))
                    node_setters[nid_mot] = (lambda v, ax=i: snap.motor_temps_c.__setitem__(ax, float(v)))
                node_setters[program_state_path(ns)] = (lambda v: setattr(snap, "program_state", int(v)))

                handler = _SubHandler(snap, node_setters)
                sub = await client.create_subscription(50, handler)

                nodes_to_sub = []
                for nid in node_setters:
                    nodes_to_sub.append(client.get_node(nid))
                await sub.subscribe_data_change(nodes_to_sub)
                log.info("Subscribed to %d items, monitoring...", len(nodes_to_sub))

                # Keep the connection alive; subscriptions push updates via handler.
                while True:
                    await asyncio.sleep(5)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:
            log.warning("OPC UA client lost (%s); reconnecting in 2 s...", e)
            await asyncio.sleep(2)
