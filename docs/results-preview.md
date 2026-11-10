# Viewing your own runs locally

To see how your models compare, point the results preview at the directory your runs are in.
It finds every run below it, however the sweeps are nested, and ranks one row per model by
task success across every task it ran:

```bash
uv run -m open_apps.results_preview                       # everything under log_outputs/
uv run -m open_apps.results_preview /checkpoint/$USER/logs/open_apps
uv run -m open_apps.results_preview sweep sweep-rerun     # several directories at once
uv run -m open_apps.results_preview log_outputs --agent 'Qwen*' --task 'todo_*'
```

Each row shows the success rate with its 95% (Wilson) interval, the episodes and distinct
tasks behind it, how many runs errored, and the mean number of steps. Click a model to drill
down: "N episodes of M tasks", then success per task and per app variation, any errored runs
with their error, and the directories the runs came from.

## The page

The preview writes nothing. It serves the page from memory and opens your browser. Two small
links on the page save it as a single self-contained HTML file (it opens from disk and fetches
nothing) or as a PDF. `--out results.html` or `--out results.pdf` writes the file and exits
instead of serving.

On a machine without a display, such as a cluster login node, it prints the `ssh -N -L` command
that brings the page to your laptop. Set `OPENAPPS_SSH_HOST` to the name your laptop uses for
that machine (the alias in your `~/.ssh/config`) and the command is ready to paste:

```bash
export OPENAPPS_SSH_HOST=my-cluster
uv run -m open_apps.results_preview /checkpoint/$USER/logs/open_apps
# No display here. From your laptop, forward the port and open the page:
#   ssh -N -L 8765:localhost:8765 my-cluster
#   open http://localhost:8765/
```

If it finds no runs, it says where it looked and how to get them there.

`launch_parallel_agents.py` opens the preview by itself when a local sweep run from a terminal
finishes. It covers the logs root's parent (`log_outputs/` by default), so the new runs are
ranked among the earlier ones. A SLURM sweep prints the command to run once its jobs finish.
`open_leaderboard=false` turns both off.

## What it reads

A run is a BrowserGym experiment directory. The preview reads two small files per run and
nothing else:

- `summary_info.json` in the experiment directory. A run succeeded when `cum_reward >= 1.0`,
  the same rule as the launcher's W&B `success`. A run with `err_msg` set hit an
  infrastructure error (the web app or model server failed), not an agent failure. It is left
  out of the rate and counted in the *Errored* column. `n_steps` gives the mean steps.
- `config.yaml` in the job directory above it (written by the launcher). The model is the
  agent's `model_pretty_name`, falling back to `model_name`. It also gives the `task_name`, the
  sweep's `app_overrides` (the variation) and the seeds.

Re-runs of the same cell (agent, task, variation and seed) count once. An errored run loses to
one that did not error; otherwise the later run wins. So you can point it at a sweep together
with its re-run of the crashed jobs.

It is a quick look, not a benchmark. Nothing checks that the models ran the same tasks, so
compare the *Episodes* and *Tasks* columns as well as the rate.

## Copying runs from a cluster

A run directory is mostly screenshots and step pickles. To view cluster runs on a laptop, copy
only the two files the preview reads (leaving out W&B's own `config.yaml` copies and the
app databases):

```bash
rsync -am --exclude='wandb/' --exclude='databases/' --include='*/' \
    --include='summary_info.json' --include='config.yaml' --exclude='*' \
    <cluster>:<logs>/ ./runs/
uv run -m open_apps.results_preview runs/
```
