"""
Parametric Geometry Generator

Produces synthetic injection-molded part geometries with varying:
- Outline (rectangular base with random cutouts/holes)
- Wall thickness distribution (uniform + ribs + thin sections)
- Gate location

These are NOT real CAD parts — they're pseudo-random thin-walled
geometries that exhibit the same flow characteristics as plastic parts
(thickness-dependent flow resistance, multi-front convergence).

For a production demo, swap this with: real CAD imports via Open CASCADE
or trimesh, parameterized real part families (cup, housing, bracket, etc.).
"""
import numpy as np
from dataclasses import dataclass


@dataclass
class MoldGeometry:
    thickness: np.ndarray   # (H, W) wall thickness in mm
    cavity_mask: np.ndarray # (H, W) boolean cavity region
    gate_mask: np.ndarray   # (H, W) boolean injection gate
    metadata: dict          # parameters used to generate this sample


def _add_random_holes(mask: np.ndarray, n_holes: int, rng: np.random.Generator):
    """Punch random circular holes in the cavity (e.g., bolt holes, windows)."""
    H, W = mask.shape
    for _ in range(n_holes):
        r = rng.integers(3, 8)
        cy = rng.integers(r + 2, H - r - 2)
        cx = rng.integers(r + 2, W - r - 2)
        Y, X = np.ogrid[:H, :W]
        mask[(Y - cy) ** 2 + (X - cx) ** 2 < r ** 2] = False
    return mask


def _add_thickness_ribs(thickness: np.ndarray, cavity_mask: np.ndarray,
                        n_ribs: int, rng: np.random.Generator):
    """Add stiffening ribs as thicker bands."""
    H, W = thickness.shape
    for _ in range(n_ribs):
        if rng.random() < 0.5:
            # horizontal rib
            y = rng.integers(5, H - 5)
            half_w = rng.integers(1, 3)
            extra_thickness = rng.uniform(0.5, 1.5)
            band = slice(max(0, y - half_w), min(H, y + half_w + 1))
            thickness[band, :] += extra_thickness * cavity_mask[band, :]
        else:
            # vertical rib
            x = rng.integers(5, W - 5)
            half_w = rng.integers(1, 3)
            extra_thickness = rng.uniform(0.5, 1.5)
            band = slice(max(0, x - half_w), min(W, x + half_w + 1))
            thickness[:, band] += extra_thickness * cavity_mask[:, band, None].squeeze(-1)
    return thickness


def _add_thin_section(thickness: np.ndarray, cavity_mask: np.ndarray,
                       rng: np.random.Generator):
    """Add a thin-wall section (a flow restriction — common cause of short shots)."""
    H, W = thickness.shape
    # Choose a random rectangular subregion and reduce its thickness
    y0 = rng.integers(0, H - 10)
    x0 = rng.integers(0, W - 10)
    h = rng.integers(5, 15)
    w = rng.integers(5, 15)
    factor = rng.uniform(0.3, 0.6)
    region = slice(y0, y0 + h), slice(x0, x0 + w)
    thickness[region] *= factor
    return thickness


def generate_random_part(grid_size: tuple[int, int] = (64, 96),
                          seed: int | None = None) -> MoldGeometry:
    """Generate one random parametric geometry."""
    rng = np.random.default_rng(seed)
    H, W = grid_size

    # Base cavity: full rectangle, then maybe trim corners
    cavity_mask = np.ones((H, W), dtype=bool)

    # Maybe add a corner cutout (L-shape or T-shape)
    shape_type = rng.choice(["rect", "L", "T", "rect_holes"])
    if shape_type == "L":
        cy = rng.integers(H // 3, 2 * H // 3)
        cx = rng.integers(W // 3, 2 * W // 3)
        cavity_mask[:cy, cx:] = False  # cut top-right
    elif shape_type == "T":
        thickness_arm = rng.integers(H // 4, H // 3)
        cavity_mask[:H // 2 - thickness_arm // 2, :W // 4] = False
        cavity_mask[:H // 2 - thickness_arm // 2, 3 * W // 4:] = False
    elif shape_type == "rect_holes":
        cavity_mask = _add_random_holes(cavity_mask, n_holes=rng.integers(1, 4), rng=rng)

    # Border cleanup — ensure 1px border is empty (avoids FMM edge issues)
    cavity_mask[0, :] = False
    cavity_mask[-1, :] = False
    cavity_mask[:, 0] = False
    cavity_mask[:, -1] = False

    # Base thickness
    base_t = rng.uniform(1.5, 3.5)
    thickness = np.where(cavity_mask, base_t, 0.0).astype(np.float32)

    # Add ribs
    n_ribs = rng.integers(0, 3)
    thickness = _add_thickness_ribs(thickness, cavity_mask, n_ribs=n_ribs, rng=rng)

    # Maybe add thin section
    if rng.random() < 0.5:
        thickness = _add_thin_section(thickness, cavity_mask, rng=rng)

    thickness = thickness * cavity_mask  # zero outside cavity

    # Gate location: random cavity edge cell
    edge_cells = []
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            if cavity_mask[y, x]:
                neighbors = cavity_mask[y - 1:y + 2, x - 1:x + 2]
                if not neighbors.all():
                    edge_cells.append((y, x))

    if not edge_cells:
        edge_cells = [(H // 2, 1)]
    gate_y, gate_x = edge_cells[rng.integers(0, len(edge_cells))]
    gate_mask = np.zeros((H, W), dtype=bool)
    gate_mask[gate_y, gate_x] = True

    return MoldGeometry(
        thickness=thickness,
        cavity_mask=cavity_mask,
        gate_mask=gate_mask,
        metadata={
            "shape_type": shape_type,
            "base_thickness_mm": float(base_t),
            "n_ribs": int(n_ribs),
            "gate_pos": (int(gate_y), int(gate_x)),
            "seed": seed,
        },
    )


if __name__ == "__main__":
    # Generate a few samples and report stats
    for i in range(5):
        g = generate_random_part(seed=i)
        print(f"Sample {i}: shape={g.metadata['shape_type']}, "
              f"base_t={g.metadata['base_thickness_mm']:.2f}mm, "
              f"ribs={g.metadata['n_ribs']}, "
              f"cavity_cells={g.cavity_mask.sum()}, "
              f"max_t={g.thickness.max():.2f}mm")
