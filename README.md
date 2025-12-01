
<div align="center">

 #  <img width="45" height="45" alt="image" src="https://github.com/user-attachments/assets/6c409d42-6f3a-4a62-be7f-57793d9dad9d" /> OpenApps
 
*Building Blocks for Digital Agents Research*

[📒 docs](https://facebookresearch.github.io/OpenApps/)  | [📑 ArXiV](https://arxiv.org/abs/2511.20766)
</div>



## Install

```
uv pip install git+https://github.com/facebookresearch/openapps.git
```


### Manual Installation

- Pre-requisite: install uv (a much faster pip): `pip install uv` (or from [source](https://docs.astral.sh/uv/getting-started/installation/))
<!-- - [If using Conda] Create a fresh venv: `uv venv --python "$(which python)"` -->

1) Install packages: `uv sync`
2) Activate environment: `source .venv/bin/activate`
3) Install `playwright install chromium`

<details > 
<summary>
 Optionally install for onlineshop (off by default)
</summary>

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
</details>

## Run OpenApps

Simply run:

```bash
uv run launch.py 
```

Each app can be modified with variables available in `config/apps`. You can override any of these via command line:

```bash
uv run launch.py app.todo.title='Super Todo'
```

#### App variations
OpenApps comes with pre-defined variations that can affect the content and appearance of apps. For example, to launch apps with dark mode:

```bash
export APPEARANCE="dark_theme" 
uv run launch.py apps/calendar/appearance=$APPEARANCE apps/maps/appearance=$APPEARANCE apps/start_page/appearance=$APPEARANCE apps/messenger/appearance=$APPEARANCE
```

To launch the apps with adversarial content:
```bash
export CONTENT="adversarial_descriptions" 
uv run launch.py apps/calendar/content=$CONTENT apps/maps/content=$CONTENT apps/start_page/content=$CONTENT apps/messenger/content=$CONTENT apps/todo/content=$CONTENT apps/pop_ups=$CONTENT
```

Options:
- content: `default, long_descriptions, german, misleading_descriptions`
- appearance: `default, dark_theme, black_and_white, challenging_font`

To launch popups, set `apps/pop_ups=adversarial_descriptions`.

You can see the specific variables for each defined in the individual apps. For example, `config/apps/maps/appearance/dark_theme.yaml`.

## Launch Agent

Launch an agent to perform a task:

```
uv run launch_agent.py
```

To see the agent solving the task live:
```
uv run launch_agent.py browsergym_env_args.headless=False
```

You can specify the agent of your choice with the `agent=` argument. For example `agent=dummy` is a simple agent that clicks randomly on any buttons, great for exploration!

Learn more about launching with OpenAI, Claude, and VLLM models such as UI-Tars in our docs.

## Launch Agent(s) Across Multiple Tasks
> launch thousands of app variations to study agent behaviors in parallel

To launch one (or multiple) agents to solve many tasks in parallel, each in an isolated deployment of OpenApps:

```
uv run launch_sweep.py
```

* Note each deployment of OpenApps can have different appearance and content
* Note each task is launched in an isolated environment to ensure reproducible results.

## Testing

Run all tests via:

```python
uv run -m pytest tests/
```


## Attribution

Our apps are built on top of several excellent frameworks:  

- FastHTML [framework](https://github.com/AnswerDotAI/fasthtml) and [examples](https://github.com/AnswerDotAI/fasthtml-example) which allowed us to build fully functional apps in Python, the language most familiar to AI researchers.
- [Browser Gym](https://github.com/ServiceNow/BrowserGym/blob/main/LICENSE) and [AgentLab](https://github.com/ServiceNow/AgentLab/blob/main/LICENSE):
- [Spacy](https://github.com/innoq/spacy/blob/main/LICENSE): for natural language processing
- Open Street Maps: https://www.openstreetmap.org/copyright for our Maps apps.
- (and for the optional webshop) we rely on [WebShop](https://github.com/princeton-nlp/WebShop/blob/master/LICENSE.md) developed by Princeton 

Some icons are have been designed using resources from Flaticon.com

# Development

```
uv sync --extra dev
```

To build docs:

```
mkdocs build
mkdocs serve
``` 

this will launch docs available at https://facebookresearch.github.io/OpenApps/


## Legal

Our work is licensed under CC-BY-NC, please refer to the [LICENSE](LICENSE) file in the top level directory.

Copyright © Meta Platforms, Inc. See the [Terms of Use](https://opensource.fb.com/legal/terms/) and [Privacy Policy](https://opensource.fb.com/legal/privacy/) for this project.

## Cite

```
@article{ullrich2025openapps0,
  title   = {OpenApps: Simulating Environment Variations to Measure UI-Agent Reliability},
  author  = {Karen Ullrich and Jingtong Su and Claudia Shi and Arjun Subramonian and Amir Bar and Ivan Evtimov and Nikolaos Tsilivis and Randall Balestriero and Julia Kempe and Mark Ibrahim},
  year    = {2025},
  journal = {arXiv preprint arXiv: 2511.20766}
}
```
