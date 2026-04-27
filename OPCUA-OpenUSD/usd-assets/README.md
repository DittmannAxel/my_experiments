# USD Assets — robot cell

OpenUSD layer composition for the demo cell.

## Layer stack

`stage.usda` is the root. Strongest-on-top ordering:

```
live.usda    ← strongest. Bridge writes "over" defs here. Bridge is the
              ONLY writer to this file (BUILD.md decision #3).
robot.usda   ← static rig: 6 jointed Xforms with xformOp:rotateXYZ.
cell.usda    ← static cell geometry: floor, two stations, lights.
```

Composing in this order means runtime overrides from the bridge always win.

## Joint convention

```
/World/Robot/Base/Joint1/Link1/Joint2/Link2/.../Joint6/Tool
```

Each `JointN` is an `Xform` with default rotation (0,0,0). The bridge updates
`xformOp:rotateXYZ` on each `JointN` based on OPC UA `Axis<N>.ActualPosition`.

The mapping from OPC UA axes to rotation axes (which Cartesian axis the joint
spins around) is documented in `bridge/src/usd_writer.py`.

## Display color

The bridge also sets `primvars:displayColor` on each `LinkN/Geo` mesh as a
proxy for motor temperature: cool = blue/grey, hot (≥90 °C) = red.

## Files

| File | Purpose | Owner |
|---|---|---|
| `stage.usda` | Root, sublayers | static |
| `robot.usda` | 6-axis rig + tool | static |
| `cell.usda` | Floor, stations, fence, lights | static |
| `live.usda` | Bridge runtime overrides | bridge service ONLY |
