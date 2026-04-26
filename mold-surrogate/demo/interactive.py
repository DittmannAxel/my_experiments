"""
Interactive Mold Filling Surrogate Demo (Streamlit)

Run: streamlit run demo/interactive.py

Lets the user:
- Pick from preset geometry types
- Adjust gate position with sliders
- Adjust base wall thickness
- See solver vs surrogate predictions side-by-side
- See timing comparison live

This is the artifact you screen-record for the 30-second demo clip.
"""
import sys
import time
from pathlib import Path
import numpy as np
import streamlit as st
import torch
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from geometry import generate_random_part, MoldGeometry
from solver import solve_fill_time, detect_air_traps
from dataset import encode_gate_distance
from model import UNetSurrogate


@st.cache_resource
def load_model_cached(ckpt_path: str = "models/best.pt"):
    state = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model = UNetSurrogate(**state["config"])
    model.load_state_dict(state["model"])
    model.eval()
    return model


def predict_surrogate(model, geom: MoldGeometry):
    inp_t = (geom.thickness / 5.0).astype(np.float32)
    inp_d = encode_gate_distance(geom.gate_mask, geom.cavity_mask)
    x = torch.from_numpy(np.stack([inp_t, inp_d])).unsqueeze(0)
    with torch.no_grad():
        t0 = time.perf_counter()
        y = model(x)
        elapsed = (time.perf_counter() - t0) * 1000
    log_ft = y[0, 0].numpy()
    air = y[0, 1].numpy()
    pred_ft = np.expm1(log_ft)
    pred_ft = np.where(geom.cavity_mask, pred_ft, np.nan)
    air = np.where(geom.cavity_mask, np.clip(air, 0, 1), np.nan)
    return pred_ft, air, elapsed


def make_geometry_with_overrides(seed: int, gate_y_frac: float, gate_x_frac: float,
                                   thickness_scale: float):
    geom = generate_random_part(seed=seed)
    H, W = geom.cavity_mask.shape
    # Override gate to user-selected fraction (snap to nearest cavity edge cell)
    gy_target = int(np.clip(gate_y_frac * H, 1, H - 2))
    gx_target = int(np.clip(gate_x_frac * W, 1, W - 2))
    # Find nearest cavity edge cell
    edge_cells = []
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            if geom.cavity_mask[y, x]:
                neighbors = geom.cavity_mask[y - 1:y + 2, x - 1:x + 2]
                if not neighbors.all():
                    edge_cells.append((y, x))
    if edge_cells:
        edge_cells = np.array(edge_cells)
        d = (edge_cells[:, 0] - gy_target) ** 2 + (edge_cells[:, 1] - gx_target) ** 2
        gy, gx = edge_cells[d.argmin()]
        geom.gate_mask = np.zeros_like(geom.gate_mask)
        geom.gate_mask[gy, gx] = True
        geom.metadata["gate_pos"] = (int(gy), int(gx))
    geom.thickness = (geom.thickness * thickness_scale).astype(np.float32)
    return geom


# ---- Streamlit UI ----
st.set_page_config(page_title="Mold Filling Surrogate", layout="wide")
st.title("🏭 Mold Filling Surrogate — Real-Time Injection Molding Prediction")
st.caption("Hele-Shaw eikonal solver vs. U-Net surrogate. Move the sliders, compare live.")

# Sidebar controls
st.sidebar.header("Geometry")
seed = st.sidebar.number_input("Geometry seed", min_value=0, value=42, step=1)
st.sidebar.markdown("Different seeds give different part shapes (rect / L / T / with holes).")

st.sidebar.header("Gate Position")
gate_y_frac = st.sidebar.slider("Gate Y (top → bottom)", 0.0, 1.0, 0.5, 0.05)
gate_x_frac = st.sidebar.slider("Gate X (left → right)", 0.0, 1.0, 0.05, 0.05)
st.sidebar.caption("Gate snaps to nearest cavity edge cell.")

st.sidebar.header("Process")
thickness_scale = st.sidebar.slider("Wall thickness multiplier", 0.5, 2.0, 1.0, 0.1)

# Build geometry & run both solvers
geom = make_geometry_with_overrides(seed, gate_y_frac, gate_x_frac, thickness_scale)

t0 = time.perf_counter()
ft_true = solve_fill_time(geom.thickness, geom.gate_mask, geom.cavity_mask)
air_true = detect_air_traps(ft_true, geom.cavity_mask)
solver_ms = (time.perf_counter() - t0) * 1000

model = load_model_cached()
ft_pred, air_pred, surrogate_ms = predict_surrogate(model, geom)

# Display: top row metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Solver time", f"{solver_ms:.1f} ms")
c2.metric("Surrogate time", f"{surrogate_ms:.1f} ms")
speedup = solver_ms / max(surrogate_ms, 1e-3)
c3.metric("Speedup", f"{speedup:.1f}×")
ft_max = np.nanmax(np.where(geom.cavity_mask, ft_true, np.nan))
c4.metric("Max fill time", f"{ft_max:.2f} (units)")

# Plots
ft_true_disp = np.where(geom.cavity_mask, ft_true, np.nan)
air_true_disp = np.where(geom.cavity_mask, air_true, np.nan)
vmax = np.nanmax(ft_true_disp)

fig, axes = plt.subplots(2, 3, figsize=(14, 7))

ax = axes[0, 0]
im = ax.imshow(np.where(geom.cavity_mask, geom.thickness, np.nan), cmap="cividis")
gy, gx = geom.metadata["gate_pos"]
ax.plot(gx, gy, "r*", markersize=16, markeredgecolor="white", markeredgewidth=1.5)
ax.set_title("Wall Thickness [mm] (★ = gate)")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

ax = axes[0, 1]
im = ax.imshow(ft_true_disp, cmap="viridis", vmin=0, vmax=vmax)
ax.set_title(f"Solver: Fill Time   ({solver_ms:.0f} ms)")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

ax = axes[0, 2]
im = ax.imshow(ft_pred, cmap="viridis", vmin=0, vmax=vmax)
ax.set_title(f"Surrogate: Fill Time   ({surrogate_ms:.1f} ms)")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

# Bottom row: error and air traps
err = np.abs(ft_pred - ft_true_disp) / (vmax + 1e-6) * 100
ax = axes[1, 0]
im = ax.imshow(err, cmap="hot", vmin=0, vmax=20)
mae = np.nanmean(err)
ax.set_title(f"Surrogate Error [%]   (mean={mae:.1f}%)")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

ax = axes[1, 1]
im = ax.imshow(air_true_disp, cmap="Reds", vmin=0, vmax=1)
ax.set_title("Solver: Air-Trap Risk")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

ax = axes[1, 2]
im = ax.imshow(air_pred, cmap="Reds", vmin=0, vmax=1)
ax.set_title("Surrogate: Air-Trap Risk")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

st.pyplot(fig)

st.divider()
with st.expander("ℹ️  What am I looking at?"):
    st.markdown("""
**Wall Thickness:** the physical input. Plastic part thickness varies across the cavity —
ribs are thicker, thin walls are bottlenecks. Red star = injection gate.

**Fill Time:** how long it takes molten plastic to reach each point of the cavity.
Cool colors = filled early, warm colors = filled late. The last-filled regions are
where short shots and air traps occur.

**Surrogate Error:** percent error of the neural network vs. the physics solver, normalized
by max fill time. <5% is production-ready, 5-15% is good for design exploration,
>20% means the model needs more training data or saw an out-of-distribution geometry.

**Air-Trap Risk:** locations where multiple flow fronts converge — air can be trapped here,
causing voids or burn marks in the final part. These are the points the mold designer
wants to either eliminate by re-routing the flow or fit with vents.

---

**Why this matters for manufacturing:** A real Moldflow simulation takes 5-30 minutes
per design iteration. A surrogate makes this real-time, enabling interactive design
exploration: "what if I move the gate?" "what if I make this section thinner?" —
get the answer in milliseconds instead of staring at a progress bar.
    """)
