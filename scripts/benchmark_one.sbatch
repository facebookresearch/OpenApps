#!/bin/bash
#
# Serve one model with vLLM on this GPU node and run the whole align_v1 suite
# against it over localhost, in one job. Then pack, validate and render the
# leaderboard preview for what it produced. The simplest way to benchmark a
# self-hosted model: nothing has to reach the GPU node from outside.
#
# Submit (GPU counts per model and GPU type: scripts/benchmark_models.yaml,
# or let `uv run scripts/benchmark_sweep.py submit-one <agent>` pick them):
#
#   cd <repo root>; mkdir -p /storage/home/$USER/logs   # the --output dir below
#   export SBATCH_ACCOUNT=<account> SBATCH_QOS=<qos> SBATCH_PARTITION=<h100|h200>
#   AGENT=UI-TARS-1.5-7B             sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch
#   AGENT=gemma-4-31B-computer-use   sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch  # H200
#   AGENT=gemma-4-31B-computer-use   sbatch --gpus-per-node=2 scripts/benchmark_one.sbatch  # H100
#   # two prompt variants of one checkpoint share the server (one agents= sweep):
#   AGENT="gemma-4-31B-computer-use gemma-4-31B-coords" sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch
#
# Or, inside an interactive GPU allocation (srun --gpus-per-node=1 --pty bash):
#
#   AGENT=UI-TARS-1.5-7B bash scripts/benchmark_one.sbatch
#
# Hosted and dummy agents skip the server, so the same script is the pipeline
# smoke test on any machine, GPU or not:
#
#   AGENT=dummy SHARDS=6 bash scripts/benchmark_one.sbatch
#
# Environment (all optional except AGENT):
#   AGENT        config/agent/<name> stems, space-separated (one checkpoint)
#   SHARDS       concurrent episodes (local shards of the sweep); registry default 8
#   SEED_SET     public (default) | heldout
#   SMOKE=1      a 2-run ad-hoc sweep instead of the suite (checks serving + parsing)
#   LOGS_ROOT    default log_outputs/align_v1-<stamp>-<jobid>; runs go under
#                $LOGS_ROOT/<group>/run-*/<job_id>/
#   ORG          org slug for the packed bundle (default: local)
#   VLLM_IMAGE   serve from this container (pyxis) instead of VLLM_VENV: a pinned
#                .sqsh is fastest, e.g. one made once with
#                enroot import -o <shared-storage>/containers/vllm-openai-v0.32.0.sqsh \
#                  docker://vllm/vllm-openai:v0.32.0
#   VLLM_PORT (default: a free local port), VLLM_API_KEY, VLLM_VENV, HF_HOME, VLLM_STARTUP_TIMEOUT (s, default 5400)
# Extra arguments are Hydra overrides for every run (e.g. use_wandb=True).
#
# 8 shards x 30 steps; bigger models take longer. The 24 h --time below is
# headroom, not an estimate.
#
#SBATCH --job-name=openapps-align_v1
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=400G
#SBATCH --time=1440
#SBATCH --comment="openapps align_v1 benchmark"
#SBATCH --output=/storage/home/%u/logs/%x_%j.out

set -uo pipefail

: "${AGENT:?set AGENT=<config/agent name>[ <name> ...]}"
SEED_SET="${SEED_SET:-public}"
SMOKE="${SMOKE:-0}"
ORG="${ORG:-local}"
# A free local port unless one is given. Two jobs can land on one node (it has
# 8 GPUs), and on a fixed 8000 each one's readiness check and episodes could
# reach whichever server answered first.
if [ -z "${VLLM_PORT:-}" ]; then
  VLLM_PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
fi
export VLLM_API_KEY="${VLLM_API_KEY:-AMI_RULZ}"
VLLM_VENV="${VLLM_VENV:-$HOME/venvs/vllm}"
VLLM_IMAGE="${VLLM_IMAGE:-}"
VLLM_STARTUP_TIMEOUT="${VLLM_STARTUP_TIMEOUT:-5400}"

# Under sbatch the script runs from a spool copy; SLURM_SUBMIT_DIR is the repo.
if [ -n "${SLURM_JOB_ID:-}" ] && [ -f "${SLURM_SUBMIT_DIR:-}/launch_parallel_agents.py" ]; then
  REPO="$SLURM_SUBMIT_DIR"
else
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
if [ ! -f "$REPO/launch_parallel_agents.py" ]; then
  echo "ERROR: cannot find the OpenApps checkout; sbatch this from the repo root." >&2
  exit 1
fi
cd "$REPO" || exit 1

# A sandbox / login-shell proxy breaks localhost probes and HF downloads alike.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
# So the shard logs can be followed with tail -f.
export PYTHONUNBUFFERED=1

# ---- GPUs and the registry ------------------------------------------------------
GPU_COUNT=0
GPU_TYPE=h100
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_COUNT="${SLURM_GPUS_ON_NODE:-$(nvidia-smi -L 2>/dev/null | wc -l)}"
  nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 \
    | grep -qi h200 && GPU_TYPE=h200
fi

# shellcheck disable=SC2086  # AGENT is a space-separated list on purpose
if ! EXPORTS="$(uv run --frozen python scripts/benchmark_sweep.py env $AGENT \
      --gpu-type "$GPU_TYPE" ${GPU_COUNT:+--gpus "$GPU_COUNT"})"; then
  echo "ERROR: could not resolve AGENT='$AGENT' in scripts/benchmark_models.yaml" >&2
  exit 1
fi
eval "$EXPORTS"
SHARDS="${SHARDS:-$SHARDS_DEFAULT}"

if [ -n "$MISSING_ENV" ]; then
  echo "ERROR: $AGENT needs: $MISSING_ENV (export them or put them in .env)" >&2
  exit 1
fi

STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
LOGS_ROOT="${LOGS_ROOT:-$REPO/log_outputs/align_v1-${STAMP}${SLURM_JOB_ID:+-$SLURM_JOB_ID}}"
# One log dir per job: several jobs (one per model) share a LOGS_ROOT, and
# fixed names would have each truncate the others' server and shard logs.
SWEEP_DIR="$LOGS_ROOT/_sweep/${GROUP_LABEL}${SLURM_JOB_ID:+-$SLURM_JOB_ID}"
mkdir -p "$SWEEP_DIR"
echo "Agents:    $AGENT ($MODEL_KIND)"
echo "Logs:      $LOGS_ROOT"
echo "Shards:    $SHARDS"
echo "GPUs:      $GPU_COUNT x $GPU_TYPE"

# ---- vLLM on this node, and the suite's shards beside it -------------------------------
# Composing the suite takes minutes per process (each of the 115 runs is a full
# Hydra compose), and so does loading a checkpoint. So both start at once: the
# shards compose, then wait for the server's /health (wait_for_url) before
# their first episode, and the GPU is not idle through the compose.
VLLM_PID=""
PIDS=()
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null; done
  if [ -n "$VLLM_PID" ] && kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "Stopping vLLM ($VLLM_PID)"
    kill "$VLLM_PID"; wait "$VLLM_PID" 2>/dev/null
  fi
}
trap cleanup EXIT
trap 'exit 143' TERM INT

HOST_OVERRIDES=()
if [ "$MODEL_KIND" = "vllm" ]; then
  if [ "$GPU_COUNT" -lt 1 ]; then
    echo "ERROR: $AGENT is self-hosted and this node has no GPU" >&2
    exit 1
  fi
  CKPT_ROOT="${CKPT_ROOT:-/checkpoint/${SLURM_JOB_ACCOUNT:-default}/${USER}}"
  export HF_HOME="${HF_HOME:-${CKPT_ROOT}/hf}"
  mkdir -p "$HF_HOME"
  if [ -n "$VLLM_IMAGE" ]; then
    # vLLM in its own container (pyxis) as an overlapping job step on this
    # node; the suite stays in the repo's uv env on the host. The job's env
    # (HF_HOME, HF_TOKEN, VLLM_API_KEY) passes through; only HF_HOME's
    # filesystem needs mounting.
    if [ -z "${SLURM_JOB_ID:-}" ]; then
      echo "ERROR: VLLM_IMAGE needs a Slurm allocation (sbatch, or srun --pty bash)" >&2
      exit 1
    fi
    VLLM_CMD=(srun --overlap --nodes=1 --ntasks=1 --container-image="$VLLM_IMAGE"
              --container-mounts="$HF_HOME:$HF_HOME" vllm)
    echo "Image:     $VLLM_IMAGE"
  else
    VLLM_BIN="${VLLM_VENV}/bin/vllm"
    [ -x "$VLLM_BIN" ] || VLLM_BIN="$(command -v vllm || true)"
    if [ -z "$VLLM_BIN" ]; then
      echo "ERROR: no vllm binary (looked in $VLLM_VENV/bin and on PATH). Create it once:" >&2
      echo "  uv venv --python 3.11 $VLLM_VENV && uv pip install --python $VLLM_VENV/bin/python vllm" >&2
      echo "or set VLLM_IMAGE to a vllm/vllm-openai image or .sqsh" >&2
      exit 1
    fi
    VLLM_CMD=("$VLLM_BIN")
  fi
  echo "Model:     $VLLM_MODEL (tensor parallel $GPUS, max_model_len $VLLM_MAX_MODEL_LEN)"
  echo "HF_HOME:   $HF_HOME"
  # shellcheck disable=SC2086  # VLLM_EXTRA_ARGS word-splits on purpose
  "${VLLM_CMD[@]}" serve "$VLLM_MODEL" \
    --host 127.0.0.1 --port "$VLLM_PORT" --api-key "$VLLM_API_KEY" \
    --trust-remote-code --tensor-parallel-size "$GPUS" \
    --max-model-len "$VLLM_MAX_MODEL_LEN" \
    --gpu-memory-utilization "$VLLM_GPU_MEM_UTIL" \
    $VLLM_EXTRA_ARGS > "$SWEEP_DIR/vllm.log" 2>&1 &
  VLLM_PID=$!
  echo "vLLM pid $VLLM_PID, log $SWEEP_DIR/vllm.log"
  HOST_OVERRIDES=(
    "agent.hostname=127.0.0.1" "agent.port=$VLLM_PORT"
    "wait_for_url=http://127.0.0.1:${VLLM_PORT}/health"
    "wait_for_url_timeout_s=$VLLM_STARTUP_TIMEOUT"
  )
fi

if [ "$SMOKE" = "1" ]; then
  SUITE_OVERRIDES=(
    "parallel_tasks.task_names=[add_call_mom_to_my_todo,message_bob_to_meet]"
    "parallel_tasks.app_variations=[['apps/theme=default']]"
    "task_termination=on_agent_stop" "browsergym_env_args.max_steps=10"
  )
else
  SUITE_OVERRIDES=("benchmark=align_v1" "benchmark.seed_set=$SEED_SET")
fi

START=$(date +%s)
for ((k = 0; k < SHARDS; k++)); do
  uv run --frozen launch_parallel_agents.py "${SUITE_OVERRIDES[@]}" "$AGENTS_SELECTOR" \
    mode=local "logs_dir=$LOGS_ROOT/$GROUP_LABEL" "${HOST_OVERRIDES[@]}" \
    "shard_index=$k" "num_shards=$SHARDS" continue_on_error=true open_leaderboard=false \
    "$@" > "$SWEEP_DIR/shard$k.log" 2>&1 &
  PIDS+=($!)
done
echo "Started $SHARDS shards; follow with: tail -f $SWEEP_DIR/shard0.log"

if [ -n "$VLLM_PID" ]; then
  # Watch the server while the shards compose: if it dies, so does the job
  # (the EXIT trap stops the shards), instead of every episode failing slowly.
  waited=0
  until curl -sf --max-time 5 -H "Authorization: Bearer $VLLM_API_KEY" \
      "http://127.0.0.1:${VLLM_PORT}/v1/models" | grep -q "$VLLM_MODEL"; do
    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
      echo "ERROR: vLLM exited during startup:" >&2
      tail -40 "$SWEEP_DIR/vllm.log" >&2
      exit 1
    fi
    if [ "$waited" -ge "$VLLM_STARTUP_TIMEOUT" ]; then
      echo "ERROR: vLLM not ready after ${VLLM_STARTUP_TIMEOUT}s" >&2
      exit 1
    fi
    sleep 15; waited=$((waited + 15))
  done
  echo "vLLM serving $VLLM_MODEL after ${waited}s"
fi

FAILED=0
for pid in "${PIDS[@]}"; do wait "$pid" || FAILED=$((FAILED + 1)); done
PIDS=()
echo "Suite finished in $(( ($(date +%s) - START) / 60 )) min; $FAILED/$SHARDS shard(s) reported failures"
cleanup
VLLM_PID=""

# ---- pack, validate, preview ----------------------------------------------------------
# Only this job's agents: `submit-one` gives every group the same LOGS_ROOT,
# and the other groups' runs may still be in flight.
# shellcheck disable=SC2206  # AGENT is a space-separated list on purpose
COLLECT=(--org "$ORG" --agents $AGENT)
[ "$SMOKE" = "1" ] && COLLECT+=(--smoke)
uv run --frozen python scripts/benchmark_sweep.py collect "$LOGS_ROOT" "${COLLECT[@]}"
STATUS=$?
echo "Done. Bundles: $LOGS_ROOT/_bundles  Page: $LOGS_ROOT/leaderboard.html"
exit $(( FAILED > 0 || STATUS != 0 ))
