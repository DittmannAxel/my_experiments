"""
Training Script

Trains the U-Net surrogate to predict (log fill time, air risk) from
(thickness, gate distance).

Key implementation details:

- Masked loss: only compute MSE over cavity cells, so the network spends
  capacity on the actual prediction problem instead of learning to output
  zeros outside the cavity.

- Mixed precision (AMP): autocast in fp16 for ~1.5-2x speedup on Ada-class GPUs.

- Random h/v flip augmentation: the eikonal physics is rotation/flip
  equivariant, so this is free training signal.

- Loss weighting: fill-time prediction is the primary task (weight 1.0),
  air-trap detection is auxiliary (weight 0.3).

- Two checkpoints saved: best.pt (lowest val_loss) and best_mae.pt
  (lowest fill-time MAE) — usually the same epoch, but tracked separately
  in case they diverge.
"""
import argparse
import json
import time
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, random_split
from tqdm import tqdm

from model import UNetSurrogate, count_params


def masked_mse(pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """MSE computed only over cavity cells."""
    diff = (pred - target) ** 2
    diff = diff * mask.unsqueeze(1)  # broadcast mask over channel dim
    return diff.sum() / (mask.sum() * pred.shape[1] + 1e-8)


def random_flip_batch(x: torch.Tensor, y: torch.Tensor, m: torch.Tensor):
    """Apply the same random h/v flip to (input, target, mask) triplets."""
    if torch.rand(1).item() < 0.5:
        x = torch.flip(x, dims=[-1])
        y = torch.flip(y, dims=[-1])
        m = torch.flip(m, dims=[-1])
    if torch.rand(1).item() < 0.5:
        x = torch.flip(x, dims=[-2])
        y = torch.flip(y, dims=[-2])
        m = torch.flip(m, dims=[-2])
    return x, y, m


def train(
    dataset_path: Path,
    output_dir: Path,
    val_dataset_path: Path | None = None,
    epochs: int = 30,
    batch_size: int = 8,
    lr: float = 3e-4,
    val_split: float = 0.2,
    augment: bool = True,
    use_amp: bool = True,
    num_workers: int = 4,
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
):
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load dataset
    print(f"Loading dataset from {dataset_path}")
    data = np.load(dataset_path)
    X = torch.from_numpy(data["inputs"])    # (N, 2, H, W)
    Y = torch.from_numpy(data["targets"])   # (N, 2, H, W)
    M = torch.from_numpy(data["masks"])     # (N, H, W)
    grid_size = tuple(int(v) for v in data["grid_size"])

    print(f"Dataset: {len(X)} samples, grid {tuple(X.shape[2:])}")
    print(f"Device:  {device}  (AMP={use_amp}, augment={augment})")

    if val_dataset_path is not None and Path(val_dataset_path).exists():
        print(f"Loading external val set from {val_dataset_path}")
        vdata = np.load(val_dataset_path)
        Xv = torch.from_numpy(vdata["inputs"])
        Yv = torch.from_numpy(vdata["targets"])
        Mv = torch.from_numpy(vdata["masks"])
        train_ds = TensorDataset(X, Y, M)
        val_ds = TensorDataset(Xv, Yv, Mv)
        print(f"  train={len(train_ds)}  val={len(val_ds)}")
    else:
        full_ds = TensorDataset(X, Y, M)
        n_val = max(1, int(len(full_ds) * val_split))
        n_train = len(full_ds) - n_val
        train_ds, val_ds = random_split(full_ds, [n_train, n_val],
                                         generator=torch.Generator().manual_seed(42))
        print(f"  train={n_train}  val={n_val} (random split, val_split={val_split})")

    pin = device == "cuda"
    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True,
        num_workers=num_workers, pin_memory=pin,
        persistent_workers=num_workers > 0,
    )
    val_loader = DataLoader(
        val_ds, batch_size=batch_size,
        num_workers=num_workers, pin_memory=pin,
        persistent_workers=num_workers > 0,
    )

    # Build model
    model = UNetSurrogate(in_channels=2, out_channels=2).to(device)
    print(f"Model:   {count_params(model):,} parameters")

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    scaler = torch.amp.GradScaler("cuda", enabled=(use_amp and device == "cuda"))

    history = {"train_loss": [], "val_loss": [], "val_ft_mae": [], "lr": [], "epoch_time_s": []}

    best_val = float("inf")
    best_mae = float("inf")
    t_total = time.time()
    for epoch in range(epochs):
        # ---- Train ----
        model.train()
        train_losses = []
        t_epoch = time.time()
        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")
        for x, y, m in pbar:
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            m = m.to(device, non_blocking=True)
            if augment:
                x, y, m = random_flip_batch(x, y, m)

            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=(use_amp and device == "cuda")):
                pred = model(x)
                loss_ft = masked_mse(pred[:, 0:1], y[:, 0:1], m)
                loss_air = masked_mse(pred[:, 1:2], y[:, 1:2], m)
                loss = loss_ft + 0.3 * loss_air

            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            train_losses.append(loss.item())
            pbar.set_postfix(loss=f"{loss.item():.4f}")

        # ---- Validate ----
        model.eval()
        val_losses = []
        ft_mae_list = []
        with torch.no_grad():
            for x, y, m in val_loader:
                x = x.to(device, non_blocking=True)
                y = y.to(device, non_blocking=True)
                m = m.to(device, non_blocking=True)
                with torch.amp.autocast("cuda", enabled=(use_amp and device == "cuda")):
                    pred = model(x)
                    loss_ft = masked_mse(pred[:, 0:1], y[:, 0:1], m)
                    loss_air = masked_mse(pred[:, 1:2], y[:, 1:2], m)
                val_losses.append((loss_ft + 0.3 * loss_air).item())
                pred_ft = torch.expm1(pred[:, 0].float())
                true_ft = torch.expm1(y[:, 0].float())
                ft_mae = ((pred_ft - true_ft).abs() * m).sum() / (m.sum() + 1e-8)
                ft_mae_list.append(ft_mae.item())

        scheduler.step()
        train_loss = float(np.mean(train_losses))
        val_loss = float(np.mean(val_losses))
        ft_mae = float(np.mean(ft_mae_list))
        epoch_s = time.time() - t_epoch
        cur_lr = optimizer.param_groups[0]["lr"]
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_ft_mae"].append(ft_mae)
        history["lr"].append(cur_lr)
        history["epoch_time_s"].append(epoch_s)

        print(f"  train={train_loss:.4f}  val={val_loss:.4f}  "
              f"fill_time_MAE={ft_mae:.3f}  lr={cur_lr:.2e}  ({epoch_s:.1f}s)")

        ckpt_payload = {
            "model": model.state_dict(),
            "epoch": epoch,
            "val_loss": val_loss,
            "val_ft_mae": ft_mae,
            "config": {"in_channels": 2, "out_channels": 2},
            "grid_size": list(grid_size),
        }
        if val_loss < best_val:
            best_val = val_loss
            torch.save(ckpt_payload, output_dir / "best.pt")
        if ft_mae < best_mae:
            best_mae = ft_mae
            torch.save(ckpt_payload, output_dir / "best_mae.pt")
        torch.save(ckpt_payload, output_dir / "last.pt")

        with open(output_dir / "history.json", "w") as f:
            json.dump(history, f, indent=2)

    total_s = time.time() - t_total
    print(f"\nTotal training time: {total_s:.1f}s ({total_s/60:.1f} min)")
    print(f"Best val loss: {best_val:.4f}")
    print(f"Best fill-time MAE: {best_mae:.3f}")
    print(f"Checkpoint saved to {output_dir / 'best.pt'}")
    return model, history


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/sample_dataset.npz")
    parser.add_argument("--val-dataset", default=None,
                        help="Optional separate val NPZ. If None, uses val_split fraction.")
    parser.add_argument("--output", default="models/")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--no-augment", action="store_true")
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--num-workers", type=int, default=4)
    args = parser.parse_args()

    train(
        dataset_path=Path(args.dataset),
        output_dir=Path(args.output),
        val_dataset_path=Path(args.val_dataset) if args.val_dataset else None,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        val_split=args.val_split,
        augment=not args.no_augment,
        use_amp=not args.no_amp,
        num_workers=args.num_workers,
    )
