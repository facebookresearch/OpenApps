#!/usr/bin/env bash
#
# SLURM wrapper around launch_parallel_agents.py, for sweeps against a
# self-hosted model.
#
# Requests a CPU allocation, finds the node serving VLLM_MODEL on :8000, and
# runs the sweep inside the allocation, MAX_PARALLEL jobs at a time, pointed
# at that node. Replaces scripts/conduct_slurm.sh: the sweep is whatever
# launch_parallel_agents.py builds (task files x agents x app variations x
# tasks x repeats), and every job gets its own port window, logs_dir and
# databases.
#
# Submit:
#   VLLM_MODEL=google/gemma-4-31B-it sbatch scripts/sweep_slurm.sh \
#     agents=gemma-4-31B-computer-use \
#     'parallel_tasks.task_names=[add_meeting_with_dennis]' 'parallel_tasks.app_variations=[[]]'
#
#   VLLM_MODEL=google/gemma-4-31B-it MAX_PARALLEL=8 sbatch scripts/sweep_slurm.sh \
#     agents=gemma-4-31B-computer-use tasks=longer_horizon.yaml \
#     parallel_tasks.repeats=3 browsergym_env_args.max_steps=40
#
# VLLM_MODEL must match the served model id (the agent config's model_name).
# Every self-hosted agent in one sweep is pointed at the same node.
#
# Pin the node instead of discovering it:
#   VLLM_HOST=node-001 VLLM_MODEL=... sbatch scripts/sweep_slurm.sh ...
#
# Print the command instead of running it (works off the cluster too):
#   DRY_RUN=1 VLLM_HOST=localhost VLLM_MODEL=... ./scripts/sweep_slurm.sh ...
#
# Any args are forwarded verbatim to launch_parallel_agents.py.
#
# The account, QOS and partition below are placeholders -- sbatch will reject
# the job until they name a real allocation. Override at submit time, which
# takes precedence over these lines:
#
#   sbatch --account=... --qos=... --partition=... scripts/sweep_slurm.sh
#
#SBATCH --job-name=openapps-sweep
#SBATCH --account=example_replace_me
#SBATCH --qos=qos_example_replace_me
#SBATCH --partition=partition_example_replace_me
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=2
#SBATCH --gpus-per-node=0
#SBATCH --mem=10G
#SBATCH --time=400
#SBATCH --comment="openapps sweep"
#SBATCH --output=slurm-%j.out

set -uo pipefail

# ---- Configuration (override via environment) -------------------------------
VLLM_MODEL="${VLLM_MODEL:-}"            # served model id to look for (required)
VLLM_PORT="${VLLM_PORT:-8000}"          # port vLLM listens on
VLLM_HOST="${VLLM_HOST:-}"              # set to skip discovery
MAX_PARALLEL="${MAX_PARALLEL:-4}"       # jobs to run at once in this allocation
DRY_RUN="${DRY_RUN:-}"                  # set to print the command instead

if [ -z "$VLLM_MODEL" ]; then
  echo "ERROR: set VLLM_MODEL to the model the vLLM job serves, e.g." >&2
  echo "       VLLM_MODEL=google/gemma-4-31B-it sbatch scripts/sweep_slurm.sh ..." >&2
  exit 1
fi

# Run from the repo root (parent of this script's directory). Under sbatch the
# script runs from a spool copy, so prefer the directory it was submitted from.
REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_ROOT" || exit 1

# ---- vLLM node discovery ----------------------------------------------------
probe_model() {
  # Return 0 if <node>:<port>/v1/models responds and advertises VLLM_MODEL.
  local node="$1"
  curl -s --max-time 5 "http://${node}:${VLLM_PORT}/v1/models" 2>/dev/null \
    | grep -qF "$VLLM_MODEL"
}

discover_vllm_host() {
  # Expand this user's running jobs' nodelists, exclude the current job's node,
  # and pick the first candidate serving VLLM_MODEL.
  local self_node candidates node
  self_node="$(hostname -s)"

  mapfile -t candidates < <(
    squeue --me -t R -h -o '%N' \
      | while read -r nodelist; do
          [ -n "$nodelist" ] && scontrol show hostnames "$nodelist"
        done \
      | sort -u
  )

  for node in "${candidates[@]}"; do
    [ "$node" = "$self_node" ] && continue
    echo "[discovery] probing http://${node}:${VLLM_PORT}/v1/models" >&2
    if probe_model "$node"; then
      echo "$node"
      return 0
    fi
  done
  return 1
}

if [ -z "$VLLM_HOST" ]; then
  echo "[discovery] searching for a node serving '$VLLM_MODEL' on :$VLLM_PORT"
  if ! VLLM_HOST="$(discover_vllm_host)"; then
    echo "ERROR: no running job found serving '$VLLM_MODEL' on :$VLLM_PORT." >&2
    echo "       Confirm the vLLM GPU job is running (squeue --me), or pin the" >&2
    echo "       node explicitly with VLLM_HOST=<node>." >&2
    exit 1
  fi
  echo "[discovery] selected vLLM node: $VLLM_HOST"
else
  echo "[discovery] using pinned VLLM_HOST=$VLLM_HOST"
  if ! probe_model "$VLLM_HOST"; then
    echo "ERROR: VLLM_HOST=$VLLM_HOST is not serving '$VLLM_MODEL' on :$VLLM_PORT." >&2
    exit 1
  fi
fi

# ---- Run the sweep -----------------------------------------------------------
# mode=local: this allocation runs the jobs itself, MAX_PARALLEL at a time.
# `++` because a few agent configs (dummy, the observation presets) do not
# declare hostname; every agent's args accept it. port is only declared by the
# model-backed agents -- dummy's args reject it -- so it is passed only when it
# differs from their default, and then plainly, so a mismatch fails loudly.
# WANDB_MODE is honored if set (e.g. offline). Forwarded args come last and win.
cmd=(
  uv run launch_parallel_agents.py
  mode=local
  "max_parallel=${MAX_PARALLEL}"
  "++agent.hostname=${VLLM_HOST}"
)
[ "$VLLM_PORT" != "8000" ] && cmd+=("agent.port=${VLLM_PORT}")
cmd+=(use_wandb=True "$@")

if [ -n "$DRY_RUN" ]; then
  printf '%q ' "${cmd[@]}"
  echo
  exit 0
fi
exec "${cmd[@]}"
