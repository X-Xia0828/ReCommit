#!/usr/bin/env bash
# Run the bundled Linear example after following the README installation steps.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

PYTHON="${PYTHON:-python}"
BENCHMARK_ROOT="${BENCHMARK_ROOT:-data/agent-diff}"
LLADA_CODE="${LLADA_CODE:-third_party/LLaDA}"
LLADA_MODEL="${LLADA_MODEL:-GSAI-ML/LLaDA-8B-Instruct}"
HOW_MODEL="${HOW_MODEL:-Qwen/Qwen3-8B}"
OUTPUT_DIR="${OUTPUT_DIR:-outputs/linear-example}"
BUDGET="${BUDGET:-13}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

if [[ ! -d "$BENCHMARK_ROOT" || ! -f "$LLADA_CODE/model/modeling_llada.py" ]]; then
  echo "Missing Agent-Diff or LLaDA checkout. Follow the README installation steps." >&2
  exit 1
fi
for name in input supports repairs; do
  if [[ -e "$OUTPUT_DIR/$name.json" ]]; then
    echo "Output already exists: $OUTPUT_DIR/$name.json. Set OUTPUT_DIR to a new directory." >&2
    exit 1
  fi
done
mkdir -p -- "$OUTPUT_DIR"

echo "[1/3] Preparing public inputs"
"$PYTHON" -m recommit.cli prepare \
  --service linear \
  --benchmark-root "$BENCHMARK_ROOT" \
  --failures examples/failures.json \
  --output "$OUTPUT_DIR/input.json"

echo "[2/3] Scoring operation sets with LLaDA"
"$PYTHON" -m recommit.cli score \
  --input "$OUTPUT_DIR/input.json" \
  --llada-code "$LLADA_CODE" \
  --model "$LLADA_MODEL" \
  --slots 8 --budget "$BUDGET" \
  --output "$OUTPUT_DIR/supports.json"

echo "[3/3] Realizing and executing repairs"
"$PYTHON" -m recommit.cli repair \
  --input "$OUTPUT_DIR/input.json" \
  --supports "$OUTPUT_DIR/supports.json" \
  --model "$HOW_MODEL" \
  --budget "$BUDGET" --seed 1234 --max-new-tokens 768 \
  --output "$OUTPUT_DIR/repairs.json"

echo "Done. Results: $OUTPUT_DIR/repairs.json"
