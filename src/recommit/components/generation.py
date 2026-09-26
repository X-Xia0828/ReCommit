"""Generation."""

from __future__ import annotations
import json
from typing import Any


def compact_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


class QwenGenerator:
    def __init__(
        self,
        model_id: str,
        max_new_tokens: int,
        temperature: float,
        *,
        deterministic: bool = False,
        attn_implementation: str | None = None,
    ):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        model_kwargs = {
            "torch_dtype": torch.bfloat16,
            "device_map": "auto",
            "trust_remote_code": True,
        }
        if attn_implementation is not None:
            model_kwargs["attn_implementation"] = attn_implementation
        else:
            model_kwargs["attn_implementation"] = "sdpa"
        self.model = AutoModelForCausalLM.from_pretrained(model_id, **model_kwargs)
        self.model.eval()
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.deterministic = deterministic
        self.attn_implementation = model_kwargs.get("attn_implementation")

    def generate(self, system: str, prompt: str, *, seed: int | None = None) -> str:
        if seed is not None:
            from recommit.components.randomness import seed_torch

            seed_torch(int(seed))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        try:
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
            )
        except TypeError:
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        do_sample = self.temperature > 0
        generate_kwargs = {
            "max_new_tokens": self.max_new_tokens,
            "do_sample": do_sample,
            "temperature": self.temperature if do_sample else None,
            "pad_token_id": self.tokenizer.eos_token_id,
        }
        out = self.model.generate(**inputs, **generate_kwargs)
        new_tokens = out[0, inputs["input_ids"].shape[-1] :]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True)
