- Pre-requisite: install uv (a much faster pip): `pip install uv` (or from [source](https://docs.astral.sh/uv/getting-started/installation/))
<!-- - [If using Conda] Create a fresh venv: `uv venv --python "$(which python)"` -->

0) Clone [repo](https://github.com/facebookresearch/OpenApps)

1) Install packages: `uv sync`

2) Activate environment: `source .venv/bin/activate`

3) Install `playwright install chromium`

That is the whole installation. **Every app runs from `uv sync` with no
further setup** — no JDK and no model weights. The one exception is the online
shop, which needs a catalog before it appears; see
[The online shop](#the-online-shop) below. Launch with:

```
uv run launch.py
```

/// details | Optionally install Java 21 for map route planning

The map app shells out to an [OpenTripPlanner](https://www.opentripplanner.org/)
server, which is Java. Only `apps.maps.allow_planning` needs it — every other
app, and the map app's browsing and saved places, work without it.

4) Install OpenJDK 21: `chmod +x setup.sh` and `./setup.sh` for **Linux X64** or **Mac ARM64** systems

5) Designate Java path: `source setup_javapath.sh` for **Linux X64** or **Mac ARM64** systems

6) Check `java -version` gives you `java version "21.0.1"`

**Remember to run `source setup_javapath.sh` in future shells before launching map-planning tasks.**
///

## The online shop

**The shop ships with no catalog, so it does not appear until you build one.**
Until then there is no `/onlineshop` route and no tile on the start page — the
app is absent rather than empty. One optional script fixes that:

```bash
uv run scripts/fetch_webshop.py                          # build the catalog
uv run launch.py apps/onlineshop/content=webshop         # shop at /onlineshop
```

The script downloads the [WebShop](https://github.com/princeton-nlp/WebShop)
item dump from
[a HuggingFace mirror](https://huggingface.co/datasets/YWZBrandon/webshop-data)
and converts it into a `webshop` content pack.

```bash
# Look before you write: prints the dataset's file list, the keys actually
# present in the records, how they map onto the shop's fields, and the
# category distribution. Writes nothing.
uv run scripts/fetch_webshop.py --inspect

# Default run: 200 products from items_shuffle_1000.json.
uv run scripts/fetch_webshop.py

# A bigger catalog, from the full 1.18M-product dump (~1.5 GB download).
uv run scripts/fetch_webshop.py --file items_shuffle.json --limit 2000

# Populate star ratings when the chosen dump has none. Off by default: the
# values are derived from the sku, so they are stable but invented.
uv run scripts/fetch_webshop.py --synth-ratings

# Write somewhere else, e.g. to diff two conversions.
uv run scripts/fetch_webshop.py --out /tmp/webshop.yaml
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--inspect` | off | Print the real schema and exit without writing |
| `--file` | first of `items_shuffle_1000.json`, `items_human_ins.json`, `items_shuffle.json` | Which dump in the HF repo to convert |
| `--limit` | `200` | Products to keep |
| `--synth-ratings` | off | Derive stable ratings from the sku when the data has none |
| `--out` | `config/apps/onlineshop/content/webshop.yaml` | Destination |

It downloads through `huggingface_hub` when that is importable, so re-runs hit
the shared HF cache and cost nothing; otherwise it falls back to a plain HTTPS
GET and needs no extra dependency.

**On field names.** The dump has been re-exported several times and the casing
is not stable — the current one uses `name`, `full_description`,
`small_description` and `pricing` rather than the `Title`/`Description`/
`BulletPoints`/`Price` an older guide might suggest. Each output field is read
from a list of candidate keys (`_FIELDS` in the script), so if a conversion
comes out empty, run `--inspect` and add the real key to that list. That is a
one-line fix rather than a rewrite.

**On options.** WebShop's `customization_options` is
`{"Color": [{"value": "Bath Ball", "url": ..., "image": ..., "price": ...}]}`
— the sibling keys hold `amazon.com` and `media-amazon.com` links. Only
`value` is carried across, and the script aborts if any URL survives into the
output, because `tests/test_no_egress.py` would fail the rendered page.

**Why it is a download and not a checked-in file.** Those records are scraped
Amazon listings — real brand names and real marketing copy. Redistributing
them from this repository is a licensing question we do not need to answer, so
the generated pack is gitignored and stays on the machine that built it.
Product images are dropped on import for the same reason the shop draws its
own line art: the eval nodes have no outbound network, and
`tests/test_no_egress.py` fails any page that references a CDN.

`config/apps/onlineshop/content/fixture.yaml` is a small mechanical catalog
(18 generic products, no prose) that exists only so the test suite has
something fixed to run against. It is not meant as a storefront, but
`apps/onlineshop/content=fixture` will give you a working shop offline.

/// details | It used to need OpenJDK 21 too

Earlier versions were a port of Princeton's WebShop that fetched the same
dataset from Google Drive with `gdown` and searched it through a Lucene index
built by `pyserini` — a JNI binding, hence the JDK — plus a spaCy model for a
reward function.

Search is now SQLite FTS5, so the JDK and `setup_pyserini.sh` are gone, and
the Google Drive links the old `setup.sh` used are dead. `scripts/fetch_webshop.py`
replaces that download with the HuggingFace mirror.
///

### Varying the shop

It composes the same way as every other app:

```bash
uv run launch.py apps/onlineshop/layout=grid            # default | grid | compact_table
uv run launch.py apps/onlineshop/content=german         # or long_descriptions, adversarial_...
uv run launch.py apps.onlineshop.theme=solarized        # or apps/theme=solarized globally
uv run launch.py apps.onlineshop.products_per_page=5
```

One caveat on `content`: it is a single Hydra group, so the pack that supplies
the catalog is the same pack that supplies the chrome. `content=german`
inherits from `default` and therefore has no products. To vary the chrome on
top of a real catalog, add `defaults: - webshop` to a copy of the pack you
want rather than selecting two.

The catalog lives in whichever content pack is selected —
`webshop.yaml` after running the setup script, or `fixture.yaml` for the
mechanical test catalog. `default.yaml` is chrome only (title, promo text,
category labels, currency) and deliberately carries `products: []`. Override
`apps.onlineshop.products` from your own content file to swap the catalog
wholesale.

### The product seed

A content pack is plain Hydra YAML. `set_environment` clears and re-seeds all
four tables from it on every launch and every reset, so the catalog is a
variation axis like any other text in OpenApps — not a fixture you migrate.

```yaml
# @package apps.onlineshop
defaults:
  - default          # inherit title, promo text, category labels, currency

products:
  - sku: 'furn-desk-205'          # stable id; used in URLs and in cart/order state
    title: 'Standing Desk'        # display name, and the top-weighted search field
    price: 649.00                 # float, in `currency_symbol` units
    category: furniture           # must be a key of `categories` in the pack
    breadcrumb: ['Furniture', 'Desks']   # crumbs on the item page
    rating: 4.2                   # 0-5, rendered as stars
    options:                      # option name -> selectable values; may be {}
      finish: ['walnut', 'natural']
      width: ['120 cm', '140 cm']
    bullets:                      # short feature list; may be []
      - 'Electric height adjustment'
    description: 'Sit-stand desk with a programmable controller.'

# Optional starting state. Both are seeded after `products`, and a line
# referencing an unknown sku is dropped with a warning rather than crashing
# the launch.
cart:
  - sku: 'furn-desk-205'
    options: {finish: 'walnut'}   # values not offered by the product are dropped
    quantity: 1

orders:
  - order_id: '0efcf51f'          # omit to get a random one
    name: 'Alex Morgan'
    address: '14 Bridge Street'
    date: '2026-08-24 19:54:34'
    status: 'Delivered'
    items:
      - sku: 'furn-desk-205'
        options: {finish: 'walnut'}
        quantity: 1
    # total: 649.00               # omit to compute from current prices
```

Option values are indexed for search alongside the title, bullets and
description, so a query for `walnut` finds the desk above even though the word
appears nowhere in its text. Cart lines are keyed on sku *plus* a canonical
form of the chosen options, so the same product in two finishes is two lines,
and the same options in a different order is one.

### Product glyphs

Thumbnails are generated inline SVG, never fetched. `_GLYPHS` in
`src/open_apps/apps/onlineshop_app/main.py` holds ~35 hand-written line-art
shapes as SVG path data on a 100×100 box; `_GLYPH_KEYWORDS` maps title
keywords onto them, first match wins; `_CATEGORY_GLYPHS` catches anything that
matches nothing. Stroke and fill come from a hue derived from the sku, so a
product looks identical on every page it appears on.

To review the whole catalog's art at once, rather than paging through the app:

```bash
uv run scripts/render_glyph_sheet.py                        # the webshop pack
uv run scripts/render_glyph_sheet.py fixture                # the test catalog
uv run scripts/render_glyph_sheet.py --out ~/glyphs.html    # somewhere durable
uv run scripts/render_glyph_sheet.py --size 140             # bigger glyphs
open /tmp/glyphs.html
```

It prints how many titles matched a keyword versus fell back to their
category, and tints the fallbacks on the page so they are easy to pick out:

```
--> Wrote /tmp/glyphs.html (177 kB, 200 products)
    113 matched a glyph keyword, 87 fell back to their category default (tinted)
```

#### Hotlinked product photos

The generated pack keeps each product's image URLs in an `images` list, and
`apps.onlineshop.product_images` chooses what gets drawn:

```bash
uv run launch.py apps/onlineshop/content=webshop                          # glyphs (default)
uv run launch.py apps/onlineshop/content=webshop \
                 apps.onlineshop.product_images=hotlink                   # real photos
```

Under `hotlink`, a product with several images becomes a carousel: one hidden
radio per slide and a clickable dot per image, pure CSS, no JavaScript and no
new dependency — so the controls are real elements an agent can click. A
product with one image renders it without controls, and a product with none
falls back to its glyph. The glyph also ships inside every carousel: each
`<img>` has an `onerror` that reveals it, so a failed fetch degrades to line
art rather than to a broken-image icon. One failure drops the whole carousel
to the glyph, deliberately — the common case is "no network", where they all
fail, and a half-populated carousel is a worse observation than a consistent
one. `layout=compact_table` stays imageless either way.

!!! warning "Why this is off by default"

    - **The eval nodes have no outbound network.** The images do not arrive
      there, the page still returns 200, and a screenshot-scored agent is
      graded on an observation that quietly lost its imagery. This is the
      exact failure `tests/test_no_egress.py` was written to catch.
    - **`TestNoEgress` in `tests/test_onlineshop.py` fails under `hotlink`**,
      by design. It asserts the pages reference no external host.
    - **The URLs point at Amazon's CDN**, so hotlinking them is subject to
      whatever terms attach to that.

    Use it for local browsing, or for runs where you have decided the network
    is genuinely available. An unrecognised value falls back to `glyphs`, so a
    typo in a sweep override cannot silently start hotlinking.

Keywords match on **word boundaries with an optional plural**, so `pen` hits
"Fountain Pen" and "Pens" but not "Open" or "Pendant" — worth knowing before
you add a short keyword, because real retailer titles are long and a bare
substring match produces confidently wrong art. To raise coverage on a
catalog of your own, add entries to `_GLYPH_KEYWORDS` pointing at existing
glyph names, then re-run the sheet to check the delta. Order matters: put the
specific ahead of the general, which is why `desk organizer` and `lamp` both
sit above `desk`.

### Inspecting the shop's data

Each launch writes a fresh SQLite database under the run's `log_outputs`
directory, so the newest one is the run you were just clicking through:

```bash
DB=$(ls -t log_outputs/*/databases/onlineshop.db | head -1)
sqlite3 "$DB" ".tables"
sqlite3 -header -column "$DB" "SELECT * FROM cart_items;"
```

Four plain tables — `products`, `cart_items`, `orders`, `order_items` — plus
the `products_fts*` search index. Nothing needs quoting, and joins read the
way you would expect:

```sql
SELECT p.title, c.options, c.quantity, printf('%.2f', p.price * c.quantity) AS line_total
FROM cart_items c JOIN products p USING(sku);
```

The same state is served as JSON at `/onlineshop_all`, which is what rewards
are computed from.

### How it works

| Concern | Implementation |
| --- | --- |
| Catalog | Seeded from the Hydra `content` pack into the `products` table on launch; built by `scripts/fetch_webshop.py` |
| Search | SQLite **FTS5** with its built-in `bm25()`, weighted title > options > bullets > description |
| Persistence | One SQLite file, four relational tables, via `fastlite` |
| Rendering | FastHTML against the shared design tokens (`apps/theme=`) with a `layout` group |
| Imagery | Inline SVG line art chosen by title keyword, hue keyed on the sku — no files, no outbound requests |
| Reward surface | `GET /onlineshop_all` → `{"cart": [...], "orders": [...]}` |

Search is the part worth knowing about. FTS5 is compiled into Python's
`sqlite3` module, so BM25 ranking costs no dependency at all — that is what
replaced Lucene. User input is tokenised and each token quoted before being
OR-ed into a `MATCH` expression, so FTS5 operators typed into the search box
(`*`, `NEAR`, a stray quote) are matched literally instead of being executed.
Tokens are OR-ed rather than AND-ed so a query returns its best partial
matches, which is how the Lucene-backed original behaved.
