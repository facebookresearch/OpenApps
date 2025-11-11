
```
uv pip install git+https://github.com/facebookresearch/openapps.git
```


### Manual Installation

- Pre-requisite: install uv (a much faster pip): `pip install uv` (or from [source](https://docs.astral.sh/uv/getting-started/installation/))
<!-- - [If using Conda] Create a fresh venv: `uv venv --python "$(which python)"` -->

0) Clone [repo](https://github.com/facebookresearch/OpenApps)

1) Install packages: `uv sync`

2) Activate environment: `source .venv/bin/activate`

3) Install `playwright install chromium`

/// details | Optionally install for onlineshop (off by default)

`Onlineshop java + spacy configuration`

4) Prepare Java, Webshop data and spacy model: `chmod +x setup.sh` and `./setup.sh` for **Linux X64** or **Mac ARM64** systems
5) Designate Java path: `source setup_javapath.sh` for **Linux X64** or **Mac ARM64** systems
6) Check `java -version` gives you `java version "21.0.1"`
7) Build search engine indexes: `chmod +x setup_pyserini.sh` and `./setup_pyserini.sh`

**Congratulations! The onlineshop is ready to be used. Remember in future, always run `source setup_javapath.sh` to configure Java path before launching onlineshop-related tasks.**

`Map planning usage`

Prerequisite: Java 21.
- Note. By default it is turned off (see `config/apps/maps/default.yaml`); if turned on, wait for ~30 seconds for the planner to run in the backend.

8) Navigate to map: `cd src/web_agent_playground/playground_server/map_app/`
9) Grant access and download necessary files: `chmod +x setup_planner.sh` and `./setup_planner.sh`

Finally, launch with
```
uv run launch.py use_wandb=False apps.onlineshop.enable=True
```
///