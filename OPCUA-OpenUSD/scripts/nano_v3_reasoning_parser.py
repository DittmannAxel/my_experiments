# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES.
# Reasoning parser plugin for NVIDIA Nemotron-3-Nano (30B-A3B / 12B-v2) when
# served by vLLM. Vendored from
# https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8/resolve/main/nano_v3_reasoning_parser.py
# so the launch script can pass `--reasoning-parser-plugin` without depending
# on whatever the user has in CWD.

from vllm.reasoning.abs_reasoning_parsers import ReasoningParserManager
from vllm.reasoning.deepseek_r1_reasoning_parser import DeepSeekR1ReasoningParser


@ReasoningParserManager.register_module("nano_v3")
class NanoV3ReasoningParser(DeepSeekR1ReasoningParser):
    def extract_reasoning(self, model_output, request):
        reasoning_content, final_content = super().extract_reasoning(
            model_output, request
        )
        if (
            hasattr(request, "chat_template_kwargs")
            and request.chat_template_kwargs
            and request.chat_template_kwargs.get("enable_thinking") is False
            and final_content is None
        ):
            reasoning_content, final_content = final_content, reasoning_content

        return reasoning_content, final_content
