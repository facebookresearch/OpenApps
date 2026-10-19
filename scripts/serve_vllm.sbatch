#!/bin/bash
#
# Launch a persistent vLLM server for a self-hosted agent model.
#
# The companion to scripts/conduct_slurm.sh: this job *serves* the model, that
# one finds it and runs the eval against it. Keep this one running for the whole
# sweep -- when it exits, the node is released and every eval that was pointed
# at it starts failing with a bare `RetryError: Connection error`.
#
# Submit:
#   VLLM_MODEL=ByteDance-Seed/UI-TARS-1.5-7B sbatch scripts/serve_vllm.sbatch
#   VLLM_MODEL=Qwen/Qwen3-VL-8B-Instruct GPUS=2 sbatch scripts/serve_vllm.sbatch
#
# Then point the eval at it (discovery greps the served model id, so pass the
# same VLLM_MODEL):
#   VLLM_MODEL=ByteDance-Seed/UI-TARS-1.5-7B AGENTS=UI-TARS-1.5-7B COUNT=1 \
#     sbatch scripts/conduct_slurm.sh
#
# The account, QOS and partition below are placeholders -- sbatch will reject
# the job until they name a real allocation. Override at submit time, which
# takes precedence over these lines:
#
#   sbatch --account=... --qos=... --partition=... scripts/serve_vllm.sbatch
#
#SBATCH --job-name=openapps-vllm
#SBATCH --account=example_replace_me
#SBATCH --qos=qos_example_replace_me
#SBATCH --partition=partition_example_replace_me
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=200G
#SBATCH --time=1440
#SBATCH --comment="openapps vllm server"
#SBATCH --output=/storage/home/%u/logs/%x_%j.out

set -uo pipefail

# ---- Configuration (override via environment) -------------------------------
VLLM_MODEL="${VLLM_MODEL:-ByteDance-Seed/UI-TARS-1.5-7B}"
VLLM_PORT="${VLLM_PORT:-8000}"
GPUS="${GPUS:-1}"                       # tensor-parallel size; 7B fits on one

# vLLM is deliberately NOT a dependency of this project -- it pulls a pinned
# torch + CUDA stack that would fight the one the agents and playwright need,
# and nothing in OpenApps imports it. It lives in its own venv, created once:
#
#   uv venv --python 3.11 "$HOME/venvs/vllm"
#   uv pip install --python "$HOME/venvs/vllm/bin/python" vllm
#
# Point VLLM_VENV elsewhere if yours lives somewhere else.
VLLM_VENV="${VLLM_VENV:-$HOME/venvs/vllm}"

# Weights are tens of GB and /home is capped at 1 TB per user, where hitting the
# cap fails every write silently -- including this job's own log. Cache them on
# /checkpoint instead. Override HF_HOME if your project uses a different tree.
CKPT_ROOT="${CKPT_ROOT:-/checkpoint/${SLURM_JOB_ACCOUNT:-default}/${USER}}"
export HF_HOME="${HF_HOME:-${CKPT_ROOT}/hf}"

# Claude Code and some login shells export a sandbox proxy that compute nodes
# cannot reach, which turns the HuggingFace download into a silent hang.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

mkdir -p "/storage/home/${USER}/logs" "${HF_HOME}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

# Resolve the vllm binary before requesting anything expensive: failing here
# costs nothing, failing after the model download costs twenty minutes.
VLLM_BIN="${VLLM_VENV}/bin/vllm"
if [ ! -x "$VLLM_BIN" ]; then
  VLLM_BIN="$(command -v vllm || true)"
fi
if [ -z "$VLLM_BIN" ]; then
  echo "ERROR: no vllm binary. Looked in ${VLLM_VENV}/bin/vllm and on PATH." >&2
  echo "       Create the env once with:" >&2
  echo "         uv venv --python 3.11 ${VLLM_VENV}" >&2
  echo "         uv pip install --python ${VLLM_VENV}/bin/python vllm" >&2
  echo "       Or set VLLM_VENV to an existing one." >&2
  exit 1
fi

NODE="$(hostname -s)"
echo "Model:     $VLLM_MODEL"
echo "Node:      $NODE"
echo "Port:      $VLLM_PORT"
echo "GPUs:      $GPUS"
echo "HF_HOME:   $HF_HOME"
echo "vllm:      $VLLM_BIN"
echo

# Record the node so a later eval can skip discovery: VLLM_HOST=$(cat ...).
HOST_FILE="/storage/home/${USER}/logs/vllm_host_${SLURM_JOB_ID}.txt"
echo "$NODE" > "$HOST_FILE"
echo "Node written to $HOST_FILE"
echo

# --host 0.0.0.0 is what makes this reachable from the *eval* node. Bound to
# localhost it still answers its own health check and nothing else, which is a
# slow way to discover the problem.
exec "$VLLM_BIN" serve "$VLLM_MODEL" \
  --host 0.0.0.0 \
  --port "$VLLM_PORT" \
  --api-key AMI_RULZ \
  --trust-remote-code \
  --tensor-parallel-size "$GPUS"
