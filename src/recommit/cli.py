"""Prepare public context, score supports, and run repair as separate stages."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .engine import recover
from .services import load_context, simulator
from .types import Episode, ServiceContext


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="ReCommit with LLaDA WHAT and Qwen HOW")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="Read only public seed state and tool documentation"
    )
    prepare.add_argument("--service", choices=["box", "calendar", "linear", "slack"], required=True)
    prepare.add_argument("--benchmark-root", type=Path, required=True)
    prepare.add_argument("--failures", required=True)
    prepare.add_argument("--output", required=True)
    score = commands.add_parser("score", help="One masked forward per episode")
    score.add_argument("--input", required=True)
    score.add_argument("--output", required=True)
    score.add_argument("--model", default="GSAI-ML/LLaDA-8B-Instruct")
    score.add_argument("--llada-code", required=True)
    score.add_argument("--slots", type=int, default=8)
    score.add_argument("--budget", type=int, default=13)
    repair = commands.add_parser(
        "repair", help="Execute a full repair budget in the local simulator"
    )
    repair.add_argument("--input", required=True)
    repair.add_argument("--supports", required=True)
    repair.add_argument("--output", required=True)
    repair.add_argument("--model", default="Qwen/Qwen3-8B")
    repair.add_argument("--max-new-tokens", type=int, default=768)
    repair.add_argument("--budget", type=int, default=13)
    repair.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error("output already exists; choose a new path")
    if args.command == "prepare":
        context = load_context(args.service, args.benchmark_root)
        data = read(args.failures)
        rows = data if isinstance(data, list) else data.get("rows", data.get("records", []))
        episodes = [asdict(Episode.from_dict(row)) for row in rows]
        if not episodes or len({e["failure_id"] for e in episodes}) != len(episodes):
            parser.error("failure input must be nonempty with unique failure_id values")
        save(args.output, {"context": asdict(context), "episodes": episodes})
        return
    data = read(args.input)
    context = ServiceContext(**data["context"])
    episodes = [Episode.from_dict(row) for row in data["episodes"]]
    if args.budget < 1:
        parser.error("budget must be positive")
    if args.command == "score":
        from .what import LLaDA

        model = LLaDA(args.model, args.llada_code)
        rows = []
        for i, episode in enumerate(episodes):
            rows.append(
                {
                    "failure_id": episode.failure_id,
                    **model.score(episode, context.service, args.slots, (args.budget + 1) // 2),
                }
            )
            print(f"WHAT {i + 1}/{len(episodes)}", flush=True)
        save(
            args.output,
            {
                "input_sha256": fingerprint(data),
                "model": args.model,
                "slots": args.slots,
                "budget": args.budget,
                "records": rows,
            },
        )
    else:
        from .components.generation import QwenGenerator

        scored = read(args.supports)
        if scored["input_sha256"] != fingerprint(data):
            parser.error("supports were scored from different public inputs")
        rows = scored["records"]
        by_id = {r["failure_id"]: r for r in rows}
        if len(by_id) != len(rows) or set(by_id) != {e.failure_id for e in episodes}:
            parser.error("support records do not match the input episodes")
        generator = QwenGenerator(
            args.model, args.max_new_tokens, 0.0, deterministic=True, attn_implementation="sdpa"
        )
        execute = simulator(context)
        outputs = []
        for i, episode in enumerate(episodes):
            record = by_id[episode.failure_id]
            outputs.append(
                recover(
                    episode,
                    context,
                    record["supports"],
                    generator,
                    execute,
                    budget=args.budget,
                    seed=args.seed,
                    what_seconds=record["what_seconds"],
                )
            )
            print(f"HOW/COMMIT {i + 1}/{len(episodes)}", flush=True)
        save(
            args.output,
            {
                "model": args.model,
                "budget": args.budget,
                "seed": args.seed,
                "max_new_tokens": args.max_new_tokens,
                "temperature": 0.0,
                "input_sha256": fingerprint(data),
                "records": outputs,
            },
        )


if __name__ == "__main__":
    main()
