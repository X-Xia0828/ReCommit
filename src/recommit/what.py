"""One masked LLaDA forward pass followed by ranked support enumeration."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from .components.legend import NONE, build_legend
from .components.support_search import inclusion_scores, kbest_inclusion_sets
from .components.what_prompt import build_block_prompt
from .services import vocabulary
from .types import Episode


class LLaDA:
    """Score operation codes at exact mask-token positions, without AR shifting."""

    mask_id = 126336

    def __init__(self, model_id: str, code_dir: str, device: str = "cuda"):
        import torch
        from transformers import AutoConfig, AutoTokenizer

        source = Path(code_dir).resolve()
        if not (source / "model/modeling_llada.py").is_file():
            raise ValueError("--llada-code must point to the upstream LLaDA checkout")
        sys.path.insert(0, str(source))
        from model.modeling_llada import LLaDAModelLM

        self.torch = torch
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        config = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
        if not hasattr(config, "train_max_sequence_length"):
            config.train_max_sequence_length = config.max_sequence_length
        if hasattr(config, "flash_attention"):
            config.flash_attention = False
        self.model = (
            LLaDAModelLM.from_pretrained(
                model_id, config=config, trust_remote_code=True, torch_dtype=torch.bfloat16
            )
            .to(device)
            .eval()
        )
        self.token_ids: dict[str, int | None] = {}

    def token_id(self, code: str) -> int | None:
        if code not in self.token_ids:
            ids = self.tokenizer.encode(" " + code, add_special_tokens=False)
            self.token_ids[code] = int(ids[0]) if len(ids) == 1 else None
        return self.token_ids[code]

    def score(self, episode: Episode, service: str, slots: int = 8, supports: int = 7) -> dict:
        if slots < 1 or supports < 1:
            raise ValueError("slots and supports must be positive")
        torch = self.torch
        legend = build_legend(
            vocabulary(service), is_single_token=lambda c: self.token_id(c) is not None
        )
        if str(self.device).startswith("cuda"):
            torch.cuda.synchronize()
        start = time.perf_counter()
        system, prompt = build_block_prompt(
            question=episode.question,
            failed_tools=episode.proposal_tools,
            failed_raw=episode.raw_output,
            legend=legend,
            block_size=slots,
            evidence=(),
        )
        messages = [{"role": "user", "content": f"{system}\n\n{prompt}"}]
        try:
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        except Exception:
            text = f"{system}\n\n{prompt}\n\nAssistant:"
        prefix = self.tokenizer.encode(text + "\nOBLIGATION BLOCK:", add_special_tokens=False)
        suffix = self.tokenizer.encode("\nEND BLOCK\n", add_special_tokens=False)
        inputs = torch.tensor([prefix + [self.mask_id] * slots + suffix], device=self.device)
        codes = list(legend.codes)
        code_ids = torch.tensor(
            [self.token_id(c) for c in codes], dtype=torch.long, device=self.device
        )
        with torch.no_grad():
            logits = self.model(inputs).logits
        distributions, legend_mass = [], []
        for offset in range(slots):
            row = logits[0, len(prefix) + offset].float()
            legend_mass.append(float(torch.softmax(row, dim=-1)[code_ids].sum()))
            probs = torch.softmax(row[code_ids], dim=-1)
            distributions.append(
                {
                    NONE if c == legend.none_code else legend.code_to_effect[c]: float(probs[i])
                    for i, c in enumerate(codes)
                }
            )
        ranked = kbest_inclusion_sets(distributions, supports)
        if len(ranked) < supports:
            raise ValueError("The operation vocabulary does not contain enough distinct supports")
        if str(self.device).startswith("cuda"):
            torch.cuda.synchronize()
        return {
            "supports": [list(c.obligations) for c in ranked],
            "inclusion_scores": inclusion_scores(distributions),
            "slot_distributions": distributions,
            "legend_mass": legend_mass,
            "what_seconds": time.perf_counter() - start,
            "slots": slots,
        }
