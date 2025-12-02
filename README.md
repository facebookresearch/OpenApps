
<div align="center">

 #  <img width="45" height="45" alt="image" src="https://github.com/user-attachments/assets/6c409d42-6f3a-4a62-be7f-57793d9dad9d" /> OpenApps
 
*Building Blocks for Digital Agents Research*

[📒 docs](https://facebookresearch.github.io/OpenApps/)  | [📑 ArXiV](https://arxiv.org/abs/2511.20766)
</div>



## Install

1. Clone
```
git clone https://github.com/facebookresearch/OpenApps.git
```

2. Install
```
uv sync
```

see [docs](https://facebookresearch.github.io/OpenApps/) for details.


## Run OpenApps

Simply run:

```bash
uv run launch.py 
```
<img width="1440" height="822" alt="image" src="https://github.com/user-attachments/assets/46024c36-9f6d-462b-acb7-b6c148ed1754" />


Each app can be modified with variables available in `config/apps`. You can override any of these via command line:

```bash
uv run launch.py app.todo.title='Super Todo'
```

Learn more about to customize the content and appearance of apps in the [docs](https://facebookresearch.github.io/OpenApps/).

## Launch an Agent

For agents to directly interact with apps, install: `playwright install chromium`.

Launch an agent to perform a task of *adding a meeting with Dennis to the calendar*:


```
# export OPENAI_API_KEY=""
uv run launch_agent.py agent=GPT-5-1 task_name=add_meeting_with_dennis
```

To see the agent solving the task live, add the headless argument:
```
uv run launch_agent.py ... browsergym_env_args.headless=False
```
![gif (1)](https://github.com/user-attachments/assets/cbf3c02e-0bad-4be7-8b4d-31c64fda49a0)


You can specify the agent of your choice with the `agent=` argument. For example `agent=dummy` is a simple agent that clicks randomly on any buttons, great for exploration!

Learn more about launching with OpenAI, Claude, and VLLM models such as UI-Tars in our [docs](https://facebookresearch.github.io/OpenApps/).

## Contributing

We welcome pull requests with new features or issues via GitHub.


### Development

```
uv sync --extra dev
```

To build docs:

```
mkdocs build
mkdocs serve
``` 

this will launch docs available at https://facebookresearch.github.io/OpenApps/


### Testing

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

## Cite

```
@article{ullrich2025openapps0,
  title   = {OpenApps: Simulating Environment Variations to Measure UI-Agent Reliability},
  author  = {Karen Ullrich and Jingtong Su and Claudia Shi and Arjun Subramonian and Amir Bar and Ivan Evtimov and Nikolaos Tsilivis and Randall Balestriero and Julia Kempe and Mark Ibrahim},
  year    = {2025},
  journal = {arXiv preprint arXiv: 2511.20766}
}
```
