"""OPC UA address space — Robotics Companion-spec-flavored 6-axis robot.

Two namespaces:
- urn:axel:robot           — primary process variables
- urn:axel:robot:recommendations — agent-written advisory state
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from asyncua import Server, ua
from asyncua.common.methods import uamethod

NS_PRIMARY = "urn:axel:robot"
NS_RECOMMENDATIONS = "urn:axel:robot:recommendations"

PROGRAM_STATE_LABELS = {
    0: "Idle",
    1: "Starting",
    2: "Running",
    3: "Stopping",
    4: "Stopped",
    5: "Aborted",
    6: "MaintenanceRequired",
}


@dataclass
class AxisNodes:
    actual_position: object
    actual_speed: object
    actual_temperature: object
    motor_temperature: object


@dataclass
class AddressSpace:
    """Handle bag for nodes the simulator and agent write to."""
    ns_primary: int
    ns_reco: int

    program_state: object
    cycle_counter: object
    gripper_state: object
    payload_kg: object

    axes: dict[int, AxisNodes] = field(default_factory=dict)

    active_recommendation: object = None
    recommendation_count: object = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_approval: dict | None = None

    anomaly_injected: object = None  # variable, set by InjectAnomaly method

    # Set by ResetMaintenance; the simulator consumes this on its next tick to
    # reset thermal state + force ProgramState back to Running, then clears it.
    # This is the single authoritative recovery path out of MaintenanceRequired
    # (=6) / Aborted (=5) — direct external writes to ProgramState in those
    # latched states are otherwise ignored.
    reset_event: asyncio.Event = field(default_factory=asyncio.Event)


def _nid(ns: int, path: str) -> ua.NodeId:
    """Stable string NodeId so external clients (dashboard, Telegraf, agent) can
    address nodes by name instead of relying on auto-allocated numeric IDs."""
    return ua.NodeId(path, ns, ua.NodeIdType.String)


async def build_address_space(server: Server) -> AddressSpace:
    ns_primary = await server.register_namespace(NS_PRIMARY)
    ns_reco = await server.register_namespace(NS_RECOMMENDATIONS)

    objects = server.nodes.objects

    # ─────────── RobotController ───────────
    controller = await objects.add_object(_nid(ns_primary, "RobotController"), "RobotController")

    ident = await controller.add_object(
        _nid(ns_primary, "RobotController.Identification"), "Identification"
    )
    await ident.add_variable(
        _nid(ns_primary, "RobotController.Identification.Manufacturer"),
        "Manufacturer", "Axel Demo Robotics",
    )
    await ident.add_variable(
        _nid(ns_primary, "RobotController.Identification.Model"),
        "Model", "Demo6Axis-1",
    )
    await ident.add_variable(
        _nid(ns_primary, "RobotController.Identification.SerialNumber"),
        "SerialNumber", "POC-001",
    )

    motion = await controller.add_object(
        _nid(ns_primary, "RobotController.MotionDevice"), "MotionDevice"
    )
    axes: dict[int, AxisNodes] = {}
    for i in range(1, 7):
        ax_path = f"RobotController.MotionDevice.Axis{i}"
        axis = await motion.add_object(_nid(ns_primary, ax_path), f"Axis{i}")
        ap = await axis.add_variable(
            _nid(ns_primary, f"{ax_path}.ActualPosition"),
            "ActualPosition", 0.0, ua.VariantType.Double,
        )
        await ap.set_writable(False)
        sp = await axis.add_variable(
            _nid(ns_primary, f"{ax_path}.ActualSpeed"),
            "ActualSpeed", 0.0, ua.VariantType.Double,
        )
        await sp.set_writable(False)
        at = await axis.add_variable(
            _nid(ns_primary, f"{ax_path}.ActualTemperature"),
            "ActualTemperature", 25.0, ua.VariantType.Double,
        )
        await at.set_writable(False)
        mt = await axis.add_variable(
            _nid(ns_primary, f"{ax_path}.MotorTemperature"),
            "MotorTemperature", 28.0, ua.VariantType.Double,
        )
        await mt.set_writable(False)
        axes[i] = AxisNodes(ap, sp, at, mt)

    tool = await controller.add_object(
        _nid(ns_primary, "RobotController.Tool"), "Tool"
    )
    gripper = await tool.add_variable(
        _nid(ns_primary, "RobotController.Tool.GripperState"),
        "GripperState", False, ua.VariantType.Boolean,
    )
    await gripper.set_writable(False)
    payload = await tool.add_variable(
        _nid(ns_primary, "RobotController.Tool.PayloadKg"),
        "PayloadKg", 0.0, ua.VariantType.Double,
    )
    await payload.set_writable(False)

    program_state = await controller.add_variable(
        _nid(ns_primary, "RobotController.ProgramState"),
        "ProgramState", 0, ua.VariantType.Int32,
    )
    await program_state.set_writable(True)

    cycle_counter = await controller.add_variable(
        _nid(ns_primary, "RobotController.CycleCounter"),
        "CycleCounter", 0, ua.VariantType.UInt64,
    )
    await cycle_counter.set_writable(False)

    task_control = await controller.add_object(
        _nid(ns_primary, "RobotController.TaskControl"), "TaskControl"
    )

    # Forward declaration — `addr` is constructed below; we close over it lazily.
    @uamethod
    async def reset_maintenance(parent):  # noqa: ARG001
        # Single authoritative recovery: clear anomaly, drop active
        # recommendation, signal the simulator to thermal-reset, then write
        # ProgramState=2 (Running). The simulator's gate honors this transition
        # only because `reset_event` is set; without it, latched states stay
        # latched. See simulator.py:run() for the consumer side.
        try:
            await anomaly_var.write_value(ua.Variant("", ua.VariantType.String))
        except Exception:
            pass
        try:
            await active_reco.write_value(ua.Variant("", ua.VariantType.String))
        except Exception:
            pass
        addr.reset_event.set()
        await program_state.write_value(ua.Variant(2, ua.VariantType.Int32))
        return ua.StatusCode(ua.StatusCodes.Good)

    await task_control.add_method(
        _nid(ns_primary, "RobotController.TaskControl.ResetMaintenance"),
        "ResetMaintenance",
        reset_maintenance,
        [],
        [ua.VariantType.StatusCode],
    )

    anomaly_var = await controller.add_variable(
        _nid(ns_primary, "RobotController.InjectedAnomaly"),
        "InjectedAnomaly", "", ua.VariantType.String,
    )
    await anomaly_var.set_writable(False)

    @uamethod
    async def inject_anomaly_method(parent, name: str):  # noqa: ARG001
        await anomaly_var.write_value(name or "")
        return ua.StatusCode(ua.StatusCodes.Good)

    await task_control.add_method(
        _nid(ns_primary, "RobotController.TaskControl.InjectAnomaly"),
        "InjectAnomaly",
        inject_anomaly_method,
        [ua.VariantType.String],
        [ua.VariantType.StatusCode],
    )

    # ─────────── RobotRecommendations ───────────
    reco_obj = await objects.add_object(
        _nid(ns_reco, "RobotRecommendations"), "RobotRecommendations"
    )

    active_reco = await reco_obj.add_variable(
        _nid(ns_reco, "RobotRecommendations.ActiveRecommendation"),
        "ActiveRecommendation", "", ua.VariantType.String,
    )
    await active_reco.set_writable(True)

    reco_count = await reco_obj.add_variable(
        _nid(ns_reco, "RobotRecommendations.RecommendationCount"),
        "RecommendationCount", 0, ua.VariantType.UInt32,
    )
    await reco_count.set_writable(True)

    addr = AddressSpace(
        ns_primary=ns_primary,
        ns_reco=ns_reco,
        program_state=program_state,
        cycle_counter=cycle_counter,
        gripper_state=gripper,
        payload_kg=payload,
        axes=axes,
        active_recommendation=active_reco,
        recommendation_count=reco_count,
        anomaly_injected=anomaly_var,
    )

    @uamethod
    async def approve_recommendation(parent, reco_id: str, approved: bool):  # noqa: ARG001
        addr.last_approval = {"id": reco_id, "approved": approved}
        addr.approval_event.set()
        if not approved:
            # Reject — clear recommendation, do nothing else.
            try:
                await active_reco.write_value(
                    ua.Variant("", ua.VariantType.String)
                )
            except Exception:
                pass
            return ua.StatusCode(ua.StatusCodes.Good)

        # Approved — parse the active recommendation and apply its actions.
        import json
        try:
            blob = await active_reco.read_value()
            if not blob:
                return ua.StatusCode(ua.StatusCodes.BadInvalidState)
            payload = json.loads(blob)
        except Exception:
            return ua.StatusCode(ua.StatusCodes.BadInvalidState)

        for action in payload.get("actions", []):
            node_id = action.get("node_id")
            value = action.get("value")
            if not node_id:
                continue
            # Map browse-path style (RobotController.ProgramState) to a node.
            if node_id == "RobotController.ProgramState":
                target = program_state
                vt = ua.VariantType.Int32
                v = ua.Variant(int(value), vt)
            else:
                # Unknown action target — skip silently for PoC.
                continue
            try:
                await target.write_value(v)
            except Exception:
                pass

        # Mark the recommendation as approved (rewrite as approved=true).
        try:
            payload["approved"] = True
            await active_reco.write_value(
                ua.Variant(json.dumps(payload), ua.VariantType.String)
            )
        except Exception:
            pass

        return ua.StatusCode(ua.StatusCodes.Good)

    await reco_obj.add_method(
        _nid(ns_reco, "RobotRecommendations.ApproveRecommendation"),
        "ApproveRecommendation",
        approve_recommendation,
        [ua.VariantType.String, ua.VariantType.Boolean],
        [ua.VariantType.StatusCode],
    )

    return addr
