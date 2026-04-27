"""6-axis pick-and-place trajectory + toy thermal model + anomaly injection."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import time

from asyncua import ua

from .robotics_model import AddressSpace

log = logging.getLogger("simulator")

UPDATE_HZ = 50.0
DT = 1.0 / UPDATE_HZ

# Joint waypoint sequence — six waypoints per axis, deg.
# Defines a pick-and-place loop.
WAYPOINTS = [
    [0,    -30,  60,   0,    30,   0],
    [45,   -45,  90,   0,    45,   90],
    [90,   -60,  90,   0,    60,   90],   # pickup
    [45,   -45,  90,   0,    45,   90],
    [-45,  -45,  90,   0,    45,  -90],
    [-90,  -60,  90,   0,    60,  -90],   # dropoff
]
WAYPOINT_DURATION_S = 4.0  # time between waypoints
T_AMBIENT = 22.0           # °C
T_BASE_MOTOR = 28.0        # °C, idle motor temp
THERMAL_K = 0.05           # heating per |deg/s|
THERMAL_C = 0.06           # cooling toward ambient


class Simulator:
    def __init__(self, addr: AddressSpace):
        self.addr = addr
        self._stop = asyncio.Event()
        self.t0 = time.monotonic()
        self.cycle = 0

        # Per-axis dynamic state
        self.pos = [0.0] * 6
        self.prev_pos = [0.0] * 6
        self.speed = [0.0] * 6
        self.actual_temp = [T_AMBIENT + 3.0] * 6
        self.motor_temp = [T_BASE_MOTOR] * 6

        # Anomaly state
        self.anomaly_name: str = os.environ.get("INJECT_ANOMALY", "").strip()
        self.anomaly_t_start: float | None = None
        if self.anomaly_name:
            log.info("Starting with anomaly: %s", self.anomaly_name)

    async def run(self):
        last_state_change = time.monotonic()
        program_state = 2  # Running
        await self.addr.program_state.write_value(
            ua.Variant(program_state, ua.VariantType.Int32)
        )

        while not self._stop.is_set():
            tick_start = time.monotonic()

            # Pull anomaly state from the OPC UA variable too (Node-RED can flip it).
            try:
                opc_anomaly = await self.addr.anomaly_injected.read_value()
                if opc_anomaly and opc_anomaly != self.anomaly_name:
                    self.anomaly_name = opc_anomaly
                    self.anomaly_t_start = time.monotonic()
                    log.info("Anomaly triggered via OPC UA: %s", opc_anomaly)
            except Exception:
                pass

            # Trajectory.
            t = time.monotonic() - self.t0
            seg = (t / WAYPOINT_DURATION_S)
            seg_idx = int(seg) % len(WAYPOINTS)
            seg_next = (seg_idx + 1) % len(WAYPOINTS)
            alpha = seg - int(seg)

            self.prev_pos = list(self.pos)
            for i in range(6):
                base = WAYPOINTS[seg_idx][i] * (1 - alpha) + WAYPOINTS[seg_next][i] * alpha
                jitter = math.sin(t * (0.7 + i * 0.13)) * 0.4
                self.pos[i] = base + jitter
                self.speed[i] = (self.pos[i] - self.prev_pos[i]) / DT

            # Cycle counter — increment when we wrap waypoint sequence.
            new_cycle = int(t / (WAYPOINT_DURATION_S * len(WAYPOINTS)))
            if new_cycle != self.cycle:
                self.cycle = new_cycle
                await self.addr.cycle_counter.write_value(
                    ua.Variant(new_cycle, ua.VariantType.UInt64)
                )

            # ProgramState heartbeat: cycle through Running/Stopping/Idle every ~30 s
            if time.monotonic() - last_state_change > 30.0:
                program_state = {2: 3, 3: 0, 0: 2}.get(program_state, 2)
                await self.addr.program_state.write_value(
                    ua.Variant(program_state, ua.VariantType.Int32)
                )
                last_state_change = time.monotonic()

            # Thermal model.
            for i in range(6):
                heat = THERMAL_K * abs(self.speed[i])
                cool = THERMAL_C * (self.motor_temp[i] - T_AMBIENT)
                self.motor_temp[i] += (heat - cool) * DT
                # Actual axis temp is a bit cooler than motor.
                self.actual_temp[i] = T_AMBIENT + (self.motor_temp[i] - T_AMBIENT) * 0.6

            # Anomaly injection.
            if self.anomaly_name == "axis4_overheat":
                if self.anomaly_t_start is None:
                    self.anomaly_t_start = time.monotonic()
                # Ramp axis 4 motor temp from baseline → 95 °C over 60 s
                age = time.monotonic() - self.anomaly_t_start
                target = min(95.0, T_BASE_MOTOR + (95.0 - T_BASE_MOTOR) * (age / 60.0))
                # Gently pull axis-4 motor temp up toward `target`
                self.motor_temp[3] = max(self.motor_temp[3], target + random.uniform(-0.3, 0.3))

            # Write back to OPC UA. Batch in a TaskGroup to avoid serial round-trips.
            await self._write_axes()

            # Pacing.
            elapsed = time.monotonic() - tick_start
            sleep = max(0.0, DT - elapsed)
            await asyncio.sleep(sleep)

    async def _write_axes(self):
        # asyncua doesn't have native batch write here; sequential writes are fast on local sockets.
        for i, axis in self.addr.axes.items():
            j = i - 1
            await axis.actual_position.write_value(float(self.pos[j]))
            await axis.actual_speed.write_value(float(self.speed[j]))
            await axis.actual_temperature.write_value(float(self.actual_temp[j]))
            await axis.motor_temperature.write_value(float(self.motor_temp[j]))

    def stop(self):
        self._stop.set()
