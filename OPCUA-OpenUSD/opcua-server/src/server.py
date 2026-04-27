"""asyncua server — main entry."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from asyncua import Server, ua
from asyncua.server.user_managers import User, UserManager, UserRole

from .robotics_model import build_address_space
from .simulator import Simulator

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("opcua-server")

CERT_DIR = Path("/app/certs")
SERVER_CERT = CERT_DIR / "server_cert.der"
SERVER_KEY = CERT_DIR / "server_key.pem"
TRUSTED_DIR = CERT_DIR / "trusted"

OPCUA_USER = os.environ.get("OPCUA_USER", "axel")
OPCUA_PASSWORD = os.environ.get("OPCUA_PASSWORD", "changeme-please")


class StaticUserManager(UserManager):
    """Anonymous reads allowed; admin write requires OPCUA_USER/OPCUA_PASSWORD."""
    def get_user(self, iserver, username=None, password=None, certificate=None):  # noqa: ARG002
        if username is None and password is None:
            # Anonymous session — allow read-only access.
            return User(role=UserRole.User, name="anonymous")
        if username == OPCUA_USER and password == OPCUA_PASSWORD:
            return User(role=UserRole.Admin, name=username)
        return None


async def main():
    server = Server(user_manager=StaticUserManager())
    await server.init()

    server.set_endpoint("opc.tcp://0.0.0.0:4840/axel/robot")
    server.set_server_name("AxelRobotTwin")
    await server.set_application_uri("urn:axel:robot:server")

    # Security policies: None (anonymous browse) + Basic256Sha256 (Sign / SignAndEncrypt).
    server.set_security_policy([
        ua.SecurityPolicyType.NoSecurity,
        ua.SecurityPolicyType.Basic256Sha256_Sign,
        ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
    ])
    server.set_identity_tokens([
        ua.AnonymousIdentityToken,
        ua.UserNameIdentityToken,
    ])

    # Load own cert/key.
    if SERVER_CERT.exists() and SERVER_KEY.exists():
        await server.load_certificate(str(SERVER_CERT))
        await server.load_private_key(str(SERVER_KEY))
        log.info("Loaded server cert: %s", SERVER_CERT)

    # PoC: auto-trust any client cert presented (Trap 7 in BUILD.md).
    # asyncua honors a directory of trusted DERs; we add new client certs as we see them.
    TRUSTED_DIR.mkdir(parents=True, exist_ok=True)
    try:
        await server.iserver.disable_clock()  # type: ignore[attr-defined]
    except Exception:
        pass

    addr = await build_address_space(server)
    log.info(
        "Address space ready: ns_primary=%d ns_reco=%d",
        addr.ns_primary,
        addr.ns_reco,
    )

    sim = Simulator(addr)

    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received.")
        stop_event.set()
        sim.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    async with server:
        log.info("Server up at %s", "opc.tcp://0.0.0.0:4840/axel/robot")
        sim_task = asyncio.create_task(sim.run(), name="simulator")
        try:
            await stop_event.wait()
        finally:
            sim.stop()
            sim_task.cancel()
            try:
                await sim_task
            except (asyncio.CancelledError, Exception):
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
