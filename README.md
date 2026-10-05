
<div align="center">

 #  <img width="45" height="45" alt="image" src="https://github.com/user-attachments/assets/6c409d42-6f3a-4a62-be7f-57793d9dad9d" /> OpenApps
 
*Building Blocks for Computer-Use Agents Research*

🏆 ICLR Oral, Top 1%

[📒 docs](https://facebookresearch.github.io/OpenApps/)  | [📑 ArXiV](https://arxiv.org/abs/2511.20766) | [🎬 Video Tutorial](https://www.youtube.com/watch?v=gzNW_LXE7OE)
</div>



Evaluate and train multimodal agents to use apps like humans do (by clicking, typing, and scrolling):

✅ **Unlimited data** (for evaluating and training UI-agents): Configurable state and design to generate thousands of versions of each app

✅ **Lightweight**: runs on a single CPU (and Python-based); no Docker or OS emulators needed

✅ **Ground truth rewards**: task rewards are based on the underlying state and all app logic is transparent in Python


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

For a hot reloading dev server (live changes in browser):
`scripts/dev.sh`

## The online shop

The shop is on by default — no setup step, no download, no flags:

```bash
uv run launch.py                                  # shop at /onlineshop
uv run launch.py apps.onlineshop.enable=False     # turn it off entirely
uv run launch.py apps/onlineshop/content=default  # shop chrome, no products
```

The default `webshop` pack is a 999-product catalog converted from the
[WebShop](https://github.com/princeton-nlp/WebShop) item dump and checked in.
Those records are scraped Amazon listings, so the titles and marketing copy
are the retailer's own, and the pack hotlinks their CDN images rather than
drawing line art.

**This means the default configuration reaches the network.** On an eval node
without egress the images silently fail to load while the page still returns
200, so a screenshot-scored agent gets graded on an observation that quietly
lost its imagery. Pass `apps.onlineshop.product_images=glyphs` there and the
shop draws its own SVG art instead:

```bash
uv run launch.py apps.onlineshop.product_images=glyphs
```

Every other content pack (`german`, `fixture`, `adversarial_descriptions`, …)
stays on `glyphs`.

`scripts/fetch_webshop.py` regenerates the pack if you want a different dump
or more products; nothing requires it to run. See
[Installation](docs/installation.md) for its flags, and for the small
mechanical `fixture` catalog the tests use.

It varies like every other app, with a `layout` axis of its own:

```bash
uv run launch.py apps/onlineshop/layout=grid      # default | grid | compact_table
uv run launch.py apps/onlineshop/content=german
uv run launch.py apps/theme=solarized             # or apps.onlineshop.theme=...
```

**Architecture.** The catalog is seeded from the selected
`config/apps/onlineshop/content/` pack into a SQLite database on launch, so it
is a content variation axis like any other text in OpenApps. Search is SQLite
**FTS5** using its built-in
`bm25()` ranking — FTS5 ships inside Python's `sqlite3`, so relevance ranking
costs no dependency. State lives in four plain tables (`products`,
`cart_items`, `orders`, `order_items`) and is served as JSON at
`/onlineshop_all`, which is what rewards are computed from. Pages are FastHTML
rendered against the shared design tokens. Product images are generated inline
SVG under the default `glyphs` mode, so the app makes no outbound requests;
the `webshop` catalog opts into hotlinking real photos instead.

**The product seed.** A content pack lists `products`, plus an optional `cart`
and `orders` to give a run some starting state. Each product is nine fields —
`sku`, `title`, `price`, `category`, `breadcrumb`, `rating`, `options`,
`bullets`, `description` — and `set_environment` re-seeds all four tables from
them on every launch and every reset. Write your own pack to swap the catalog
wholesale; `scripts/fetch_webshop.py` is just a generator for one.

**The glyphs.** Outside the `webshop` pack, thumbnails are drawn rather than
fetched: a keyword table maps the
product title to one of ~35 hand-written SVG line-art shapes, tinted by a hue
derived from the sku so a product looks the same on every page. Titles that
match nothing fall back to a per-category glyph. To see the whole catalog's
art on one page:

```bash
uv run scripts/render_glyph_sheet.py && open /tmp/glyphs.html
```

**Real photos.** The catalog keeps each product's image URLs, and
`apps.onlineshop.product_images=hotlink` renders them — as a CSS-only carousel
when a product has several, falling back to the glyph when an image fails to
load. The `webshop` pack sets this itself, since it is the one catalog whose
URLs point at real images; the shop-wide default stays `glyphs`. The reason it
is a per-pack opt-in rather than the global default: eval nodes have no
outbound network, so hotlinked images silently do not arrive while the page
still returns 200, and a screenshot-scored agent ends up graded on an
observation that lost its imagery. For eval runs on the real catalog, pass
`apps.onlineshop.product_images=glyphs`.

See [Installation](docs/installation.md#the-online-shop) for both scripts'
flags, the seed schema in full, and how to extend the keyword table.

**Inspecting its data.** Every launch writes a fresh database under that run's
`log_outputs`, so the newest one is the run you were just clicking through:

```bash
DB=$(ls -t log_outputs/*/databases/onlineshop.db | head -1)
sqlite3 -header -column "$DB" "SELECT * FROM cart_items;"
```

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

## OpenApps in action


https://github.com/user-attachments/assets/40482d53-9481-4e48-962b-eb384e94e3c7




## Reproducing the paper

`main` moves. The [`v1.0-paper`](https://github.com/facebookresearch/OpenApps/releases/tag/v1.0-paper) tag pins the paper-era config surface used for the variation grid in [arXiv:2511.20766](https://arxiv.org/abs/2511.20766) — check it out if you are reproducing or comparing against our setup:
```bash
git clone https://github.com/facebookresearch/OpenApps.git
cd OpenApps
git checkout v1.0-paper
uv sync
```

Everything after that tag is free to diverge. The first such change is the appearance refactor: the per-app `appearance` config group is replaced by a shared `apps/theme` (look) plus a per-app `apps/<app>/layout` (structure), so overrides written as `apps/todo/appearance=dark_theme` no longer resolve on `main`. See [App variations](https://facebookresearch.github.io/OpenApps/#app-variations) in the docs for the current axes, which include a table mapping every old `appearance` value onto its replacement.

The tag is the config surface the paper used, not a byte-exact snapshot of the runs: it carries the app, task and harness fixes landed since publication, some of which move rewards (map tasks now match coordinates by ground distance, for example). Expect small differences from the published tables.

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
- Open Street Maps: https://www.openstreetmap.org/copyright for our Maps apps.
- our online shop descends from [WebShop](https://github.com/princeton-nlp/WebShop/blob/master/LICENSE.md), developed by Princeton University. The application has been rewritten and shares none of WebShop's code. Its catalog, `config/apps/onlineshop/content/webshop.yaml`, is a 999-product subset of WebShop's item dump (the whole of `items_shuffle_1000.json`, less one record with an empty title) converted into our own schema and checked in here; the records originate as scraped Amazon listings, and the pack also carries links to Amazon's image CDN. `scripts/fetch_webshop.py` regenerates it from the [source mirror](https://huggingface.co/datasets/YWZBrandon/webshop-data).

Some icons are have been designed using resources from Flaticon.com

Our work is licensed under CC-BY-NC, please refer to the [LICENSE](LICENSE) file in the top level directory.

Copyright © Meta Platforms, Inc. See the [Terms of Use](https://opensource.fb.com/legal/terms/) and [Privacy Policy](https://opensource.fb.com/legal/privacy/) for this project.

## Acknowledgements

* A big thank you to Taj Gillin for implementing an MCP interface for OpenApps and Jiayu Wang for suggestions to improve our harness!
* Another thank you to Yuval Kansal for fixing inconsistencies in task phrasings!

## Featured In

* BrowserGym: https://github.com/servicenow/browsergym
* NE Agents Day (🏆Oral Award) : https://ne-agents-day.github.io/
* OpenEnv (HugginFace and PyTorch RL environment):  https://huggingface.co/docs/openenv/environments/openapp

## Cite

```
@article{ullrich2025openapps0,
  title   = {OpenApps: Simulating Environment Variations to Measure UI-Agent Reliability},
  author  = {Karen Ullrich and Jingtong Su and Claudia Shi and Arjun Subramonian and Amir Bar and Ivan Evtimov and Nikolaos Tsilivis and Randall Balestriero and Julia Kempe and Mark Ibrahim},
  year    = {2025},
  journal = {arXiv preprint arXiv: 2511.20766}
}
```
