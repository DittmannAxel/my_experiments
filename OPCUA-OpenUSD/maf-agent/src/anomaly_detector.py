"""Pure-Python rule-based anomaly detector — temperature-only for the PoC."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("anomaly")

THRESHOLD_C = 90.0
# Tightened for live-demo cadence and to interleave with the simulator's
# safety interlock (also at 90 °C): the moment temp crosses 90, the cell
# auto-stops (ProgramState 2 → 4) and motor cooling kicks in immediately at
# THERMAL_C·(95-22) ≈ 4.4 °C/s, so the value drops back below 90 within
# ~1.1 s. A 0.5 s sustained-above window comfortably fires the agent inside
# that envelope while still suppressing single-sample noise. 30 s cooldown
# lets the operator repeat the demo without a two-minute wait.
DURATION_S = 0.5
COOLDOWN_S = 30.0


@dataclass
class AnomalyEvent:
    axis: int
    metric: str
    value: float
    threshold: float
    duration_above: float


@dataclass
class _AxisState:
    above_since: float | None = None
    last_fired: float = 0.0


class TemperatureAnomalyDetector:
    """Tracks per-axis time-above-threshold; emits AnomalyEvents to a queue."""

    def __init__(self, queue: asyncio.Queue[AnomalyEvent]):
        self.queue = queue
        self.state: dict[int, _AxisState] = {i: _AxisState() for i in range(1, 7)}

    def observe(self, axis: int, motor_temp_c: float) -> None:
        now = time.monotonic()
        st = self.state[axis]

        if motor_temp_c >= THRESHOLD_C:
            if st.above_since is None:
                st.above_since = now
            elif (now - st.above_since) >= DURATION_S \
                 and (now - st.last_fired) >= COOLDOWN_S:
                # Fire.
                ev = AnomalyEvent(
                    axis=axis,
                    metric="motor_temperature",
                    value=motor_temp_c,
                    threshold=THRESHOLD_C,
                    duration_above=now - st.above_since,
                )
                st.last_fired = now
                log.warning("ANOMALY axis=%d temp=%.2f duration=%.1fs",
                            axis, motor_temp_c, ev.duration_above)
                try:
                    self.queue.put_nowait(ev)
                except asyncio.QueueFull:
                    log.warning("Anomaly queue full, dropping event")
        else:
            st.above_since = None
