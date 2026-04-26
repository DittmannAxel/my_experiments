"""
Interactive Mold Filling Surrogate Demo (Streamlit)

Run: streamlit run demo/interactive.py --server.address 0.0.0.0 --server.port 8501

Lets the user:
- Pick from preset geometry types (clickable buttons)
- Adjust gate position with sliders
- Adjust base wall thickness
- See solver vs surrogate predictions side-by-side
- See timing comparison live (large, prominent metric)

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


PRESETS = {
    "Rectangle": 100001,
    "L-bracket": 100002,
    "T-shape": 100003,
    "Plate w/ holes": 100004,
    "Stiff ribbed": 100007,
    "Thin section": 100009,
}


@st.cache_resource
def load_model_cached(ckpt_path: str = "models/best.pt"):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    state = torch.load(ckpt_path, map_location=device, weights_only=False)
    model = UNetSurrogate(**state["config"])
    model.load_state_dict(state["model"])
    model.to(device)
    model.eval()
    grid_size = tuple(state.get("grid_size", [64, 96]))
    return model, device, grid_size


def predict_surrogate(model, device, geom: MoldGeometry):
    inp_t = (geom.thickness / 5.0).astype(np.float32)
    inp_d = encode_gate_distance(geom.gate_mask, geom.cavity_mask)
    x = torch.from_numpy(np.stack([inp_t, inp_d])).unsqueeze(0).to(device)
    with torch.no_grad():
        if device == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        y = model(x)
        if device == "cuda":
            torch.cuda.synchronize()
        elapsed = (time.perf_counter() - t0) * 1000
    log_ft = y[0, 0].cpu().numpy()
    air = y[0, 1].cpu().numpy()
    pred_ft = np.expm1(log_ft)
    pred_ft = np.where(geom.cavity_mask, pred_ft, np.nan)
    air = np.where(geom.cavity_mask, np.clip(air, 0, 1), np.nan)
    return pred_ft, air, elapsed


def make_geometry_with_overrides(seed: int, gate_y_frac: float, gate_x_frac: float,
                                   thickness_scale: float, grid_size: tuple):
    geom = generate_random_part(grid_size=grid_size, seed=seed)
    H, W = geom.cavity_mask.shape
    gy_target = int(np.clip(gate_y_frac * H, 1, H - 2))
    gx_target = int(np.clip(gate_x_frac * W, 1, W - 2))
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
st.set_page_config(page_title="Mold Filling Surrogate", page_icon="🏭",
                   layout="wide", initial_sidebar_state="expanded")

# Custom CSS to make hero metrics pop
st.markdown("""
<style>
[data-testid="stMetricValue"] {
    font-size: 2.6rem;
    font-weight: 700;
}
[data-testid="stMetricLabel"] {
    font-size: 1.0rem;
    opacity: 0.85;
}
[data-testid="stMetricDelta"] {
    font-size: 1.0rem;
}
.block-container {
    padding-top: 1.5rem;
    padding-bottom: 1rem;
}
h1 {
    font-size: 2.0rem !important;
    margin-bottom: 0.2rem;
}
</style>
""", unsafe_allow_html=True)

st.title("🏭 Mold Filling Surrogate — Real-Time Injection Molding Prediction")
st.caption("Hele-Shaw eikonal solver vs. neural surrogate.  "
           "Drag sliders, pick a preset — predictions update live.")

# Initialize session state for preset
if "preset_seed" not in st.session_state:
    st.session_state.preset_seed = PRESETS["Rectangle"]

# Preset gallery (top row of buttons)
st.markdown("**Presets:**")
preset_cols = st.columns(len(PRESETS))
for col, (name, seed_val) in zip(preset_cols, PRESETS.items()):
    if col.button(name, use_container_width=True):
        st.session_state.preset_seed = seed_val

# Sidebar controls
st.sidebar.header("Geometry")
seed = st.sidebar.number_input("Random seed", min_value=0,
                                value=int(st.session_state.preset_seed), step=1)
st.sidebar.caption("Click a preset above, or type a seed.")

st.sidebar.header("Gate Position")
gate_y_frac = st.sidebar.slider("Gate Y (top → bottom)", 0.0, 1.0, 0.5, 0.05)
gate_x_frac = st.sidebar.slider("Gate X (left → right)", 0.0, 1.0, 0.05, 0.05)

st.sidebar.header("Process")
thickness_scale = st.sidebar.slider("Wall thickness multiplier", 0.5, 2.0, 1.0, 0.1)

show_air = st.sidebar.checkbox("Show air-trap maps", value=True)

# Load model
try:
    model, device, grid_size = load_model_cached()
except FileNotFoundError:
    st.error("⚠️  No trained checkpoint found at models/best.pt. "
             "Run `python src/train.py` first.")
    st.stop()

# Build geometry & run both solvers
geom = make_geometry_with_overrides(seed, gate_y_frac, gate_x_frac,
                                      thickness_scale, grid_size)

t0 = time.perf_counter()
ft_true = solve_fill_time(geom.thickness, geom.gate_mask, geom.cavity_mask)
air_true = detect_air_traps(ft_true, geom.cavity_mask)
solver_ms = (time.perf_counter() - t0) * 1000

ft_pred, air_pred, surrogate_ms = predict_surrogate(model, device, geom)

# Hero metrics — large prominent display
st.markdown("---")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Solver", f"{solver_ms:.0f} ms",
          help="scikit-fmm Hele-Shaw eikonal Fast Marching")
m2.metric("Surrogate", f"{surrogate_ms:.1f} ms",
          help=f"7.7M-param U-Net on {device.upper()}")
speedup = solver_ms / max(surrogate_ms, 1e-3)
m3.metric("Speedup", f"{speedup:.0f}×", delta=f"{speedup-1:.0f}× faster")
ft_true_disp = np.where(geom.cavity_mask, ft_true, np.nan)
ft_max = float(np.nanmax(ft_true_disp))
m4.metric("Max fill time", f"{ft_max:.1f}",
          help="Time-of-arrival in normalized units (Hele-Shaw)")
st.markdown("---")

# Plots
air_true_disp = np.where(geom.cavity_mask, air_true, np.nan)
vmax = ft_max

n_rows = 2 if show_air else 1
fig, axes = plt.subplots(n_rows, 3, figsize=(13, 4 * n_rows), facecolor="white")
if n_rows == 1:
    axes = axes[None, :]

ax = axes[0, 0]
im = ax.imshow(np.where(geom.cavity_mask, geom.thickness, np.nan), cmap="cividis")
gy, gx = geom.metadata["gate_pos"]
ax.plot(gx, gy, "*", color="red", markersize=22, markeredgecolor="white", markeredgewidth=2)
ax.set_title(f"Wall Thickness  ({geom.metadata['shape_type']}, ★ = gate)",
             fontsize=11, fontweight="bold")
plt.colorbar(im, ax=ax, fraction=0.04, label="mm")
ax.axis("off")

ax = axes[0, 1]
im = ax.imshow(ft_true_disp, cmap="viridis", vmin=0, vmax=vmax)
ax.set_title(f"Solver: Fill Time  ({solver_ms:.0f} ms)",
             fontsize=11, fontweight="bold")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

ax = axes[0, 2]
im = ax.imshow(ft_pred, cmap="viridis", vmin=0, vmax=vmax)
ax.set_title(f"Surrogate: Fill Time  ({surrogate_ms:.1f} ms)",
             fontsize=11, fontweight="bold")
plt.colorbar(im, ax=ax, fraction=0.04)
ax.axis("off")

if show_air:
    err = np.abs(ft_pred - ft_true_disp) / (vmax + 1e-6) * 100
    mae = float(np.nanmean(err))
    ax = axes[1, 0]
    im = ax.imshow(err, cmap="hot", vmin=0, vmax=20)
    ax.set_title(f"Surrogate Error [%]  (mean = {mae:.2f}%)",
                 fontsize=11, fontweight="bold")
    plt.colorbar(im, ax=ax, fraction=0.04)
    ax.axis("off")

    ax = axes[1, 1]
    im = ax.imshow(air_true_disp, cmap="Reds", vmin=0, vmax=1)
    ax.set_title("Solver: Air-Trap Risk", fontsize=11, fontweight="bold")
    plt.colorbar(im, ax=ax, fraction=0.04)
    ax.axis("off")

    ax = axes[1, 2]
    im = ax.imshow(air_pred, cmap="Reds", vmin=0, vmax=1)
    ax.set_title("Surrogate: Air-Trap Risk", fontsize=11, fontweight="bold")
    plt.colorbar(im, ax=ax, fraction=0.04)
    ax.axis("off")

plt.tight_layout()
st.pyplot(fig)

# Footer expander with explanations
with st.expander("ℹ️  What am I looking at?  /  How to read this"):
    st.markdown("""
**Wall Thickness:** physical input to the solver. Plastic part thickness varies across
the cavity — ribs are thicker, thin walls bottleneck flow. Red ★ marks the injection gate.

**Fill Time (solver):** how long molten plastic takes to reach each cavity cell, computed
by Fast Marching on an eikonal approximation of Hele-Shaw flow. Cool colors = filled
early, warm colors = filled late.

**Fill Time (surrogate):** the same field, predicted by a 7.7M-parameter U-Net trained
on 5000 randomized geometries. The point of the demo is the timing column above.

**Surrogate Error:** percent error vs. the solver, normalized by max fill time. <5% is
production-ready for design exploration; >20% means the model saw something
out-of-distribution.

**Air-Trap Risk:** locations where multiple flow fronts converge — common spots for
voids, burn marks, or short shots. Mold designers either re-route flow or add vents here.

---

**Why this matters for manufacturing:** A real Moldflow simulation takes 5–30 minutes per
design iteration. A surrogate makes that real-time, enabling a designer to interactively
ask "what if I move the gate?" or "what if I make this section thinner?" and get a useful
answer in milliseconds — instead of staring at a progress bar.

This is a research prototype with simplified physics (isothermal, Newtonian, 2D Hele-Shaw
eikonal). It is not a Moldflow replacement.
    """)

st.caption(f"Model on **{device.upper()}** · grid {grid_size[0]}×{grid_size[1]} · "
           f"hosted on 192.168.21.230 (LAN only)")
