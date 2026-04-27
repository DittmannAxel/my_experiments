"""Pure-Python rule-based anomaly detector — temperature-only for the PoC."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("anomaly")

THRESHOLD_C = 90.0
# Demo cadence with safety interlock at the same threshold: the simulator
# stops the cell the moment temp crosses 90 °C, after which motor cooling
# (~0.4 °C per 100 ms tick) drops the value back below 90 within a single
# observation. A "sustained-above" window therefore can't trip — by the
# second tick the motor is already cooling. Fire on the first observation
# above the threshold and use COOLDOWN_S as the only debouncer. 30 s
# cooldown is short enough for live-demo iteration.
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
            # Track the first-crossing time for telemetry (duration_above in
            # the event) but don't gate firing on it — see module docstring.
            if st.above_since is None:
                st.above_since = now
            if (now - st.last_fired) >= COOLDOWN_S:
                ev = AnomalyEvent(
                    axis=axis,
                    metric="motor_temperature",
                    value=motor_temp_c,
                    threshold=THRESHOLD_C,
                    duration_above=now - st.above_since,
                )
                st.last_fired = now
                log.warning("ANOMALY axis=%d temp=%.2f", axis, motor_temp_c)
                try:
                    self.queue.put_nowait(ev)
                except asyncio.QueueFull:
                    log.warning("Anomaly queue full, dropping event")
        else:
            st.above_since = None
