

<div align="center">

 # OpenApps
 
*Building Blocks for Digital Agents Research*

</div>

## Install

- Pre-requisite: install uv (a much faster pip): `pip install uv` (or from [source](https://docs.astral.sh/uv/getting-started/installation/))
- [if need be] Install python: `uv python install`

1) Install packages: `uv sync`
2) Activate environment: `source .venv/bin/activate`
3) Install `playwright install chromium`

<details > 
<summary>
 Optionally install for onlineshop (Linux Only) (off by default)
</summary>

`Onlineshop java + spacy configuration`

4) Prepare Java, Webshop data and spacy model: `chmod +x setup.sh` and `./setup.sh`
5) Designate Java path: `source setup_javapath.sh`
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
uv run launch_experiment.py only_run_apps=True mode=aws_a100_cpu_only use_wandb=False apps.onlineshop.enable=True
```
</details>

To run any other commands: `uv run [any_script.py]`.

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

To build docs:

```
mkdocs build
mkdocs serve
``` 

this will launch docs.

## Legal

Our work is licensed under CC-BY-NC, please refer to the [LICENSE](LICENSE) file in the top level directory.

Copyright © Meta Platforms, Inc. See the [Terms of Use](https://opensource.fb.com/legal/terms/) and [Privacy Policy](https://opensource.fb.com/legal/privacy/) for this project.
