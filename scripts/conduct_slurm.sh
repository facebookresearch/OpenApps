#!/usr/bin/env bash
#
# SLURM wrapper around scripts/conduct.sh.
#
# Requests a CPU allocation, auto-discovers the node running vLLM (serving the
# target model on :8000), and launches the worker pool pointed at it. This assume
# the eval job is running on the same internal network as Gemme.
#
# Submit:
#   AGENTS=gemma-4-computer-use COUNT=1 sbatch scripts/conduct_slurm.sh
#   AGENTS="gemma-4-computer-use" COUNT=20 MAX_PARALLEL=4 sbatch scripts/conduct_slurm.sh
#
# Override discovery by pinning the node explicitly:
#   VLLM_HOST=node-001 AGENTS=gemma-4-e2b-it COUNT=1 sbatch scripts/conduct_slurm.sh
#
# Any extra CLI args are forwarded verbatim to conduct.sh -> launch_agent.py.
#
# The account, QOS and partition below are placeholders — sbatch will reject the
# job until they name a real allocation. Edit them for your cluster, or leave
# them and override at submit time, which takes precedence over these lines:
#
#   sbatch --account=... --qos=... --partition=... scripts/conduct_slurm.sh
#
#SBATCH --job-name=openapps-eval
#SBATCH --account=example_replace_me
#SBATCH --qos=qos_example_replace_me
#SBATCH --partition=partition_example_replace_me
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=2
#SBATCH --gpus-per-node=0
#SBATCH --mem=10G
#SBATCH --time=400
#SBATCH --comment="parallel agent tasks"
#SBATCH --output=slurm-%j.out

set -uo pipefail

# ---- Configuration (override via environment) -------------------------------
VLLM_MODEL="${VLLM_MODEL:-google/gemma-4-E2B-it}"  # served_model_name to match
VLLM_PORT="${VLLM_PORT:-8000}"                       # port vLLM listens on
VLLM_HOST="${VLLM_HOST:-}"                           # set to skip auto-discovery

# Run from the repo root (parent of this script's directory).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

# ---- vLLM node discovery ----------------------------------------------------
probe_model() {
  # Return 0 if <node>:<port>/v1/models responds and advertises VLLM_MODEL.
  local node="$1"
  curl -s --max-time 5 "http://${node}:${VLLM_PORT}/v1/models" 2>/dev/null \
    | grep -q "$VLLM_MODEL"
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

# ---- Launch the worker pool -------------------------------------------------
# WANDB_MODE is honored if set (e.g. offline); otherwise runs online.
AGENTS="${AGENTS:-gemma-4-e2b-it}" \
COUNT="${COUNT:-1}" \
MAX_PARALLEL="${MAX_PARALLEL:-4}" \
  ./scripts/conduct.sh \
    "agent.hostname=$VLLM_HOST" \
    use_wandb=True \
    "$@"
