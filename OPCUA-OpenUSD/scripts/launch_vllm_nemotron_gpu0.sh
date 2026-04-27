#!/bin/bash
# vLLM launch — single-GPU (GPU 0) Nemotron Nano 8B with tool calling.
# Frees GPU 1 for Omniverse Kit. Runs on the host (NOT in a container).
set -euo pipefail

source ~/miniconda3/bin/activate vllm

export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_V1=1

MODEL_DIR="${MODEL_DIR:-$HOME/dev/models/Llama-3.1-Nemotron-Nano-8B-v1}"
SERVED_NAME="${SERVED_NAME:-nvidia/Llama-3.1-Nemotron-Nano-8B-v1}"

exec vllm serve "$MODEL_DIR" \
  --served-model-name "$SERVED_NAME" \
  --tensor-parallel-size 1 \
  --max-model-len 32768 \
  --max-num-batched-tokens 8192 \
  --gpu-memory-utilization 0.85 \
  --enable-auto-tool-choice \
  --tool-call-parser llama3_json \
  --host 0.0.0.0 \
  --port 8000 \
  2>&1
