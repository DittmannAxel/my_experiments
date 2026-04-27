"""Mutate /stage/live.usda with override defs derived from OPC UA values."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from pxr import Gf, Sdf, Vt

log = logging.getLogger("usd_writer")

# Joint axis convention — must match docs in usd-assets/robot.usda.
# Index 0..5 maps to OPC UA Axis 1..6.
ROT_AXIS_INDEX = {
    1: 1,  # Joint1 -> Y (yaw)
    2: 0,  # Joint2 -> X (pitch)
    3: 0,  # Joint3 -> X (pitch)
    4: 2,  # Joint4 -> Z (roll)
    5: 0,  # Joint5 -> X (pitch)
    6: 2,  # Joint6 -> Z (roll)
}

# Path under robot.usda for each joint.
def joint_prim_path(axis: int) -> str:
    base = "/World/Robot/Base"
    chain = "/Joint1"
    if axis == 1:
        return f"{base}{chain}"
    if axis == 2:
        return f"{base}/Joint1/Joint2"
    if axis == 3:
        return f"{base}/Joint1/Joint2/Joint3"
    if axis == 4:
        return f"{base}/Joint1/Joint2/Joint3/Joint4"
    if axis == 5:
        return f"{base}/Joint1/Joint2/Joint3/Joint4/Joint5"
    if axis == 6:
        return f"{base}/Joint1/Joint2/Joint3/Joint4/Joint5/Joint6"
    raise ValueError(f"axis must be 1..6, got {axis}")


def link_prim_path(axis: int) -> str:
    return f"{joint_prim_path(axis)}/Link{axis}"


STATUS_PAD_PATH = "/World/Cell/StatusPad"


def temp_to_color(temp_c: float) -> tuple[float, float, float]:
    """Lerp from cool grey (≤30 °C) → red (≥90 °C)."""
    t = max(0.0, min(1.0, (temp_c - 30.0) / 60.0))
    return (
        0.55 + 0.40 * t,         # R rises
        0.58 - 0.45 * t,         # G falls
        0.62 - 0.55 * t,         # B falls
    )


class UsdWriter:
    """Owns the live.usda Sdf.Layer; updates it under change blocks."""

    def __init__(self, stage_dir: Path):
        self.stage_dir = stage_dir
        self.live_path = stage_dir / "live.usda"
        # Make sure live.usda exists with a minimal header.
        if not self.live_path.exists():
            self.live_path.write_text(
                "#usda 1.0\n(\n    defaultPrim = \"World\"\n)\n"
            )
        self.layer = Sdf.Layer.FindOrOpen(str(self.live_path))
        if self.layer is None:
            raise RuntimeError(f"Could not open USD live layer: {self.live_path}")
        log.info("Opened live layer: %s", self.live_path)

    def write_batch(
        self,
        joint_angles_deg: dict[int, float],
        motor_temps_c: dict[int, float],
        program_state: int | None,
    ) -> None:
        """Apply overrides for all axes in one ChangeBlock and Save once."""
        with Sdf.ChangeBlock():
            # Joint rotations.
            for axis, deg in joint_angles_deg.items():
                self._set_joint_rotation(axis, deg)
            # Link colors from temperature.
            for axis, temp in motor_temps_c.items():
                self._set_link_color(axis, temp)
            # Status pad color from program state (6 = MaintenanceRequired → red)
            if program_state is not None:
                self._set_status_pad(program_state)

        self.layer.Save()
        # Heartbeat for the bridge container healthcheck.
        (self.stage_dir / ".bridge.heartbeat").touch()

    def _set_joint_rotation(self, axis: int, deg: float) -> None:
        prim_path = Sdf.Path(joint_prim_path(axis))
        self._ensure_over_chain(prim_path)
        spec = self.layer.GetPrimAtPath(prim_path)

        attr_name = "xformOp:rotateXYZ"
        attr_path = prim_path.AppendProperty(attr_name)
        attr_spec = self.layer.GetAttributeAtPath(attr_path)
        if attr_spec is None:
            attr_spec = Sdf.AttributeSpec(
                spec, attr_name, Sdf.ValueTypeNames.Float3
            )
        rot = [0.0, 0.0, 0.0]
        rot[ROT_AXIS_INDEX[axis]] = float(deg)
        # Use Gf.Vec3f explicitly. Previously assigned a plain Python tuple
        # which Sdf accepted on first write but then refused to mutate on
        # subsequent writes for the same attr_spec — the layer's default
        # would stay frozen at the first value.
        attr_spec.default = Gf.Vec3f(rot[0], rot[1], rot[2])

    def _set_link_color(self, axis: int, temp_c: float) -> None:
        prim_path = Sdf.Path(link_prim_path(axis))
        self._ensure_over_chain(prim_path)
        spec = self.layer.GetPrimAtPath(prim_path)
        attr_name = "primvars:displayColor"
        attr_spec = self.layer.GetAttributeAtPath(prim_path.AppendProperty(attr_name))
        if attr_spec is None:
            attr_spec = Sdf.AttributeSpec(
                spec, attr_name, Sdf.ValueTypeNames.Color3fArray
            )
        rgb = temp_to_color(temp_c)
        attr_spec.default = Vt.Vec3fArray([tuple(rgb)])

    def _set_status_pad(self, program_state: int) -> None:
        prim_path = Sdf.Path(STATUS_PAD_PATH)
        self._ensure_over_chain(prim_path)
        spec = self.layer.GetPrimAtPath(prim_path)
        attr_name = "primvars:displayColor"
        attr_spec = self.layer.GetAttributeAtPath(prim_path.AppendProperty(attr_name))
        if attr_spec is None:
            attr_spec = Sdf.AttributeSpec(
                spec, attr_name, Sdf.ValueTypeNames.Color3fArray
            )
        # 6 = MaintenanceRequired → red, 5 = Aborted → orange, otherwise green
        if program_state == 6:
            rgb = (0.95, 0.20, 0.20)
        elif program_state == 5:
            rgb = (0.95, 0.55, 0.10)
        else:
            rgb = (0.18, 0.78, 0.18)
        attr_spec.default = Vt.Vec3fArray([rgb])

    def _ensure_over_chain(self, prim_path: Sdf.Path) -> None:
        """Walk from the root, creating Sdf.SpecifierOver primSpecs as needed."""
        parts = prim_path.pathString.strip("/").split("/")
        running = ""
        parent_spec = self.layer.pseudoRoot
        for part in parts:
            running = running + "/" + part
            cur = self.layer.GetPrimAtPath(Sdf.Path(running))
            if cur is None:
                cur = Sdf.PrimSpec(parent_spec, part, Sdf.SpecifierOver)
            parent_spec = cur
