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
# Safety interlock: any axis crossing this limit while Running auto-transitions
# the cell to Stopped (4). Same value as the anomaly-detector threshold so the
# auto-stop and the agent's recommendation arrive within the same demo beat.
SAFETY_THRESHOLD_C = 90.0


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

            # Pull anomaly state from the OPC UA variable too (the dashboard's
            # "Inject" button or any external client can flip it via the
            # InjectAnomaly method).
            try:
                opc_anomaly = await self.addr.anomaly_injected.read_value()
                if opc_anomaly and opc_anomaly != self.anomaly_name:
                    self.anomaly_name = opc_anomaly
                    self.anomaly_t_start = time.monotonic()
                    log.info("Anomaly triggered via OPC UA: %s", opc_anomaly)
            except Exception:
                pass

            # Trajectory advances ONLY while ProgramState is Running (=2).
            # In Idle (0) / Stopping (3) / Stopped (4) / Aborted (5) /
            # MaintenanceRequired (6) the joints hold their last position so
            # the operator UI is consistent with what the controller reports.
            #
            # We track a "motion clock" t_motion that only ticks in Running,
            # so the trajectory resumes from where it paused on the next
            # transition back to Running.
            now = time.monotonic()
            dt_real = now - getattr(self, "_last_tick", now)
            self._last_tick = now

            # Latch semantics:
            #   - heartbeat states {2 Running, 3 Stopping, 0 Idle} freely accept
            #     external transitions (agent flow → 6 MaintenanceRequired)
            #   - latched states {4 Stopped, 5 Aborted, 6 MaintenanceRequired}
            #     ignore direct ProgramState writes — recovery is only via
            #     ResetMaintenance, which sets `addr.reset_event` and writes 2
            # This addresses an adversarial-review finding: a bare
            # external-write recovery path silently overrode the maintenance
            # halt. The Reset event is the single authoritative path out.
            try:
                if self.addr.reset_event.is_set():
                    # Operator reset: cool the thermal model back to baseline,
                    # accept the new ProgramState, then clear the flag.
                    self.motor_temp = [T_BASE_MOTOR] * 6
                    self.actual_temp = [T_AMBIENT + 3.0] * 6
                    program_state = 2
                    last_state_change = now
                    self.anomaly_name = ""
                    self.anomaly_t_start = None
                    self.addr.reset_event.clear()
                    log.info("Operator reset — thermal state baselined, ProgramState=2")
                else:
                    ext_ps = int(await self.addr.program_state.read_value())
                    if ext_ps != program_state:
                        # Heartbeat states {2 Running, 3 Stopping, 0 Idle}
                        # accept any external transition. State {4 Stopped}
                        # additionally accepts a transition to {6
                        # MaintenanceRequired} — this is the operator-approve
                        # path: safety auto-stopped the cell, the agent filed
                        # a recommendation, the operator acknowledges by
                        # writing 6. States {5 Aborted, 6 MaintenanceRequired}
                        # are otherwise latched until the next reset_event.
                        if program_state in (2, 3, 0):
                            program_state = ext_ps
                            last_state_change = now
                        elif program_state == 4 and ext_ps == 6:
                            program_state = ext_ps
                            last_state_change = now
            except Exception:
                pass

            is_running = program_state == 2
            if is_running:
                self._t_motion = getattr(self, "_t_motion", 0.0) + dt_real

            t = getattr(self, "_t_motion", 0.0)
            seg = (t / WAYPOINT_DURATION_S)
            seg_idx = int(seg) % len(WAYPOINTS)
            seg_next = (seg_idx + 1) % len(WAYPOINTS)
            alpha = seg - int(seg)

            self.prev_pos = list(self.pos)
            for i in range(6):
                if is_running:
                    base = WAYPOINTS[seg_idx][i] * (1 - alpha) + WAYPOINTS[seg_next][i] * alpha
                    jitter = math.sin(t * (0.7 + i * 0.13)) * 0.4
                    self.pos[i] = base + jitter
                    self.speed[i] = (self.pos[i] - self.prev_pos[i]) / DT
                else:
                    # Hold position; speed → 0.
                    self.speed[i] = 0.0

            # Cycle counter only increments while running.
            if is_running:
                new_cycle = int(t / (WAYPOINT_DURATION_S * len(WAYPOINTS)))
                if new_cycle != self.cycle:
                    self.cycle = new_cycle
                    await self.addr.cycle_counter.write_value(
                        ua.Variant(new_cycle, ua.VariantType.UInt64)
                    )

            # ProgramState heartbeat. The cycle is 2→3→0→2; we stay long in
            # Running (= moving) and short in Stopping / Idle so the demo
            # spends most of its wall-clock visibly producing.
            #   Running   → 60 s
            #   Stopping  →  4 s
            #   Idle      →  4 s
            duration = {2: 60.0, 3: 4.0, 0: 4.0}.get(program_state, 60.0)
            if program_state in (2, 3, 0) and (now - last_state_change) > duration:
                program_state = {2: 3, 3: 0, 0: 2}.get(program_state, 2)
                await self.addr.program_state.write_value(
                    ua.Variant(program_state, ua.VariantType.Int32)
                )
                last_state_change = now

            # Thermal model.
            for i in range(6):
                heat = THERMAL_K * abs(self.speed[i])
                cool = THERMAL_C * (self.motor_temp[i] - T_AMBIENT)
                self.motor_temp[i] += (heat - cool) * DT
                # Actual axis temp is a bit cooler than motor.
                self.actual_temp[i] = T_AMBIENT + (self.motor_temp[i] - T_AMBIENT) * 0.6

            # Anomaly injection. Only clamps the temp upward while the robot
            # is actually moving — physically the motor heats from current
            # under load, so when the operator stops the cell (ProgramState=6
            # MaintenanceRequired) the standard cooling term takes over and
            # temp drops back toward ambient. Without this gate, "Stop" did
            # nothing visible, which is misleading.
            if self.anomaly_name == "axis4_overheat" and is_running:
                if self.anomaly_t_start is None:
                    self.anomaly_t_start = time.monotonic()
                # Ramp axis 4 motor temp baseline → 95 °C over 8 s. Demo
                # cadence: a 60 s ramp made the audience wait through over a
                # minute of climbing temperature before the anomaly detector
                # triggered the agent. 8 s is enough for the curve to look
                # like a ramp and not a step.
                age = time.monotonic() - self.anomaly_t_start
                target = min(95.0, T_BASE_MOTOR + (95.0 - T_BASE_MOTOR) * (age / 8.0))
                # Pull axis-4 motor temp up toward `target`.
                self.motor_temp[3] = max(self.motor_temp[3], target + random.uniform(-0.3, 0.3))

            # Safety interlock: any axis crossing SAFETY_THRESHOLD_C while the
            # cell is producing auto-transitions ProgramState to 4 (Stopped).
            # This is independent of the agent flow — the cell halts before
            # the agent files its recommendation, and stays halted (latched)
            # until either the operator approves the recommendation
            # (4 → 6 MaintenanceRequired) or hits Reset (4/6 → 2 Running).
            if program_state == 2 and any(t >= SAFETY_THRESHOLD_C for t in self.motor_temp):
                hot = [(i + 1, self.motor_temp[i]) for i in range(6) if self.motor_temp[i] >= SAFETY_THRESHOLD_C]
                program_state = 4
                last_state_change = now
                await self.addr.program_state.write_value(
                    ua.Variant(program_state, ua.VariantType.Int32)
                )
                log.warning(
                    "SAFETY INTERLOCK: axis(es) %s exceeded %.0f °C — auto-stop, ProgramState=4",
                    hot, SAFETY_THRESHOLD_C,
                )

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
