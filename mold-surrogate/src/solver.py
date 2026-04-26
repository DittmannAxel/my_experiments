"""
Mold Filling Solver — Hele-Shaw Eikonal Approximation

The full Hele-Shaw equation for thin-cavity injection molding:
    ∇ · (h³/(12μ) ∇p) = 0    in the filled region
    v = -(h²/(12μ)) ∇p

For weakly-compressible, isothermal filling with constant viscosity,
the time-of-arrival τ(x,y) — the fill time map — satisfies an
eikonal-like relationship:
    |∇τ| ∝ 1 / h^n           (n ≈ 2 for Hele-Shaw)

This is solved efficiently with Fast Marching Method (FMM) starting
from the gate. The result is a per-cell fill time map.

Air entrapment is identified at local maxima of τ — locations where
multiple flow fronts converge last.

This is a pedagogical approximation, NOT a Moldflow replacement:
- isothermal (no thermal boundary layer)
- Newtonian (no shear-thinning, no viscoelasticity)
- 2D (no fountain flow, no 3D effects)
- single-gate, single-material

Reference: Hieber & Shen (1980), J. Non-Newtonian Fluid Mechanics
"""
import numpy as np
import skfmm


def solve_fill_time(thickness: np.ndarray, gate_mask: np.ndarray,
                    cavity_mask: np.ndarray | None = None,
                    flow_exponent: float = 2.0) -> np.ndarray:
    """
    Compute fill time map via eikonal Fast Marching.

    Args:
        thickness: (H, W) cavity thickness in mm. Zero = no cavity.
        gate_mask: (H, W) boolean. True at injection gate cells.
        cavity_mask: optional explicit cavity mask. If None, derived from thickness>0.
        flow_exponent: how strongly thickness affects flow resistance (n=2 for Hele-Shaw).

    Returns:
        fill_time: (H, W) fill time in normalized units. Inf outside cavity.
    """
    if cavity_mask is None:
        cavity_mask = thickness > 1e-6

    # Speed function: thicker regions fill faster, scales as h^n
    # Add small floor to avoid division issues
    speed = np.where(cavity_mask, np.maximum(thickness, 1e-3) ** flow_exponent, 1e-6)

    # Sign distance from gate: negative inside the gate, positive outside
    # FMM solves |∇τ| = 1/speed starting from the zero level set
    phi = np.ones_like(thickness)
    phi[gate_mask] = -1.0

    # travel_time computes τ where |∇τ| = 1/speed
    fill_time = skfmm.travel_time(phi, speed, dx=1.0)

    # Mask out non-cavity regions
    fill_time = np.where(cavity_mask, fill_time, np.inf)
    return fill_time


def detect_air_traps(fill_time: np.ndarray, cavity_mask: np.ndarray,
                     window: int = 3) -> np.ndarray:
    """
    Detect air entrapment via local maxima of fill time.

    Where multiple flow fronts converge, that point gets filled last and
    air has nowhere to escape. We flag local maxima above a threshold.

    Returns: (H, W) probability map [0, 1] of air entrapment risk.
    """
    from scipy.ndimage import maximum_filter

    ft = np.where(np.isfinite(fill_time), fill_time, 0)
    local_max = maximum_filter(ft, size=window)
    is_max = (ft == local_max) & cavity_mask & (ft > 0)

    # Score by how late this point fills relative to global mean
    if cavity_mask.sum() == 0:
        return np.zeros_like(fill_time)
    valid_times = fill_time[cavity_mask & np.isfinite(fill_time)]
    if len(valid_times) == 0:
        return np.zeros_like(fill_time)
    p95 = np.percentile(valid_times, 95)

    risk = np.zeros_like(fill_time)
    risk[is_max] = np.clip(ft[is_max] / (p95 + 1e-6), 0, 1)
    return risk


if __name__ == "__main__":
    # Smoke test: rectangular plate, gate at left edge center, uniform thickness
    H, W = 64, 96
    thickness = np.ones((H, W)) * 2.0  # 2mm uniform
    gate_mask = np.zeros((H, W), dtype=bool)
    gate_mask[H // 2, 0] = True

    ft = solve_fill_time(thickness, gate_mask)
    print(f"Fill time range: {ft[np.isfinite(ft)].min():.3f} – {ft[np.isfinite(ft)].max():.3f}")
    print(f"Solver shape: {ft.shape}, finite cells: {np.isfinite(ft).sum()}")
