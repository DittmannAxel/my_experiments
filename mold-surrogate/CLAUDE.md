# Build Instructions for Claude Code

> This file is written for an instance of Claude Code running on Axel's local
> workstation (dual NVIDIA A6000, 96GB combined VRAM, Linux). It tells you
> what was built in the sandbox, what's missing, and what to build out next.

## Context

This repository was scaffolded by Claude (web) running in an Anthropic sandbox
VM. The sandbox is CPU-only with limited time, so the deliverable is a
**working but small-scale prototype**: 80-sample dataset, 5-epoch training
run, 64×96 grid resolution. Loss converges sanely (2.18 → 0.18 in 5 epochs)
which proves the pipeline is correct end-to-end.

Your job is to take this from prototype to **demo-ready interview asset**.
Axel is in the loop for an NVIDIA "Senior DevRel Manager — Manufacturing
EMEA" role and wants a 30-second screen-record showing real-time mold-filling
prediction against a physics solver.

## Goals (priority order)

1. **Scale up** to a respectable dataset and training run that produces
   visually-tight surrogate predictions (target: <5% mean fill-time MAE)
2. **Polish the Streamlit demo** so it looks production-quality on screen-record
3. **Produce a 30-second screen-recording-ready demo path**
4. **(Stretch) PhysicsNeMo migration** of the surrogate model

## What's already done (verified working in sandbox)

- ✅ `src/solver.py` — Hele-Shaw eikonal solver via scikit-fmm, smoke-tested
- ✅ `src/geometry.py` — parametric geometry generator (rect / L / T / with-holes
  + ribs + thin sections + variable gate location)
- ✅ `src/dataset.py` — pipeline producing (inputs, targets, masks) NPZ
- ✅ `src/model.py` — U-Net architecture, ~7.7M params
- ✅ `src/train.py` — training loop with masked MSE loss, cosine LR schedule
- ✅ `demo/compare.py` — batch comparison figure (already working, see assets/comparison.png)
- ✅ `demo/interactive.py` — Streamlit demo with sliders
- ✅ `data/sample_dataset.npz` — 80 samples (sandbox)
- ✅ `models/best.pt` — 5-epoch checkpoint (sandbox; replace with your training run)

## What you need to do

### Step 1 — Set up environment

The repository's `requirements.txt` has CPU-only PyTorch. **Replace it with the
CUDA wheel for the user's platform**:

```bash
# Check CUDA version
nvidia-smi

# Install CUDA-enabled PyTorch (12.4 wheel works for most setups)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# The rest
pip install scikit-fmm numpy scipy matplotlib tqdm streamlit
```

Verify GPU is visible:

```python
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### Step 2 — Generate full dataset

The sandbox dataset has 80 samples. **For a real training run, generate 5000
samples** (still <2 minutes on CPU since the solver is fast):

```bash
python src/dataset.py --n 5000 --out data/train_dataset.npz --seed-offset 0
python src/dataset.py --n 500 --out data/test_dataset.npz --seed-offset 100000
```

Optional improvements to `src/dataset.py` that would help:

- **Higher resolution**: bump to 128×192 or 256×384. Current 64×96 is too coarse
  to look impressive on screen-record. Pass `--grid 128 192` to the script and
  verify all the downstream code still handles it (the U-Net is fully
  convolutional so it should). 256×384 will need ~2-4× more training time but
  looks dramatically better.
- **Train/val/test split**: currently `train.py` does a random split internally.
  For a clean experiment, pre-split the data into separate files.
- **Diverse part shapes**: extend `geometry.py` with additional shape primitives
  — circular plates, U-channel profiles, simple boss features, more varied rib
  patterns. Each new shape type increases the surrogate's generalization.

### Step 3 — Real training run

The 5-epoch sandbox run is a sanity check. **For a publishable result, run
50-100 epochs on the larger dataset:**

```bash
python src/train.py --dataset data/train_dataset.npz --epochs 80 \
    --batch-size 32 --lr 3e-4 --output models/
```

Expected outcomes on a single A6000:
- ~5000 samples × 80 epochs at 128×192 grid → ~30-60 minutes total
- Final val_loss should drop below ~0.05
- fill_time_MAE should drop below ~1.0 (vs ~3.0 in sandbox)

If results plateau early, try:
- Larger model: bump `base=64` in `model.py` (~30M params, still fits on A6000)
- Mixed precision: wrap training in `torch.cuda.amp.autocast()` for speed
- Data augmentation: random horizontal/vertical flips of (input, target, mask)
  triplets in the training loop — the physics is rotation-equivariant so this
  is a free win

### Step 4 — Verify on test set

Add this to `train.py` or as a new `evaluate.py`:

```python
# Compute test set MAE on a held-out test set
# Per-shape-type breakdown so you can see if any class is failing
```

Goal: fill-time MAE < 5% of dataset max for ≥95% of test samples. If a class
of shapes is failing (e.g., L-shapes), generate more of those during training.

### Step 5 — Polish the Streamlit demo

`demo/interactive.py` works but is plain. To make it screen-record-worthy:

- **Better preset gallery**: add a row of clickable thumbnails for "Rectangle",
  "L-bracket", "T-shape", "Plate with holes", each loading a fixed seed
- **Animated fill sequence**: instead of just showing the final fill-time map,
  render a 30-frame animation where you raise a threshold from 0 → max(τ) and
  show the part filling up. Looks dramatically better on screen-record.
- **Side-by-side timing display**: large-font "Solver: 412 ms" vs
  "Surrogate: 3 ms" with the speedup multiplier prominently displayed
- **Theme**: use Streamlit's `st.set_page_config(page_icon="🏭")`, dark mode,
  branded colors. Worth 10 minutes of polish.
- **Mobile-ish layout**: ensure the demo fits in a 16:9 screen-record without
  the user needing to scroll. Use `st.columns` aggressively.

### Step 6 — Generate the screen-record asset

Suggested 30-second narrative for the recording:

1. (0-5s) Show a part geometry + the Moldflow-style fill-time map appearing
   slowly via solver
2. (5-15s) Drag the gate-position slider — surrogate prediction updates in
   real-time as you drag
3. (15-25s) Switch to a different part shape, change the wall thickness
   multiplier, watch fill pattern change live
4. (25-30s) Side-by-side: same part, solver vs surrogate, with the speedup
   number prominent

Use OBS or `ffmpeg` to record. Compress to <10MB so it's LinkedIn-attachable.

### Step 7 — (Stretch) PhysicsNeMo migration

The current model is plain PyTorch. For maximal NVIDIA-credibility:

```python
# In a new file src/model_fno.py
from physicsnemo.models.fno import FNO

model = FNO(
    in_channels=2,
    out_channels=2,
    decoder_layers=1,
    decoder_layer_size=32,
    dimension=2,
    latent_channels=32,
    num_fno_layers=4,
    num_fno_modes=12,  # spectral modes — key hyperparameter
    padding=8,
)
```

Train with the same dataset/loss/loop. FNO typically needs less data and
extrapolates better to unseen geometries — should reach equivalent MAE with
half the dataset.

PhysicsNeMo install is heavy (`pip install nvidia-physicsnemo`) and the
documentation lives at https://docs.nvidia.com/physicsnemo/. If install is
painful on Axel's setup, skip this — the U-Net result is already a strong
demo asset.

### Step 8 — (Stretch) Real OpenFOAM training data

Current data is solver-generated synthetic. Replacing the solver with **real
OpenFOAM Hele-Shaw simulations** would be the biggest credibility upgrade:

- Use OpenFOAM's `interFoam` (multiphase VOF) on a 2D thin slab geometry
- Or implement a direct Hele-Shaw solver in OpenFOAM (~200 lines of custom
  solver code)
- Generate 1000-5000 samples on the A6000 box (might need overnight)

This is multi-day work, not session-scope. Mention the path in the README as
"future work" if you don't get there.

## Things to avoid

- **Don't claim Moldflow-equivalence anywhere.** This is a research prototype
  with isothermal Newtonian Hele-Shaw approximation. Be precise about that in
  any written description, especially in interview contexts. NVIDIA reviewers
  will spot overclaim instantly.
- **Don't add LangChain / Streamlit chat UI / LLM-based "explanation".**
  Resist the temptation. The demo's strength is precision and speed; LLM
  garnish dilutes it.
- **Don't skip the test-set evaluation.** A nice-looking demo that secretly
  has 50% MAE is worse than no demo. Quantify.

## Final deliverables checklist

- [ ] `data/train_dataset.npz` (5000+ samples at ≥128×192 resolution)
- [ ] `data/test_dataset.npz` (held-out)
- [ ] `models/best.pt` from real training run (val_loss < 0.05)
- [ ] `evaluate.py` script with per-shape-type test MAE
- [ ] Polished `demo/interactive.py` with preset gallery and large timing display
- [ ] `assets/demo.mp4` — 30-second screen recording
- [ ] `assets/comparison.png` — regenerated with real model
- [ ] Updated README with real numbers (replace sandbox numbers in the figure
      caption and "expected outcomes" section)

## A note on time budget

If you have ~4 hours of focused work, here's the realistic split:

| Step | Time | Notes |
|------|------|-------|
| Env + dependencies + GPU verify | 15 min | |
| Dataset generation @ 128×192 × 5000 | 10 min | |
| Training run (80 epochs) | 60-90 min | Run while doing other things |
| Streamlit polish | 60 min | Biggest discretionary investment |
| Test-set evaluation | 30 min | Critical, do not skip |
| Screen recording | 30 min | Including 2-3 takes |
| Migration to FNO | 60 min | Stretch — only if everything else done |

If short on time, prioritize: real training run > Streamlit polish > screen
record. The other steps can be follow-up.

Good luck. The goal is to give Axel a tangible artifact that shows up in 30
seconds, looks like Moldflow but runs in milliseconds, and that he can ship
along with his NVIDIA application.
