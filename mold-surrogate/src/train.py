"""
Training Script

Trains the U-Net surrogate to predict (log fill time, air risk) from
(thickness, gate distance).

Key implementation detail: masked loss. The cavity mask defines which
cells are inside the part. We only compute loss on those cells —
otherwise the network wastes capacity learning to predict zeros outside
the cavity.

Loss weighting: fill-time prediction is the primary task (weight 1.0),
air-trap detection is auxiliary (weight 0.3). Air traps are sparse,
which makes them harder to learn — could also try focal loss for that head.
"""
import argparse
import json
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


def train(
    dataset_path: Path,
    output_dir: Path,
    epochs: int = 30,
    batch_size: int = 8,
    lr: float = 3e-4,
    val_split: float = 0.2,
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
):
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load dataset
    print(f"Loading dataset from {dataset_path}")
    data = np.load(dataset_path)
    X = torch.from_numpy(data["inputs"])    # (N, 2, H, W)
    Y = torch.from_numpy(data["targets"])   # (N, 2, H, W)
    M = torch.from_numpy(data["masks"])     # (N, H, W)

    print(f"Dataset: {len(X)} samples, grid {X.shape[2:]}")
    print(f"Device:  {device}")

    full_ds = TensorDataset(X, Y, M)
    n_val = max(1, int(len(full_ds) * val_split))
    n_train = len(full_ds) - n_val
    train_ds, val_ds = random_split(full_ds, [n_train, n_val],
                                     generator=torch.Generator().manual_seed(42))

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size)

    # Build model
    model = UNetSurrogate(in_channels=2, out_channels=2).to(device)
    print(f"Model:   {count_params(model):,} parameters")

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    history = {"train_loss": [], "val_loss": [], "val_ft_mae": []}

    best_val = float("inf")
    for epoch in range(epochs):
        # ---- Train ----
        model.train()
        train_losses = []
        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")
        for x, y, m in pbar:
            x, y, m = x.to(device), y.to(device), m.to(device)
            optimizer.zero_grad()
            pred = model(x)

            # Channel 0: log fill time (primary)
            loss_ft = masked_mse(pred[:, 0:1], y[:, 0:1], m)
            # Channel 1: air risk (auxiliary)
            loss_air = masked_mse(pred[:, 1:2], y[:, 1:2], m)
            loss = loss_ft + 0.3 * loss_air

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(loss.item())
            pbar.set_postfix(loss=f"{loss.item():.4f}")

        # ---- Validate ----
        model.eval()
        val_losses = []
        ft_mae_list = []
        with torch.no_grad():
            for x, y, m in val_loader:
                x, y, m = x.to(device), y.to(device), m.to(device)
                pred = model(x)
                loss_ft = masked_mse(pred[:, 0:1], y[:, 0:1], m)
                loss_air = masked_mse(pred[:, 1:2], y[:, 1:2], m)
                val_losses.append((loss_ft + 0.3 * loss_air).item())
                # MAE on actual fill time (after expm1 to invert log1p)
                pred_ft = torch.expm1(pred[:, 0])
                true_ft = torch.expm1(y[:, 0])
                ft_mae = ((pred_ft - true_ft).abs() * m).sum() / (m.sum() + 1e-8)
                ft_mae_list.append(ft_mae.item())

        scheduler.step()
        train_loss = np.mean(train_losses)
        val_loss = np.mean(val_losses)
        ft_mae = np.mean(ft_mae_list)
        history["train_loss"].append(float(train_loss))
        history["val_loss"].append(float(val_loss))
        history["val_ft_mae"].append(float(ft_mae))

        print(f"  train={train_loss:.4f}  val={val_loss:.4f}  fill_time_MAE={ft_mae:.3f}")

        if val_loss < best_val:
            best_val = val_loss
            torch.save({
                "model": model.state_dict(),
                "epoch": epoch,
                "val_loss": val_loss,
                "config": {"in_channels": 2, "out_channels": 2},
            }, output_dir / "best.pt")

    # Save history
    with open(output_dir / "history.json", "w") as f:
        json.dump(history, f, indent=2)

    print(f"\nBest val loss: {best_val:.4f}")
    print(f"Checkpoint saved to {output_dir / 'best.pt'}")
    return model, history


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/sample_dataset.npz")
    parser.add_argument("--output", default="models/")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-4)
    args = parser.parse_args()

    train(
        dataset_path=Path(args.dataset),
        output_dir=Path(args.output),
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
    )
