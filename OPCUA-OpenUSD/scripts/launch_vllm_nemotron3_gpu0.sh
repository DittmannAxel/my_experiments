#!/bin/bash
# vLLM launch — single-GPU (GPU 0) Nemotron-3-Nano-30B-A3B-FP8 with tool calling.
#
# 30B total / 3.5B active per token (Mamba2 + Transformer hybrid, MoE).
# FP8 weights → ~17 GB on GPU 0; KV-cache also fp8 to fit a long context.
# Tool calls use the qwen3_coder parser (Nemotron-3 wire format).
# Reasoning extraction uses the vendored nano_v3 plugin so we can toggle
# `enable_thinking` from the chat template without losing the final answer.
#
# Frees GPU 1 for Omniverse Kit. Runs on the host (NOT in a container).
set -euo pipefail

source ~/miniconda3/bin/activate vllm

export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_V1=1
# Note: VLLM_USE_FLASHINFER_MOE_FP8=1 in the model card targets Hopper
# (sm_90). On Ada Lovelace (RTX 6000 Ada, sm_89) it raises
# NotImplementedError because there is no compatible FlashInfer FP8 MoE
# backend yet. Leave it unset and let vLLM pick the cutlass FP8 path.

MODEL_DIR="${MODEL_DIR:-$HOME/dev/models/Nemotron-3-Nano-30B-A3B-FP8}"
SERVED_NAME="${SERVED_NAME:-nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8}"
PARSER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec vllm serve "$MODEL_DIR" \
  --served-model-name "$SERVED_NAME" \
  --tensor-parallel-size 1 \
  --max-model-len 65536 \
  --max-num-seqs 8 \
  --gpu-memory-utilization 0.85 \
  --kv-cache-dtype fp8 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser-plugin "$PARSER_DIR/nano_v3_reasoning_parser.py" \
  --reasoning-parser nano_v3 \
  --host 0.0.0.0 \
  --port 8000 \
  2>&1
