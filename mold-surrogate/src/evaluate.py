"""
Test-Set Evaluation

Loads a trained checkpoint and runs it on a held-out test dataset.
Reports:
  - Overall fill-time MAE (and as % of dataset max)
  - Per-shape-type MAE breakdown (rect, L, T, rect_holes)
  - Per-sample worst/best/median error
  - Surrogate vs solver speed comparison
  - Pass rate (fraction of samples below 5% MAE threshold)
  - JSON report saved to disk

Usage:
  python src/evaluate.py --checkpoint models/best.pt --dataset data/test_dataset.npz
"""
import argparse
import json
import time
from pathlib import Path
import numpy as np
import torch
from tqdm import tqdm

from model import UNetSurrogate
from solver import solve_fill_time
from dataset import encode_gate_distance


def per_sample_metrics(pred_ft: np.ndarray, true_ft: np.ndarray, mask: np.ndarray):
    """Compute per-sample fill-time MAE and relative error vs sample max."""
    valid = mask > 0
    if not valid.any():
        return {"mae_abs": float("nan"), "mae_pct": float("nan"),
                "max_true": float("nan"), "max_pred": float("nan")}
    diff = np.abs(pred_ft - true_ft) * valid
    n_valid = valid.sum()
    mae_abs = float(diff.sum() / n_valid)
    max_true = float(true_ft[valid].max())
    max_pred = float(pred_ft[valid].max())
    mae_pct = mae_abs / (max_true + 1e-6) * 100
    return {"mae_abs": mae_abs, "mae_pct": mae_pct,
            "max_true": max_true, "max_pred": max_pred}


def evaluate(
    checkpoint_path: Path,
    dataset_path: Path,
    output_path: Path = Path("evaluation_report.json"),
    pass_threshold_pct: float = 5.0,
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
):
    print(f"Loading checkpoint: {checkpoint_path}")
    state = torch.load(checkpoint_path, map_location=device, weights_only=False)
    model = UNetSurrogate(**state["config"])
    model.load_state_dict(state["model"])
    model.to(device)
    model.eval()
    print(f"  epoch={state.get('epoch', '?')}, val_loss={state.get('val_loss', '?'):.4f}, "
          f"val_ft_mae={state.get('val_ft_mae', float('nan')):.3f}")

    print(f"Loading test set: {dataset_path}")
    data = np.load(dataset_path, allow_pickle=False)
    X = data["inputs"]
    Y = data["targets"]
    M = data["masks"]
    shape_types = data["shape_types"] if "shape_types" in data.files else None
    print(f"  {len(X)} samples, grid {X.shape[2:]}")
    if shape_types is not None:
        unique, counts = np.unique(shape_types, return_counts=True)
        print(f"  Shape mix: {dict(zip(unique.tolist(), counts.tolist()))}")

    # Per-sample inference timing
    sample_metrics = []
    surrogate_times_ms = []
    with torch.no_grad():
        for i in tqdm(range(len(X)), desc="Evaluating"):
            x = torch.from_numpy(X[i:i+1]).to(device)
            mask = M[i]
            true_ft = np.expm1(Y[i, 0])

            if device == "cuda":
                torch.cuda.synchronize()
            t0 = time.perf_counter()
            pred = model(x)
            if device == "cuda":
                torch.cuda.synchronize()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            surrogate_times_ms.append(elapsed_ms)

            log_ft = pred[0, 0].cpu().numpy()
            pred_ft = np.expm1(log_ft)

            metrics = per_sample_metrics(pred_ft, true_ft, mask)
            metrics["sample_idx"] = i
            metrics["surrogate_ms"] = elapsed_ms
            if shape_types is not None:
                metrics["shape_type"] = str(shape_types[i])
            sample_metrics.append(metrics)

    pct_errors = np.array([s["mae_pct"] for s in sample_metrics])
    abs_errors = np.array([s["mae_abs"] for s in sample_metrics])

    summary = {
        "n_samples": int(len(X)),
        "fill_time_mae_abs": {
            "mean": float(np.mean(abs_errors)),
            "median": float(np.median(abs_errors)),
            "p95": float(np.percentile(abs_errors, 95)),
            "max": float(np.max(abs_errors)),
        },
        "fill_time_mae_pct": {
            "mean": float(np.mean(pct_errors)),
            "median": float(np.median(pct_errors)),
            "p95": float(np.percentile(pct_errors, 95)),
            "max": float(np.max(pct_errors)),
        },
        "pass_rate_at_threshold": {
            "threshold_pct": pass_threshold_pct,
            "fraction_passing": float((pct_errors < pass_threshold_pct).mean()),
        },
        "surrogate_inference_ms": {
            "mean": float(np.mean(surrogate_times_ms)),
            "p50": float(np.percentile(surrogate_times_ms, 50)),
            "p95": float(np.percentile(surrogate_times_ms, 95)),
        },
        "checkpoint": str(checkpoint_path),
        "dataset": str(dataset_path),
    }

    # Per-shape breakdown
    if shape_types is not None:
        per_shape = {}
        for stype in np.unique(shape_types):
            indices = [i for i in range(len(shape_types)) if shape_types[i] == stype]
            shape_pct = pct_errors[indices]
            shape_abs = abs_errors[indices]
            per_shape[str(stype)] = {
                "n": int(len(indices)),
                "mae_pct_mean": float(np.mean(shape_pct)),
                "mae_pct_median": float(np.median(shape_pct)),
                "mae_pct_p95": float(np.percentile(shape_pct, 95)),
                "mae_abs_mean": float(np.mean(shape_abs)),
                "pass_rate": float((shape_pct < pass_threshold_pct).mean()),
            }
        summary["per_shape"] = per_shape

    # Solver timing on first 20 samples (resimulate)
    print("\nMeasuring solver timing on first 20 samples...")
    from geometry import generate_random_part
    solver_times_ms = []
    seeds = data["seeds"] if "seeds" in data.files else list(range(min(20, len(X))))
    for s in seeds[:20]:
        geom = generate_random_part(seed=int(s), grid_size=tuple(X.shape[2:]))
        t0 = time.perf_counter()
        ft = solve_fill_time(geom.thickness, geom.gate_mask, geom.cavity_mask)
        solver_times_ms.append((time.perf_counter() - t0) * 1000)
    summary["solver_ms_subsample"] = {
        "mean": float(np.mean(solver_times_ms)),
        "median": float(np.median(solver_times_ms)),
    }
    summary["speedup_x"] = float(np.mean(solver_times_ms) / np.mean(surrogate_times_ms))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({"summary": summary, "per_sample": sample_metrics}, f, indent=2)

    # Print human-friendly report
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    print(f"Samples: {summary['n_samples']}")
    print(f"\nFill-time MAE (absolute, normalized units):")
    print(f"  mean:   {summary['fill_time_mae_abs']['mean']:.3f}")
    print(f"  median: {summary['fill_time_mae_abs']['median']:.3f}")
    print(f"  p95:    {summary['fill_time_mae_abs']['p95']:.3f}")
    print(f"\nFill-time MAE (% of sample max):")
    print(f"  mean:   {summary['fill_time_mae_pct']['mean']:.2f}%")
    print(f"  median: {summary['fill_time_mae_pct']['median']:.2f}%")
    print(f"  p95:    {summary['fill_time_mae_pct']['p95']:.2f}%")
    print(f"\nPass rate (<{pass_threshold_pct:.1f}% MAE): "
          f"{summary['pass_rate_at_threshold']['fraction_passing']*100:.1f}%")
    print(f"\nSurrogate inference: {summary['surrogate_inference_ms']['mean']:.2f} ms (mean), "
          f"{summary['surrogate_inference_ms']['p95']:.2f} ms (p95)")
    print(f"Solver (subsample 20): {summary['solver_ms_subsample']['mean']:.1f} ms (mean)")
    print(f"Speedup: {summary['speedup_x']:.1f}x")
    if "per_shape" in summary:
        print("\nPer-shape MAE breakdown:")
        for stype, m in summary["per_shape"].items():
            print(f"  {stype:12s} n={m['n']:4d}  "
                  f"mae_pct mean={m['mae_pct_mean']:5.2f}%  median={m['mae_pct_median']:5.2f}%  "
                  f"pass={m['pass_rate']*100:5.1f}%")
    print(f"\nReport saved to {output_path}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="models/best.pt")
    parser.add_argument("--dataset", default="data/test_dataset.npz")
    parser.add_argument("--output", default="models/evaluation_report.json")
    parser.add_argument("--pass-threshold-pct", type=float, default=5.0,
                        help="MAE %% threshold for the pass-rate metric")
    args = parser.parse_args()

    evaluate(
        checkpoint_path=Path(args.checkpoint),
        dataset_path=Path(args.dataset),
        output_path=Path(args.output),
        pass_threshold_pct=args.pass_threshold_pct,
    )
