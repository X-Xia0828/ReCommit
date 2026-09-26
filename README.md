# ReCommit

ReCommit is a training-free framework for repairing failed tool-using agent attempts. It uses a diffusion language model to guide structured search over repair operations, an autoregressive language model to generate concrete repair plans, and public execution errors to prune rejected calls.

- `src/recommit/`: operation-set scoring, plan generation, and local execution
- `examples/`: three Linear failure examples
- `tests/`: method and service-adapter checks
- `run_example.sh`: complete example from input preparation to repair

The service adapters support Box, Calendar, Linear, and Slack.

## Installation

Use Python 3.10 or newer and a CUDA-compatible PyTorch installation, then install the dependencies:

```bash
pip install -r requirements.txt
```

Download the LLaDA implementation and Agent-Diff resources:

```bash
mkdir -p third_party data
git clone https://github.com/ML-GSAI/LLaDA.git third_party/LLaDA
git clone https://github.com/agent-diff-bench/agent-diff.git data/agent-diff
```

The Agent-Diff checkout must include the service seed files, tool documentation, and Slack backend source. Fetch any Git LFS resources required by that checkout.

The default checkpoints are `GSAI-ML/LLaDA-8B-Instruct` for operation-set scoring and `Qwen/Qwen3-8B` for plan generation. Local checkpoint paths are also supported.

## Running the Example

Main entry points:

- `run_example.sh`
- `src/recommit/cli.py`

Run the three Linear examples:

```bash
bash run_example.sh
```

The script prepares the inputs, scores operation sets, and generates and executes 13 repair attempts per example. Outputs are saved in `outputs/linear-example/`. Model loading runs in separate processes, so both checkpoints do not need to stay on the GPU together.

To select a GPU and use local checkpoints:

```bash
CUDA_VISIBLE_DEVICES=0 \
LLADA_MODEL=/path/to/LLaDA-8B-Instruct \
HOW_MODEL=/path/to/Qwen3-8B \
OUTPUT_DIR=outputs/linear-run2 \
bash run_example.sh
```

The script also accepts `BUDGET`, `BENCHMARK_ROOT`, `LLADA_CODE`, and `PYTHON` as environment variables. Choose a new output directory for each run; existing output files are not overwritten.

## Running Individual Stages

Run the following commands from the repository root:

```bash
recommit prepare \
  --service linear \
  --benchmark-root data/agent-diff \
  --failures examples/failures.json \
  --output outputs/linear-input.json

CUDA_VISIBLE_DEVICES=0 recommit score \
  --input outputs/linear-input.json \
  --llada-code third_party/LLaDA \
  --slots 8 --budget 13 \
  --output outputs/linear-supports.json

CUDA_VISIBLE_DEVICES=0 recommit repair \
  --input outputs/linear-input.json \
  --supports outputs/linear-supports.json \
  --budget 13 --seed 1234 --max-new-tokens 768 \
  --output outputs/linear-repairs.json
```

Use `box`, `calendar`, or `slack` with `--service` for the other adapters. Input episodes follow the format in `examples/failures.json`: `failure_id`, `test_id`, `question`, `raw_output`, and `proposal_tools`.

Key arguments:

- `--slots`: number of masked LLaDA positions; default `8`
- `--budget`: number of complete repair attempts; default `13`, using seven operation sets with alternating canonical and expressive plans
- `--seed`: generation seed; default `1234`, incremented for each operation-set rank
- `--max-new-tokens`: HOW generation limit; default `768`
- `--model`: checkpoint identifier or local path for the selected stage

HOW uses greedy decoding. Each repair attempt starts from the same initial service state and conditions on the original failure.

## Outputs

Scoring saves ranked operation sets and their scores. Repair saves the generated plans, execution observations, pruning decisions, seeds, and elapsed prefix times. Times include operation-set scoring and exclude checkpoint loading. Task-completion evaluation is separate from repair generation.

The local simulators cover supported Agent-Diff operations and do not contact live APIs. Their defaults follow the benchmark environment; read observations expose status and matching IDs rather than complete API responses. They are not full production service backends.

## Tests

Run the CPU tests without loading model checkpoints:

```bash
pip install -e '.[dev]'
pytest -q
```

## Third-Party Dependencies

- [LLaDA](https://github.com/ML-GSAI/LLaDA): upstream diffusion language model implementation
- [LLaDA-8B-Instruct](https://huggingface.co/GSAI-ML/LLaDA-8B-Instruct) and [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B): pretrained model checkpoints used in the provided example
- [Agent-Diff](https://github.com/agent-diff-bench/agent-diff): public service interfaces and seed-state resources
- PyTorch, Transformers, Accelerate, NumPy, and Einops: model dependencies

Model weights, upstream model implementations, and benchmark resources are downloaded separately. Please retain their accompanying licenses.
