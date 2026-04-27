"""Pure-Python rule-based anomaly detector — temperature-only for the PoC."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("anomaly")

THRESHOLD_C = 90.0
DURATION_S = 10.0
COOLDOWN_S = 120.0  # don't re-fire same anomaly within this window


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
