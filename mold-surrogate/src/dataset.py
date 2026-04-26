"""
Dataset Generation

Pairs each random geometry with its solver-computed fill-time map and
air-trap probability map. Saves as compressed NPZ for training.

Input channels (the network sees these):
  0: thickness map (mm), normalized
  1: gate distance map (encodes WHERE injection happens)

Output channels (the network learns to predict these):
  0: log-fill-time map (more uniform distribution than raw fill time)
  1: air-trap probability map
"""
import argparse
import time
from pathlib import Path
import numpy as np
from tqdm import tqdm

from solver import solve_fill_time, detect_air_traps
from geometry import generate_random_part


def encode_gate_distance(gate_mask: np.ndarray, cavity_mask: np.ndarray) -> np.ndarray:
    """Encode gate location as Euclidean-distance-from-gate field.

    Distance fields are a much more useful network input than a sparse
    delta function — they tell the conv kernels 'how far each cell is
    from injection', which strongly correlates with fill time."""
    from scipy.ndimage import distance_transform_edt
    # 1 everywhere except gate; FMM-style distance from gate
    d = distance_transform_edt(~gate_mask).astype(np.float32)
    d = d * cavity_mask  # zero outside cavity
    # normalize by cavity diagonal
    H, W = cavity_mask.shape
    d = d / np.sqrt(H ** 2 + W ** 2)
    return d.astype(np.float32)


def make_sample(seed: int, grid_size=(64, 96)):
    """Generate one geometry + solver output pair."""
    geom = generate_random_part(grid_size=grid_size, seed=seed)

    fill_time = solve_fill_time(geom.thickness, geom.gate_mask, geom.cavity_mask)
    air_risk = detect_air_traps(fill_time, geom.cavity_mask)

    # Replace inf with 0 outside cavity (mask handles meaning)
    fill_time_clean = np.where(np.isfinite(fill_time), fill_time, 0.0).astype(np.float32)

    # Network inputs (2 channels)
    inp_thickness = (geom.thickness / 5.0).astype(np.float32)  # normalize to ~[0,1]
    inp_gate_dist = encode_gate_distance(geom.gate_mask, geom.cavity_mask)
    inputs = np.stack([inp_thickness, inp_gate_dist], axis=0)  # (2, H, W)

    # Network targets (2 channels)
    # log(1+t) compresses the dynamic range — fill times can span 2+ decades
    log_ft = np.log1p(fill_time_clean).astype(np.float32)
    targets = np.stack([log_ft, air_risk.astype(np.float32)], axis=0)  # (2, H, W)

    mask = geom.cavity_mask.astype(np.float32)
    return inputs, targets, mask, geom.metadata


def build_dataset(n_samples: int, output_path: Path, grid_size=(64, 96), seed_offset=0):
    """Build a dataset and save to NPZ."""
    H, W = grid_size
    X = np.zeros((n_samples, 2, H, W), dtype=np.float32)
    Y = np.zeros((n_samples, 2, H, W), dtype=np.float32)
    M = np.zeros((n_samples, H, W), dtype=np.float32)
    metadata = []

    t0 = time.time()
    for i in tqdm(range(n_samples), desc="Generating dataset"):
        inp, tgt, msk, meta = make_sample(seed=seed_offset + i, grid_size=grid_size)
        X[i] = inp
        Y[i] = tgt
        M[i] = msk
        metadata.append(meta)
    elapsed = time.time() - t0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Pack metadata into parallel arrays for compact NPZ storage
    shape_types = np.array([m["shape_type"] for m in metadata])
    base_thickness = np.array([m["base_thickness_mm"] for m in metadata], dtype=np.float32)
    n_ribs_arr = np.array([m["n_ribs"] for m in metadata], dtype=np.int32)
    seeds_arr = np.array([m["seed"] for m in metadata], dtype=np.int64)
    np.savez_compressed(
        output_path,
        inputs=X, targets=Y, masks=M,
        grid_size=np.array(grid_size),
        shape_types=shape_types,
        base_thickness_mm=base_thickness,
        n_ribs=n_ribs_arr,
        seeds=seeds_arr,
    )
    print(f"\nSaved {n_samples} samples to {output_path}")
    print(f"  Input shape:  {X.shape} ({X.nbytes / 1e6:.1f} MB)")
    print(f"  Target shape: {Y.shape} ({Y.nbytes / 1e6:.1f} MB)")
    print(f"  Generation time: {elapsed:.1f}s ({elapsed / n_samples * 1000:.1f} ms/sample)")
    # Print shape distribution
    unique, counts = np.unique(shape_types, return_counts=True)
    print(f"  Shape distribution: {dict(zip(unique.tolist(), counts.tolist()))}")
    return X, Y, M


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50, help="number of samples")
    parser.add_argument("--out", type=str, default="data/sample_dataset.npz")
    parser.add_argument("--grid", type=int, nargs=2, default=[64, 96])
    parser.add_argument("--seed-offset", type=int, default=0)
    args = parser.parse_args()

    build_dataset(args.n, Path(args.out), grid_size=tuple(args.grid),
                  seed_offset=args.seed_offset)
