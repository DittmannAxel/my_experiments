"""
Demo: Solver vs Surrogate Side-by-Side

Generates a figure showing for several test geometries:
  Row 1: input (thickness, gate distance)
  Row 2: solver ground truth (fill time, air risk)
  Row 3: surrogate prediction
  Row 4: error map

Saves PNG. Also prints timing comparison.
"""
import sys
import time
from pathlib import Path
import numpy as np
import torch
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from geometry import generate_random_part
from solver import solve_fill_time, detect_air_traps
from dataset import encode_gate_distance
from model import UNetSurrogate


def load_model(ckpt_path: Path, device: str = "cpu"):
    state = torch.load(ckpt_path, map_location=device, weights_only=False)
    model = UNetSurrogate(**state["config"])
    model.load_state_dict(state["model"])
    model.to(device)
    model.eval()
    grid_size = tuple(state.get("grid_size", [64, 96]))
    return model, grid_size


def predict(model: UNetSurrogate, thickness: np.ndarray, gate_mask: np.ndarray,
            cavity_mask: np.ndarray, device: str = "cpu"):
    inp_t = (thickness / 5.0).astype(np.float32)
    inp_d = encode_gate_distance(gate_mask, cavity_mask)
    x = torch.from_numpy(np.stack([inp_t, inp_d])).unsqueeze(0).to(device)
    with torch.no_grad():
        t0 = time.perf_counter()
        y = model(x)
        elapsed_ms = (time.perf_counter() - t0) * 1000
    log_ft = y[0, 0].cpu().numpy()
    air = y[0, 1].cpu().numpy()
    pred_ft = np.expm1(log_ft)
    pred_ft = np.where(cavity_mask, pred_ft, np.nan)
    air = np.where(cavity_mask, np.clip(air, 0, 1), np.nan)
    return pred_ft, air, elapsed_ms


def make_figure(model, n_samples: int = 4, output_path: Path = Path("assets/comparison.png"),
                seed_start: int = 1000, device: str = "cpu",
                grid_size: tuple = (64, 96)):
    fig, axes = plt.subplots(n_samples, 6, figsize=(18, 3 * n_samples))
    if n_samples == 1:
        axes = axes[None, :]

    cmap_ft = "viridis"
    cmap_air = "Reds"

    # Warmup the surrogate so the first timed call doesn't include lazy init
    _warm_geom = generate_random_part(grid_size=grid_size, seed=seed_start - 1)
    for _ in range(3):
        predict(model, _warm_geom.thickness, _warm_geom.gate_mask,
                _warm_geom.cavity_mask, device)

    solver_times = []
    surrogate_times = []

    for i in range(n_samples):
        geom = generate_random_part(grid_size=grid_size, seed=seed_start + i)
        cm = geom.cavity_mask

        # Solver (ground truth)
        t0 = time.perf_counter()
        ft_true = solve_fill_time(geom.thickness, geom.gate_mask, cm)
        air_true = detect_air_traps(ft_true, cm)
        solver_ms = (time.perf_counter() - t0) * 1000
        solver_times.append(solver_ms)
        ft_true_disp = np.where(cm, ft_true, np.nan)
        air_true_disp = np.where(cm, air_true, np.nan)

        # Surrogate
        ft_pred, air_pred, sur_ms = predict(model, geom.thickness, geom.gate_mask, cm, device)
        surrogate_times.append(sur_ms)

        # Compute relative error in fill time (only on cavity)
        valid_max = np.nanmax(ft_true_disp)
        err_pct = np.abs(ft_pred - ft_true_disp) / (valid_max + 1e-6) * 100

        # Plot
        ax = axes[i]
        im0 = ax[0].imshow(np.where(cm, geom.thickness, np.nan), cmap="cividis")
        ax[0].set_title(f"Thickness [mm]\n{geom.metadata['shape_type']}, "
                        f"gate={geom.metadata['gate_pos']}", fontsize=9)
        plt.colorbar(im0, ax=ax[0], fraction=0.04)

        # Mark gate
        gy, gx = geom.metadata["gate_pos"]
        ax[0].plot(gx, gy, "r*", markersize=14, markeredgecolor="white")

        vmax_ft = np.nanmax(ft_true_disp)
        im1 = ax[1].imshow(ft_true_disp, cmap=cmap_ft, vmin=0, vmax=vmax_ft)
        ax[1].set_title(f"Solver: fill time\n{solver_ms:.1f} ms", fontsize=9)
        plt.colorbar(im1, ax=ax[1], fraction=0.04)

        im2 = ax[2].imshow(ft_pred, cmap=cmap_ft, vmin=0, vmax=vmax_ft)
        ax[2].set_title(f"Surrogate: fill time\n{sur_ms:.1f} ms", fontsize=9)
        plt.colorbar(im2, ax=ax[2], fraction=0.04)

        im3 = ax[3].imshow(err_pct, cmap="hot", vmin=0, vmax=20)
        mae = np.nanmean(err_pct)
        ax[3].set_title(f"Rel. error [%]\nmean={mae:.1f}%", fontsize=9)
        plt.colorbar(im3, ax=ax[3], fraction=0.04)

        im4 = ax[4].imshow(air_true_disp, cmap=cmap_air, vmin=0, vmax=1)
        ax[4].set_title("Solver: air-trap risk", fontsize=9)
        plt.colorbar(im4, ax=ax[4], fraction=0.04)

        im5 = ax[5].imshow(air_pred, cmap=cmap_air, vmin=0, vmax=1)
        ax[5].set_title("Surrogate: air-trap risk", fontsize=9)
        plt.colorbar(im5, ax=ax[5], fraction=0.04)

        for a in ax:
            a.axis("off")

    fig.suptitle(
        f"Mold Filling Surrogate — Solver vs U-Net Prediction\n"
        f"Mean solver time: {np.mean(solver_times):.1f} ms  |  "
        f"Mean surrogate time: {np.mean(surrogate_times):.1f} ms  |  "
        f"Speedup: {np.mean(solver_times) / np.mean(surrogate_times):.1f}×",
        fontsize=12,
    )
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    print(f"Saved comparison figure to {output_path}")
    print(f"Solver:    {np.mean(solver_times):.1f} ± {np.std(solver_times):.1f} ms")
    print(f"Surrogate: {np.mean(surrogate_times):.1f} ± {np.std(surrogate_times):.1f} ms")
    return fig


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="models/best.pt")
    parser.add_argument("--n", type=int, default=4)
    parser.add_argument("--out", default="assets/comparison.png")
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, grid_size = load_model(Path(args.checkpoint), device=device)
    print(f"Using device={device}, grid={grid_size}")
    make_figure(model, n_samples=args.n, output_path=Path(args.out),
                device=device, grid_size=grid_size)
