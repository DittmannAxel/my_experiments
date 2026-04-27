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


async def build_address_space(server: Server) -> AddressSpace:
    ns_primary = await server.register_namespace(NS_PRIMARY)
    ns_reco = await server.register_namespace(NS_RECOMMENDATIONS)

    objects = server.nodes.objects

    # ─────────── RobotController ───────────
    controller = await objects.add_object(ns_primary, "RobotController")

    ident = await controller.add_object(ns_primary, "Identification")
    await ident.add_variable(ns_primary, "Manufacturer", "Axel Demo Robotics")
    await ident.add_variable(ns_primary, "Model", "Demo6Axis-1")
    await ident.add_variable(ns_primary, "SerialNumber", "POC-001")

    motion = await controller.add_object(ns_primary, "MotionDevice")
    axes: dict[int, AxisNodes] = {}
    for i in range(1, 7):
        axis = await motion.add_object(ns_primary, f"Axis{i}")
        ap = await axis.add_variable(ns_primary, "ActualPosition", 0.0, ua.VariantType.Double)
        await ap.set_writable(False)
        sp = await axis.add_variable(ns_primary, "ActualSpeed", 0.0, ua.VariantType.Double)
        await sp.set_writable(False)
        at = await axis.add_variable(ns_primary, "ActualTemperature", 25.0, ua.VariantType.Double)
        await at.set_writable(False)
        mt = await axis.add_variable(ns_primary, "MotorTemperature", 28.0, ua.VariantType.Double)
        await mt.set_writable(False)
        axes[i] = AxisNodes(ap, sp, at, mt)

    tool = await controller.add_object(ns_primary, "Tool")
    gripper = await tool.add_variable(ns_primary, "GripperState", False, ua.VariantType.Boolean)
    await gripper.set_writable(False)
    payload = await tool.add_variable(ns_primary, "PayloadKg", 0.0, ua.VariantType.Double)
    await payload.set_writable(False)

    program_state = await controller.add_variable(
        ns_primary, "ProgramState", 0, ua.VariantType.Int32
    )
    await program_state.set_writable(True)  # operator/agent can flip this on approval

    cycle_counter = await controller.add_variable(
        ns_primary, "CycleCounter", 0, ua.VariantType.UInt64
    )
    await cycle_counter.set_writable(False)

    task_control = await controller.add_object(ns_primary, "TaskControl")

    @uamethod
    async def reset_maintenance(parent):  # noqa: ARG001
        await program_state.write_value(0)  # back to Idle
        return ua.StatusCode(ua.StatusCodes.Good)

    await task_control.add_method(
        ns_primary, "ResetMaintenance", reset_maintenance, [], [ua.VariantType.StatusCode]
    )

    # InjectAnomaly method (so a Node-RED button or curl can trigger demo).
    anomaly_var = await controller.add_variable(
        ns_primary, "InjectedAnomaly", "", ua.VariantType.String
    )
    await anomaly_var.set_writable(False)

    @uamethod
    async def inject_anomaly_method(parent, name: str):  # noqa: ARG001
        await anomaly_var.write_value(name or "")
        return ua.StatusCode(ua.StatusCodes.Good)

    await task_control.add_method(
        ns_primary,
        "InjectAnomaly",
        inject_anomaly_method,
        [ua.VariantType.String],
        [ua.VariantType.StatusCode],
    )

    # ─────────── RobotRecommendations ───────────
    reco_obj = await objects.add_object(ns_reco, "RobotRecommendations")

    active_reco = await reco_obj.add_variable(
        ns_reco, "ActiveRecommendation", "", ua.VariantType.String
    )
    await active_reco.set_writable(True)  # the agent writes here

    reco_count = await reco_obj.add_variable(
        ns_reco, "RecommendationCount", 0, ua.VariantType.UInt32
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
        return ua.StatusCode(ua.StatusCodes.Good)

    await reco_obj.add_method(
        ns_reco,
        "ApproveRecommendation",
        approve_recommendation,
        [ua.VariantType.String, ua.VariantType.Boolean],
        [ua.VariantType.StatusCode],
    )

    return addr
