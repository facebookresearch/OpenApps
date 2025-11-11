title: Start with OpenApps

> Building Blocks for Digital Agents Research

New to agents? See our [intro to ui agents guide](Intro to UI Agents.md).

### Install

```shell
uv pip install git+https://github.com/facebookresearch/openapps.git
```

For other installation options and online shop setup see [Installation](installation.md).

### Run OpenApps

```bash
uv run launch.py 
```
![landing](landing.png)



### App variations
Each app can be modified with variables available in `config/apps`. You can override any of these via command line:

```bash
uv run launch.py app.todo.title='Super Todo'
```

OpenApps also comes with pre-defined variations that can affect the content and appearance of apps.

#### Appearance


/// tab | default

    ::bash
    export APPEARANCE=default

![landing](landing.png)

///
/// tab | dark_theme

    ::bash
    export APPEARANCE=dark_theme

![landing](landing.png)
///
/// tab | challenging_font

    ::bash
    export APPEARANCE=challenging_font


![landing](landing.png)
///

```shell
# launch apps with selected appearance
uv run launch.py apps/start_page/appearance=$APPEARANCE \
apps/calendar/appearance=$APPEARANCE \
apps/maps/appearance=$APPEARANCE \
apps/messenger/appearance=$APPEARANCE
```

#### Content

/// tab | default

    ::bash
    export CONTENT=default

![landing](landing.png)

///
/// tab | long_descriptions

    ::bash
    export CONTENT=long_descriptions

![landing](landing.png)
///
/// tab | german

    ::bash
    export CONTENT=german


![landing](landing.png)
///
/// tab | misleading_descriptions

    ::bash
    export CONTENT=misleading_descriptions


![landing](landing.png)
///

```shell
export CONTENT="adversarial_descriptions" 
uv run launch.py apps/calendar/content=$CONTENT apps/maps/content=$CONTENT apps/start_page/content=$CONTENT apps/messenger/content=$CONTENT apps/todo/content=$CONTENT apps/pop_ups=$CONTENT
```

To launch popups, set `apps/pop_ups=adversarial_descriptions`.

You can see the specific variables for each defined in the individual apps. For example, `config/apps/maps/appearance/dark_theme.yaml`.

## Launch Agent

Launch an agent to perform a task:

```
uv run launch_agent.py
```
You can specify the agent of your choice with the `agent=` argument. For example `agent=dummy` is a simple agent that clicks randomly on any buttons, great for exploration!

Learn more about launching with OpenAI, Claude, VLLM models, or specialized models such as UI-Tars in [agents](agents.md).

To see the agent solving the task live:
```
uv run launch_agent.py browsergym_env_args.headless=False
```


## Launch Agent(s) Across Multiple Tasks
> launch thousands of app variations to study agent behaviors in parallel

coming soon!

<!-- To launch one (or multiple) agents to solve many tasks in parallel, each in an isolated deployment of OpenApps:

```
uv run launch_sweep.py
```

* Note each deployment of OpenApps can have different appearance and content
* Note each task is launched in an isolated environment to ensure reproducible results. -->

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


Our work is licensed under CC-BY-NC, please refer to the [LICENSE](LICENSE) file in the top level directory.
Copyright © Meta Platforms, Inc. See the [Terms of Use](https://opensource.fb.com/legal/terms/) and [Privacy Policy](https://opensource.fb.com/legal/privacy/) for this project.
