- Pre-requisite: install uv (a much faster pip): `pip install uv` (or from [source](https://docs.astral.sh/uv/getting-started/installation/))
<!-- - [If using Conda] Create a fresh venv: `uv venv --python "$(which python)"` -->

0) Clone [repo](https://github.com/facebookresearch/OpenApps)

1) Install packages: `uv sync`

2) Activate environment: `source .venv/bin/activate`

3) Install `playwright install chromium`

That is the whole installation. **Every app runs from `uv sync` with no
further setup** — no downloads and no model weights. Launch with:

```
uv run launch.py
```

The online shop's catalog is checked in too; see [Online Shop](onlineshop.md)
for how to vary or rebuild it.
