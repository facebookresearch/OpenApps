# Agent Configuration & Setup Guide

## Quickstart: Getting Your Agent to Run

### Prerequisites

To use GPT-5-1 to complete tasks, set your OpenAI API key:

```bash
export OPENAI_API_KEY=YOUR_KEY
```

Alternatively, edit the corresponding line in `config/agent/GPT-5-1.yaml`.

### Supported Clients

We support multiple client types:

- vLLM
- OpenAI 
- Azure
- AWS

To use a different client, change the `client_type` argument in your configuration. Check out `/src/open_apps/agent/vLLM_agent.py` Line 49 and following for specifics about how these clients are called internally.

### Running Your Agent

```bash
uv run launch_agent.py agent=GPT-4o
```
To run a local model with [vLLM](https://docs.vllm.ai/en/latest/), 

1. Launch your local vLLM model: `vllm serve [MODEL_NAME]`. VLLM will tell you your hostname.

2. Launch your agent

```bash
uv run launch_agent.py agent=AGENT_CONFIG agent.hostname=VLLM_HOSTNAME
```

## Configuring Your Policy

Our agent policies are built on top of [AgentLab](https://github.com/ServiceNow/AgentLab). Our setup enables automatic configuration of your prompt with config flags.
Here are some key flags you can configure in your agent's YAML file:

**Observation Flags:**

- `use_axtree`: Enable AXTree observation (accessibility tree)
- `use_screenshot`: Enable screenshot observation
- `use_som`: Add visual marks to screenshots for element identification
- `extract_coords`: Include element coordinates in observations

**History & Memory Flags:**

- `use_history`: Enable action/thought history tracking
- `use_action_history`: Track previous actions taken by the agent
- `use_think_history`: Track previous thoughts/reasoning steps

**Reasoning & Examples Flags:**

- `use_thinking`: Enable chain-of-thought reasoning before actions
- `use_concrete_example`: Include concrete examples in the prompt
- `use_abstract_example`: Include abstract reasoning examples in the prompt

**Custom Prompts:**

- `prompt_txt.system_prompt`: Override the default system prompt
- `prompt_txt.action_prompt`: Define custom action instructions
- `prompt_txt.think_prompt`: Define custom thinking/reasoning instructions

For the complete set of configuration options, see `config/agent/default.yaml`.


## Creating Your Own Agent

If AgentLab's capabilities don't meet your needs, you can create a custom agent.

1. Navigate to `src/open_apps/agent/`
2. Copy and modify the following files:

      - `vLLM_agent.py`
      - `vLLM_prompt.py`

This allows you to build rich, custom agent implementations tailored to your specific requirements.

## Running Evals on a Cluster (SLURM + vLLM + W&B)

The workflow consists of two jobs:

1. A persistent **vLLM GPU job** that serves the model on `:8000`.
2. An **eval CPU job** (`sbatch scripts/conduct_slurm.sh`) that auto-discovers the vLLM node and runs the worker pool against it.

### One-time cluster setup

The repo needs to live on shared storage (e.g. `/example/dir/$USER`
or `/storage/home/$USER`). On a login node:

```bash
# Bring the repo over. Use rsync if you have uncommitted local changes (e.g.
# scripts/conduct.sh) that aren't pushed yet:
rsync -av --exclude .venv /path/to/local/OpenApps/ /example/dir/$USER/OpenApps/
# ...or clone it fresh:
#   git clone <repo-url> /example/dir/$USER/OpenApps

cd /example/dir/$USER/OpenApps

# Python env
curl -LsSf https://astral.sh/uv/install.sh | sh   # install uv if needed
uv sync

# Headless browser
uv run playwright install chromium
uv run playwright install-deps chromium   # system deps (may need sudo/module)

# App setup (OpenJDK 21 for onlineshop, dataset via gdown, spaCy en_core_web_lg)
./setup.sh

# Secrets — do NOT commit this file
cat > .env <<'EOF'
OPENAI_API_KEY=sk-...
WANDB_BASE_URL=...
WANDB_API_KEY=...
EOF
```

`launch_agent.py` calls `load_dotenv()`, so `.env` is picked up automatically. See
[`.env.example`](https://github.com/facebookresearch/OpenApps/blob/main/.env.example) for the
full set of variables read through `.env`.

### Cluster config: the `internal-*` convention

`config/mode/slurm_cluster.yaml` ships with placeholder values (`logs_dir: /example/dir`,
`slurm_account: example_replace_me`, …) that `sbatch` will reject. Rather than editing it and
risking committing your site's paths and account names, `.gitignore` carries an `internal-*`
rule: **any file named `internal-*` stays untracked**. The convention is to keep a private
twin next to the public one:

```bash
cp config/mode/slurm_cluster.yaml config/mode/internal-slurm_cluster.yaml
```

```yaml
# config/mode/internal-slurm_cluster.yaml  (untracked)
# @package _global_
project: open_apps

logs_dir: /your/checkpoint/path/${oc.env:USER}/logs/${project}/${now:%Y-%m-%d_%H-%M-%S}-${agent.model_name}/${job_id}
databases_dir: ${logs_dir}/databases

cluster: slurm

slurm_sweep_launcher:
  gpus_per_node: 0
  nodes: 1
  tasks_per_node: 1
  cpus_per_task: 2
  timeout_min: 400
  slurm_account: your_account
  slurm_qos: your_qos
  slurm_partition: your_partition
  mem_gb: 10
  slurm_srun_args: ["-vv", "--cpu-bind", "none"]
  slurm_comment: "parallel agent tasks"
```

Select it like any other Hydra mode:

```bash
uv run launch_parallel_agents.py mode=internal-slurm_cluster agent=dummy \
    tasks=longer_horizon parallel_tasks.task_names=all use_wandb=True
```

The same pattern applies elsewhere — e.g. `docs/internal-notes.md` for cluster-specific
instructions alongside the public `docs/`.

### 1. Launch the persistent vLLM serve job

Give it a generous `--time` so it isn't killed mid-eval. For example, an
interactive allocation:

```bash
srun --account=example_account --qos=example_qos --partition=example_partition \ --gpus-per-node=1 --time=1440 --pty bash
# then, on the GPU node:
vllm serve google/gemma-4-E2B-it --host 0.0.0.0 --port 8000
```

Confirm it's healthy from its own node:

```bash
srun --overlap --jobid=<vllm-job> --pty curl -s http://localhost:8000/v1/models
# expect: ...google/gemma-4-E2B-it...
```

### 2. Submit the eval job

`scripts/conduct_slurm.sh` requests a CPU allocation, finds the vLLM node, and
runs `scripts/conduct.sh` pointed at it with `use_wandb=True`:

```bash
# smoke test (1 run)
AGENTS=gemma-4-e2b-it COUNT=1 sbatch scripts/conduct_slurm.sh

# larger sweep
AGENTS="gemma-4-e2b-it" COUNT=20 MAX_PARALLEL=4 sbatch scripts/conduct_slurm.sh
```

**Discovery:** the wrapper lists your running jobs (`squeue --me`), expands their
nodelists, excludes the eval node itself, and probes each `http://<node>:8000/v1/models`,
selecting the first one serving `google/gemma-4-E2B-it`. It probes by *serving the
model*, so it doesn't matter how the vLLM job was started (e.g. a `bash`-named
`srun --pty`).

**Override:** skip discovery by pinning the node:

```bash
VLLM_HOST=example_host AGENTS=gemma-4-e2b-it COUNT=1 sbatch scripts/conduct_slurm.sh
```

**Account/QOS/partition:** the `#SBATCH` lines in `scripts/conduct_slurm.sh` are
placeholders. Override them at submit time rather than editing the script — the
command line takes precedence:

```bash
AGENTS=gemma-4-e2b-it COUNT=1 \
  sbatch --account=... --qos=... --partition=... scripts/conduct_slurm.sh
```

The worker pool and its wrapper are configured entirely through the environment.
These are read by the shell, **not** through `.env` — export them at the call site,
or `set -a; source .env; set +a` first:

| Variable | Read by | Default |
| --- | --- | --- |
| `AGENTS` | `scripts/conduct.sh` | `dummy` — space-separated `config/agent/<name>` stems, used round-robin |
| `COUNT` | `scripts/conduct.sh` | number of agents — total runs to launch |
| `MAX_PARALLEL` | `scripts/conduct.sh` | `4` — concurrent runs |
| `HEADLESS` | `scripts/conduct.sh` | `True` |
| `LOG_DIR`, `WANDB_GROUP` | `scripts/conduct.sh` | `log_outputs`, `batch-<timestamp>` |
| `WANDB_MODE` | `wandb` | unset — `offline` skips online logging |
| `VLLM_MODEL`, `VLLM_PORT` | `scripts/conduct_slurm.sh` | the `served_model_name` and port to look for |
| `VLLM_HOST` | `scripts/conduct_slurm.sh` | unset — pin a node to skip auto-discovery |

Extra CLI args are forwarded verbatim to `launch_agent.py` as Hydra overrides.

**Fallback if compute→compute `:8000` is firewalled:** run the eval inside the
vLLM job's own allocation and talk to it over localhost:

```bash
srun --overlap --jobid=<vllm-job> \
  env AGENTS=gemma-4-e2b-it COUNT=1 VLLM_HOST=localhost \
  ./scripts/conduct.sh agent.hostname=localhost use_wandb=True
```

### 3. Check results

- SLURM log: `slurm-<jobid>.out` — confirm discovery selected the vLLM node and
  there are no "Connection error" messages.
- Per-run logs: `log_outputs/<stamp>/run-*.log` — confirm an action is produced.
- W&B: a run should appear in `open_apps_${USER}` (i.e. `open_apps_aaronsmulktis`)
  with the expected `group`/`job_type` and a logged `web_app_url`.
