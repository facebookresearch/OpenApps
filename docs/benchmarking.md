---
title: Benchmarking models
description: Run the align_v1 suite across hosted APIs and self-hosted models on H100/H200 GPUs, and compare them in the leaderboard preview.
---

# Benchmarking models on align_v1

This page covers running the frozen [leaderboard](leaderboard.md) suite (`align_v1`, 155
episodes per agent) for many models at once: hosted APIs, and open models served with vLLM on
H100 or H200 GPUs through SLURM. The results go into the local leaderboard preview. To submit
a single agent you already ran, go straight to [Submit](leaderboard.md#submit).

There are three entry points. Each builds on the one before it:

| What | When |
| --- | --- |
| `uv run launch_parallel_agents.py benchmark=align_v1 agent=<name>` | one agent, the server (if any) already running ([Leaderboard, step 1](leaderboard.md#1-run-the-suite)) |
| `scripts/benchmark_one.sbatch` | one self-hosted model: a single GPU job serves it and runs the suite against `localhost`, then packs, validates and previews |
| `scripts/benchmark_sweep.py` | many models: submits the vLLM servers, runs each sweep, tears the servers down, collects everything |

All three run the same suite with the same launcher, so their episodes are interchangeable:
`pack` takes logs from any of them.

## Prerequisites

1. **The repository on shared storage**, set up as in [Manual Installation](installation.md)
   (`uv sync`, `uv run playwright install chromium`). Compute nodes run `uv run` from it.
2. **A vLLM environment**, for self-hosted models only. vLLM is deliberately not a dependency
   of this project (its pinned torch/CUDA stack conflicts with the agents'), so it lives in
   its own venv, created once on a login node:

    ```bash
    uv venv --python 3.11 ~/venvs/vllm
    uv pip install --python ~/venvs/vllm/bin/python vllm
    ```

    Set `VLLM_VENV` if yours lives elsewhere. Gemma 4 and Qwen3.6 need a recent vLLM.

    Or skip the venv and serve from the official container (pyxis/enroot clusters).
    `benchmark_one.sbatch` then runs `vllm serve` as a container job step beside the suite.
    Import a pinned tag once to shared storage; pulling `:latest` in every job is slow and
    changes under you:

    ```bash
    enroot import -o /checkpoint/<account>/$USER/containers/vllm-openai-v0.32.0.sqsh \
      docker://vllm/vllm-openai:v0.32.0
    export VLLM_IMAGE=/checkpoint/<account>/$USER/containers/vllm-openai-v0.32.0.sqsh
    ```
3. **A Hugging Face cache with room for the weights** (about 10 GB for the 2B/7B models and
   55 to 70 GB for each of the 27B, 31B and 35B checkpoints). The scripts set
   `HF_HOME=/checkpoint/<account>/$USER/hf` unless you set it yourself; keep it off a
   quota-capped home directory. Gated models (Gemma) need `huggingface-cli login`, or `HF_TOKEN`,
   once.
4. **API keys**, for hosted models, in the shell or in `.env` (see `.env.example`):

    | Agent configs | Variables |
    | --- | --- |
    | `GPT-5.5-*` | `GPT55_API_KEY` |
    | `claude_4_sonnet` (Bedrock) | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (`AWS_SESSION_TOKEN`); `BEDROCK_MODEL_ID` to change the model id |
    | `gemini-2.5-flash` | `GEMINI_API_KEY`, `GEMINI_HOSTNAME` (an OpenAI-compatible Gemini proxy) |
    | vLLM models | none. Optionally `VLLM_API_KEY`: the server and every vLLM agent config read it (default `AMI_RULZ`) |

5. **SLURM settings.** Either keep your site's `config/mode/internal-slurm_cluster.yaml`
   (gitignored; see [Agents](agents.md#cluster-config-the-internal-convention)), or export
   them. The sweep reads `OPENAPPS_SLURM_ACCOUNT`, `OPENAPPS_SLURM_QOS`,
   `OPENAPPS_SLURM_PARTITION` and `OPENAPPS_SLURM_TIME` (minutes), then sbatch's own
   `SBATCH_ACCOUNT` / `SBATCH_QOS` / `SBATCH_PARTITION`, then the internal mode file. A plain
   `sbatch` reads the `SBATCH_*` variables natively:

    ```bash
    export SBATCH_ACCOUNT=<account> SBATCH_QOS=<qos> SBATCH_PARTITION=<h100-or-h200-partition>
    ```

## The models

`scripts/benchmark_models.yaml` is the registry every script reads. `uv run
scripts/benchmark_sweep.py list` prints it, and flags any unset API variables.

| Agent config | Kind | Served checkpoint | H100 80 GB | H200 141 GB |
| --- | --- | --- | --- | --- |
| `dummy` | none (random clicks, for smoke tests) | | | |
| `GPT-5.5-computer-use`, `-abstention`, `-screen-and-text`, `-text-only` | api | | | |
| `claude_4_sonnet` | api (Bedrock) | | | |
| `gemini-2.5-flash` | api (proxy) | | | |
| `gemma-4-2B-computer-use` | vllm | `google/gemma-4-E2B-it` | 1 | 1 |
| `UI-TARS-1.5-7B` | vllm | `ByteDance-Seed/UI-TARS-1.5-7B` | 1 | 1 |
| `Qwen3.6-27B-computer-use` | vllm | `Qwen/Qwen3.6-27B` | 2 | 1 |
| `Qwen3.6-35B-A3B-computer-use` | vllm | `Qwen/Qwen3.6-35B-A3B` | 2 | 1 |
| `gemma-4-31B-computer-use`, `gemma-4-31B-coords` | vllm | `google/gemma-4-31B-it` | 2 | 1 |

The GPU counts are the tensor-parallel size, for bf16 weights at
`gpu_memory_utilization: 0.90` and `max_model_len: 32768`. The rule: the weights (2 bytes per
parameter, and *every* expert of a MoE, even though only 3B are active per token) must leave
room for the KV cache of about 8 concurrent episodes.

- **2B/7B (10 to 16 GB of weights):** one GPU of either kind.
- **27B/31B dense (54 to 62 GB):** these do fit on a single H100, but only 10 to 18 GB is left
  for the KV cache, which caps concurrency and can run out of memory on long contexts. Use two
  H100s, or one H200 (about 65 GB of KV).
- **35B-A3B MoE (about 70 GB):** two H100s or one H200. It decodes like a 3B model, so it is the
  fastest of the large ones.

More GPUs than listed only add throughput. `benchmark_one.sbatch` uses every GPU it is
given as tensor parallel, and warns when it gets fewer than the registry recommends.

The suite pins the step budget (30), the device per cell and the termination rule, so the
"pair with `browsergym_env_args=screenshot` / `device=laptop`" hints in some agent configs
apply to ad-hoc runs only. `benchmark=align_v1` refuses `device=` and `apps/...` overrides.

## Smoke test first

Two checks, both cheap, before you spend GPU hours.

**The pipeline, with no model at all.** The `dummy` agent clicks at random, so it needs no
GPU or key. It runs the full suite, then pack, validate and preview, so every stage after the
model is exercised:

```bash
AGENT=dummy SHARDS=6 bash scripts/benchmark_one.sbatch
# or: uv run scripts/benchmark_sweep.py run dummy --mode local --shards 6 --collect
```

On a busy shared login node with 6 shards, composing the suite took about 15 minutes (each
shard composes all 155 runs, about 5 s each) and the episodes then finished at about 1.7 per
minute, so roughly 1.5 hours in all. The dummy runs every episode to its 30-step limit (it
never reports), so every episode is unaligned. Expect `validate` to print `OK` (or
`PARTIAL`, listing the cells, if you collect early) and a page at
`<logs>/leaderboard.html` with one `dummy` row. You can run `collect` at any point to check
the later stages on the episodes finished so far.

**The model, with two episodes.** `SMOKE=1` (or `--smoke`) runs a two-episode ad-hoc sweep
(`add_call_mom_to_my_todo` and `message_bob_to_meet` on the reference cell, 10 steps, under
the suite's termination rule) instead of the suite. It shows that the server comes up, the
model answers, and its actions parse, for a few minutes of GPU time:

```bash
AGENT=UI-TARS-1.5-7B SMOKE=1 sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch
uv run scripts/benchmark_sweep.py submit-one vllm --gpu-type h200 --smoke   # every vLLM model
```

Smoke runs carry no suite, so they are previewed but never packed.

## One self-hosted model on one GPU job (the simplest path)

`scripts/benchmark_one.sbatch` does everything on the GPU node: it starts vLLM bound to
`127.0.0.1`, waits for `/v1/models` to list the model, runs the suite as `SHARDS` (default 8)
concurrent local processes against it (vLLM batches their requests), stops the server, and
then packs, validates and renders the preview. Nothing needs to reach the GPU node from
another machine, so firewalled compute-to-compute ports don't matter.

Submit from the repository root (the job finds the checkout through `SLURM_SUBMIT_DIR`), and
make sure the `#SBATCH --output` directory exists; SLURM silently drops a log whose directory
is missing:

```bash
cd <repo>; mkdir -p /storage/home/$USER/logs
export SBATCH_ACCOUNT=<account> SBATCH_QOS=<qos> SBATCH_PARTITION=<partition>

# H200 partition: one GPU for every model in the registry
AGENT=UI-TARS-1.5-7B                sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch
AGENT=Qwen3.6-27B-computer-use      sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch
AGENT=Qwen3.6-35B-A3B-computer-use  sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch
# two prompt variants of one checkpoint share the server (one agents= sweep)
AGENT="gemma-4-31B-computer-use gemma-4-31B-coords" sbatch --gpus-per-node=1 scripts/benchmark_one.sbatch

# H100 partition: two GPUs for the 27B/31B/35B models
AGENT=Qwen3.6-27B-computer-use      sbatch --gpus-per-node=2 scripts/benchmark_one.sbatch
```

Or let the registry pick the GPU count, and submit one job per checkpoint:

```bash
uv run scripts/benchmark_sweep.py submit-one vllm --gpu-type h200            # 5 jobs
uv run scripts/benchmark_sweep.py submit-one Qwen3.6-27B-computer-use --gpu-type h100 --dry-run
```

Inside an interactive allocation (`srun --gpus-per-node=1 --pty bash`) run the same script with
`bash` instead of `sbatch`. Variables: `AGENT` (required; space-separated agents must share a
checkpoint), `SHARDS`, `SEED_SET` (`public` or `heldout`), `SMOKE=1`, `LOGS_ROOT`, `ORG`,
`VLLM_PORT`, `VLLM_API_KEY`, `VLLM_VENV` or `VLLM_IMAGE`, `HF_HOME`, `VLLM_STARTUP_TIMEOUT`. Any arguments
after the script are Hydra overrides for every run, e.g. `use_wandb=True`.

## Many models: the sweep

`scripts/benchmark_sweep.py run` plans the selected models into *launch groups*, one
`launch_parallel_agents.py` sweep each, and runs them:

- every vLLM model with the same checkpoint shares one server and one sweep, through the
  launcher's native agent sweep (`agents=gemma-4-31B-computer-use,gemma-4-31B-coords`);
- all the hosted models (and `dummy`) form one more group, again one `agents=` sweep.

For a vLLM group it submits `scripts/serve_vllm.sbatch` with the registry's GPU count, polls
`squeue` and `http://<node>:<port>/v1/models` until the model is served, runs the sweep with
`agent.hostname=<node>`, waits for the sweep's jobs, and `scancel`s the server, also when
something fails. A group that fails never stops the others.

```bash
uv run scripts/benchmark_sweep.py list
# preview every command, submit nothing
uv run scripts/benchmark_sweep.py run all --gpu-type h200 --dry-run
# every vLLM model, 3 servers at a time, episodes as SLURM CPU jobs; collect at the end
uv run scripts/benchmark_sweep.py run vllm --gpu-type h200 --parallel 3 \
    --mode internal-slurm_cluster --collect
# the hosted models, sharded on this machine
uv run scripts/benchmark_sweep.py run api --mode local --shards 8 --collect
# a server you already started (e.g. with serve_vllm.sbatch)
uv run scripts/benchmark_sweep.py run UI-TARS-1.5-7B --hostname <node>
```

Run it inside `tmux` or `screen` on the login node: it waits for the servers and the sweeps.

- `--mode`: `config/mode/<mode>`. A SLURM mode runs each episode as a CPU job (the default
  when `config/mode/internal-slurm_cluster.yaml` exists). Those jobs talk to the vLLM node over
  the cluster network, so compute-to-compute traffic on `--port` must be allowed; if it is
  not, use `benchmark_one.sbatch`. `local` runs `--shards` processes on this machine.
- `--seed-set heldout`: the verification tier's private seeds (see
  [Verification](leaderboard.md#verification-maintainers)).
- `--smoke`: the two-episode sweep instead of the suite, per group.
- `--separate`: one launch per model, without sharing servers or `agents=` sweeps.
- `--overrides use_wandb=True ...`: Hydra overrides for every launch.
- `--logs-root`: default `log_outputs/align_v1-<stamp>/`. Group `<g>` writes to
  `<logs-root>/<g>/run-*/<job_id>/`, and `_sweep/` holds the per-group and per-shard logs.
- `--collect`: pack, validate and preview once everything has finished (below).

## Monitoring

```bash
squeue --me                                           # servers, single-node jobs, episode arrays
tail -f /storage/home/$USER/logs/openapps-align_v1-<agent>_<jobid>.out   # benchmark_one.sbatch
tail -f <logs-root>/_sweep/shard0.log                 # one shard's episodes
tail -f <logs-root>/_sweep/vllm.log                   # benchmark_one's server
tail -f /storage/home/$USER/logs/openapps-vllm-<agent>_<jobid>.out       # serve_vllm.sbatch
find <logs-root> -name episode.json | wc -l           # episodes finished so far
uv run -m open_apps.leaderboard.preview <logs-root>   # the table so far, any time
```

A shard that hits an infrastructure error (the web app never came up, a crash) moves on to
its next episode (`continue_on_error=true`), lists the failed job ids at the end, and exits
non-zero. Their cells show up as missing in `validate`. Re-run the agent (or just those
cells with an ad-hoc sweep), then pack both logs dirs together.

## Collecting the results

`benchmark_one.sbatch` and `run --collect` end with this step. Run it on its own at any time:

```bash
uv run scripts/benchmark_sweep.py collect <logs-root> [--org <org>] [--agents A B ...]
```

For every agent it finds under the root (from each job's `config.yaml`, so a shared
`agents=` sweep is split correctly), it runs `open_apps.leaderboard.pack` into
`<logs-root>/_bundles/<org>__<agent>__align_v1/` and `open_apps.leaderboard.validate` on
the bundle (`OK`, `PARTIAL` with the missing cells, or `FAIL`). Then it writes
`<logs-root>/leaderboard.html` with `open_apps.leaderboard.preview`, one row per model. To
browse it live instead (from a login node it prints the `ssh -N -L` command for your laptop):

```bash
uv run -m open_apps.leaderboard.preview <logs-root>
uv run -m open_apps.leaderboard.preview /checkpoint/$USER/logs/open_apps <logs-root>   # beside older runs
```

To publish a row, take its bundle from `_bundles/` and follow [Submit](leaderboard.md#submit)
from step 3, with your own `--org`/`--agent` slugs (`pack` again with `--contact`, or edit
nothing and re-pack).

## Cost

One agent is 155 episodes of at most 30 steps (`max_steps`), so **at most 4,650 model calls**.
Episodes end earlier when the agent reports (`send_msg_to_user` / `report_infeasible`), so a
capable model makes noticeably fewer. Each call sends the current screenshot plus the task and
a text history: about 2,000 to 5,000 input tokens (the image dominates) and up to `max_tokens`
(1,000 to 5,000 in the shipped configs) of output.

| | Per agent, upper bound |
| --- | --- |
| Model calls | 155 x 30 = 4,650 |
| Input tokens | ~10 to 25 M |
| Output tokens | ~1 to 3 M (thinking models at the top) |
| `--seed-set` | each extra seed is another 155 episodes |

For the GPU models, wall clock is dominated by decoding, plus about 10 to 20 s per episode to
start its web app and browser. Every launch also composes each of its runs first (about 5 s
per run, so 10 to 15 minutes per agent, before anything is submitted or started).
`benchmark_one.sbatch` overlaps this with loading the checkpoint; a SLURM-mode sweep does it
in the launching process, once per agent in an `agents=` group. With 8 shards on the recommended GPUs, plan on roughly 1 to 3
hours for the 2B/7B models and 3 to 8 hours for the 27B/31B/35B ones, so about 1 to 3 H200-hours
or 2 to 16 H100-hours per model. These are planning figures, not measurements: time the
two-episode `--smoke` run and multiply (155 / 2 episodes, divided by the shards).
`benchmark_one.sbatch` prints the suite's wall time at the end. More shards help until the
GPU is saturated; watch vLLM's `Running: N reqs` lines in its log.

## Adding a model

1. Write `config/agent/<name>.yaml` ([Agents](agents.md)). For a vLLM model set
   `client_type: vllm`, `model_name: <HF repo id>` (vLLM serves the model under that name),
   and `hostname: null`; the key and port come from `default.yaml`.
2. Add it to `scripts/benchmark_models.yaml`: `kind`, and for vLLM `hf_model` (the same repo
   id) and `gpus: {h100: N, h200: M}` by the sizing rule above, plus `max_model_len`,
   `gpu_memory_utilization` or `vllm_args` if it needs them (e.g. a quantized checkpoint, or
   `--limit-mm-per-prompt`). For an API model, list its variables under `env`.
3. `uv run pytest tests/test_benchmark_sweep.py`: it checks that every registry entry composes,
   that a vLLM entry's `hf_model` equals its config's `model_name`, and that every vLLM agent
   config is in the registry.
4. Smoke it (`SMOKE=1`), then run it.

The suite name lives in one place per script: `SUITE` in `scripts/benchmark_sweep.py` and
`benchmark=align_v1` in `scripts/benchmark_one.sbatch`. A new suite version (`align_v2`)
changes those two.
