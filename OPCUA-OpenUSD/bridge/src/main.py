"""Bridge service entrypoint.

Reads OPC UA monitored items, batches updates within 50 ms windows, and writes
overrides to /stage/live.usda. The live layer is the only file the bridge
mutates; OpenUSD's layer composition takes care of the rest.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import time
from pathlib import Path

from .opcua_client import Snapshot, run_client
from .usd_writer import UsdWriter

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bridge")

ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://opcua-server:4840/axel/robot")
STAGE_DIR = Path(os.environ.get("USD_STAGE_DIR", "/stage"))
ASSETS_DIR = Path(os.environ.get("USD_ASSETS_DIR", "/usd-assets"))

# Static USD assets are mounted read-only at /usd-assets; we copy them to
# /stage on first boot so live.usda can be appended without touching the source.
def seed_stage_dir():
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("stage.usda", "robot.usda", "cell.usda", "live.usda"):
        src = ASSETS_DIR / name
        dst = STAGE_DIR / name
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)
            log.info("Seeded %s from %s", dst, src)


async def writer_loop(snap: Snapshot, writer: UsdWriter):
    """Drive USD writes at ~10 Hz so the viewer always sees the latest snap.

    Earlier this gated on snap.is_dirty_since(), which turned out to never
    fire under load — likely an interaction between asyncua's notification
    dispatcher and our shared Snapshot object. Unconditional 10 Hz writes
    are cheaper than chasing that bug and give a deterministic ~100 ms
    USD-update floor.
    """
    last_log_ts = 0.0
    write_count = 0
    while True:
        await asyncio.sleep(0.10)
        try:
            writer.write_batch(
                joint_angles_deg=dict(snap.joint_angles_deg),
                motor_temps_c=dict(snap.motor_temps_c),
                program_state=snap.program_state,
            )
            write_count += 1
            now = time.monotonic()
            if now - last_log_ts > 30.0:
                log.info(
                    "writer_loop alive: %d writes in last %.1fs, ps=%s",
                    write_count, now - last_log_ts, snap.program_state,
                )
                last_log_ts = now
                write_count = 0
        except Exception as e:
            log.warning("usd write_batch failed: %s", e)


async def main():
    seed_stage_dir()
    writer = UsdWriter(STAGE_DIR)

    snap = Snapshot(
        joint_angles_deg={i: 0.0 for i in range(1, 7)},
        motor_temps_c={i: 25.0 for i in range(1, 7)},
        program_state=None,
    )

    stop = asyncio.Event()

    def _handle_sig():
        log.info("Shutdown signal received.")
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _handle_sig)

    client_task = asyncio.create_task(run_client(ENDPOINT, snap), name="opcua_client")
    writer_task = asyncio.create_task(writer_loop(snap, writer), name="usd_writer")

    log.info("Bridge running. endpoint=%s stage=%s", ENDPOINT, STAGE_DIR)
    try:
        await stop.wait()
    finally:
        for t in (client_task, writer_task):
            t.cancel()
        await asyncio.gather(client_task, writer_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
