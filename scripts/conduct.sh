#!/usr/bin/env bash
#
# Launch multiple agent runs in parallel, one per agent, with a fixed-size
# worker pool. Each run's stdout/stderr is written to its own file under
# log_outputs/.
#
# COUNT sets the total number of runs; agents are picked round-robin from
# AGENTS, cycling back to the start when COUNT exceeds the number of agents.
#
# Usage:
#   ./scripts/conduct.sh
#   COUNT=6 AGENTS="dummy claude_4_sonnet" MAX_PARALLEL=2 ./scripts/conduct.sh
#   ./scripts/conduct.sh use_wandb=True task_name=add_meeting_with_dennis
#
# Any extra CLI args are forwarded verbatim to launch_agent.py (Hydra overrides).

set -uo pipefail

# ---- Configuration (override via environment) -------------------------------
AGENTS="${AGENTS:-dummy}"               # space-separated list of config/agent/<name>
MAX_PARALLEL="${MAX_PARALLEL:-4}"       # max concurrent runs
HEADLESS="${HEADLESS:-True}"            # run browser headless for parallelism
LOG_DIR="${LOG_DIR:-log_outputs}"       # per-run log directory

read -r -a AGENT_LIST <<< "$AGENTS"     # split AGENTS into an array
N_AGENTS="${#AGENT_LIST[@]}"
COUNT="${COUNT:-$N_AGENTS}"             # total number of runs (round-robin over agents)

# ---- Setup ------------------------------------------------------------------
# Run from the repo root (parent of this script's directory).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

RUN_STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
LOG_SUBDIR="${LOG_DIR}/${RUN_STAMP}"
mkdir -p "$LOG_SUBDIR"

# Shared W&B group so all runs from this invocation aggregate together in a
# report. Override with WANDB_GROUP; defaults to the batch timestamp.
WANDB_GROUP="${WANDB_GROUP:-batch-${RUN_STAMP}}"

EXTRA_ARGS=("$@")  # forwarded Hydra overrides

echo "Agents:        $AGENTS"
echo "Total runs:    $COUNT"
echo "Max parallel:  $MAX_PARALLEL"
echo "Headless:      $HEADLESS"
echo "W&B group:     $WANDB_GROUP"
echo "Logs:          $LOG_SUBDIR"
[ "${#EXTRA_ARGS[@]}" -gt 0 ] && echo "Extra args:    ${EXTRA_ARGS[*]}"
echo

# ---- Worker pool ------------------------------------------------------------
throttle() {
  # Block until fewer than MAX_PARALLEL background jobs are running.
  while [ "$(jobs -rp | wc -l | tr -d ' ')" -ge "$MAX_PARALLEL" ]; do
    wait -n 2>/dev/null || sleep 1
  done
}

pids=()
for ((i = 0; i < COUNT; i++)); do
  throttle
  agent="${AGENT_LIST[i % N_AGENTS]}"
  run_idx="$(printf '%03d' "$i")"
  log_file="${LOG_SUBDIR}/run-${run_idx}-agent-${agent}.log"
  # Each run gets its own 100-port window (5001 + job_id*100, see
  # AgentLauncher._job_port_offset). Without it every run in the pool scans
  # from 5001 at once, two pick the same free port before either web app has
  # bound it, and the loser exits with "address already in use" while its
  # health check finds the winner's app. Wrapped at 500 so the window stays
  # under 65535; runs 500 apart are never alive at the same time.
  job_id=$((i % 500))
  echo "[launch] run=$i agent=$agent -> $log_file"
  (
    uv run launch_agent.py \
      "agent=${agent}" \
      "++job_id=${job_id}" \
      "browsergym_env_args.headless=${HEADLESS}" \
      "wandb.group=${WANDB_GROUP}" \
      "wandb.job_type=${agent}" \
      "${EXTRA_ARGS[@]}" \
      >"$log_file" 2>&1
    echo "[done]   run=$i agent=$agent exit=$? -> $log_file"
  ) &
  pids+=("$!")
done

# ---- Wait & report ----------------------------------------------------------
fail=0
for pid in "${pids[@]}"; do
  wait "$pid" || fail=1
done

echo
if [ "$fail" -eq 0 ]; then
  echo "All runs completed successfully. Logs in $LOG_SUBDIR"
else
  echo "One or more runs failed. Check logs in $LOG_SUBDIR"
fi
exit "$fail"
