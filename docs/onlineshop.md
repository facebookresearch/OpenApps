# Online Shop

The shop is a pure-Python rewrite of Princeton's
[WebShop](https://github.com/princeton-nlp/WebShop). It is on by default and
needs no setup step:

```bash
uv run launch.py                                  # shop at /onlineshop
uv run launch.py apps.onlineshop.enable=False     # turn it off entirely
uv run launch.py apps/onlineshop/content=default  # chrome only: no products, no shop
```

`config/apps/onlineshop/content/webshop.yaml` holds 999 products converted
from the WebShop item dump, and is the shop's default `content` pack. The
catalog is a property of the pack rather than of the app, so a pack with an
empty `products` list yields no `/onlineshop` route and no start-page tile —
the app is absent rather than empty.

The `webshop` pack sets `product_images: hotlink`, because it is the one
catalog whose image URLs point at real photos. **The default configuration
therefore references Amazon's CDN.** On a node without egress the images fail
silently while the page still returns 200, so for eval runs there:

```bash
uv run launch.py apps.onlineshop.product_images=glyphs
```

`tests/test_no_egress.py` records this as the one content-driven entry in its
external-host allowlist; every other entry is a styling dependency.

## Varying the shop

It composes the same way as every other app:

```bash
uv run launch.py apps/onlineshop/layout=grid            # default | grid | compact_table
uv run launch.py apps/onlineshop/content=german         # or long_descriptions, adversarial_descriptions, misleading_descriptions
uv run launch.py apps.onlineshop.theme=solarized        # or apps/theme=solarized globally
uv run launch.py apps.onlineshop.products_per_page=5
```

The text variations (`german`, `long_descriptions`, `adversarial_descriptions`,
`misleading_descriptions`) layer on top of `webshop`, so they change the
chrome and keep the catalog. `default.yaml` is chrome only (title, promo text,
category labels, currency) and deliberately carries `products: []`. Override
`apps.onlineshop.products` from your own content file to swap the catalog
wholesale.

`content/fixture.yaml` is a small mechanical catalog (18 generic products, no
prose) that exists so the test suite has something fixed to run against.

## The product seed

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
    images: []                    # optional; only drawn under product_images=hotlink

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

## Rebuilding the catalog

`scripts/fetch_webshop.py` generated the committed pack and can regenerate it
— a different dump, more products, synthesized ratings. Nothing requires it to
run, and it overwrites `webshop.yaml` in place, discarding hand-edits. It
pulls from
[a HuggingFace mirror](https://huggingface.co/datasets/YWZBrandon/webshop-data)
of the item dump.

```bash
uv run scripts/fetch_webshop.py --inspect          # print the real schema; writes nothing
uv run scripts/fetch_webshop.py                    # rebuild the committed pack as it stands
uv run scripts/fetch_webshop.py --file items_shuffle.json --limit 5000   # bigger (~1.5 GB download)
uv run scripts/fetch_webshop.py --synth-ratings    # stable, invented ratings when the dump has none
uv run scripts/fetch_webshop.py --out /tmp/webshop.yaml                  # e.g. to diff two conversions
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--inspect` | off | Print the real schema and exit without writing |
| `--file` | first of `items_shuffle_1000.json`, `items_human_ins.json`, `items_shuffle.json` | Which dump in the HF repo to convert |
| `--limit` | `1000` | Products to keep (the committed pack's size; also all of `items_shuffle_1000.json`) |
| `--synth-ratings` | off | Derive stable ratings from the sku when the data has none |
| `--out` | `config/apps/onlineshop/content/webshop.yaml` | Destination |

It downloads through `huggingface_hub` when that is importable, so re-runs hit
the shared HF cache.

**On prices.** About one record in eleven has an empty `pricing` field, and a
few ranges open at a $0.01 placeholder. A product nobody can be charged for is
useless to a shopping task, so a missing price is filled with the median price
of the products in the same category, and sub-dollar values are ignored when
reading a range.

**On field names.** The dump has been re-exported several times and the casing
is not stable — the current one uses `name`, `full_description`,
`small_description` and `pricing`. Each output field is read from a list of
candidate keys (`_FIELDS` in the script), so if a conversion comes out empty,
run `--inspect` and add the real key to that list.

**On options.** WebShop's `customization_options` is
`{"Color": [{"value": "Bath Ball", "url": ..., "image": ..., "price": ...}]}`
— the sibling keys hold `amazon.com` links. Only `value` is carried across,
and the script aborts if any URL survives into a text field.

## Product images

`apps.onlineshop.product_images` chooses what gets drawn:

| Mode | What renders |
| --- | --- |
| `glyphs` (app default) | Inline SVG line art chosen by title keyword — no files, no outbound requests |
| `hotlink` (set by the `webshop` pack) | The catalog's own image URLs, as a CSS-only carousel when there are several |

Under `hotlink`, each `<img>` carries an `onerror` that swaps the whole
carousel for the glyph, so a failed fetch degrades to line art rather than a
broken-image icon. The carousel is one hidden radio per slide and a clickable
dot per image — real elements an agent can click, no JavaScript.
`layout=compact_table` stays imageless either way. An unrecognised value falls
back to `glyphs`, so a typo in a sweep override cannot silently start
hotlinking.

**Glyphs.** `_GLYPHS` in `src/open_apps/apps/onlineshop_app/main.py` holds ~35
hand-written shapes on a 100×100 box; `_GLYPH_KEYWORDS` maps title keywords
onto them, first match wins, on word boundaries with an optional plural (`pen`
hits "Pens" but not "Open"); `_CATEGORY_GLYPHS` catches the rest. The hue is
derived from the sku, so a product looks identical on every page. To review a
catalog's art at once:

```bash
uv run scripts/render_glyph_sheet.py            # the webshop pack; or pass `fixture`
open /tmp/glyphs.html
```

## Inspecting the shop's data

Each launch writes a fresh SQLite database under the run's `log_outputs`
directory, so the newest one is the run you were just clicking through:

```bash
DB=$(ls -t log_outputs/*/databases/onlineshop.db | head -1)
sqlite3 -header -column "$DB" "SELECT * FROM cart_items;"
```

Four plain tables — `products`, `cart_items`, `orders`, `order_items` — plus
the `products_fts*` search index. The same state is served as JSON at
`/onlineshop_all`, which is what rewards are computed from.

## How it works

| Concern | Implementation |
| --- | --- |
| Catalog | Seeded from the Hydra `content` pack into the `products` table on launch |
| Search | SQLite **FTS5** with its built-in `bm25()`, weighted title > options > bullets > description |
| Persistence | One SQLite file, four relational tables, via `fastlite` |
| Rendering | FastHTML against the shared design tokens (`apps/theme=`) with a `layout` group |
| Reward surface | `GET /onlineshop_all` → `{"cart": [...], "orders": [...]}` |

FTS5 is compiled into Python's `sqlite3` module, so BM25 ranking costs no
dependency. User input is accent-folded and tokenised, and each token is
quoted before being OR-ed into a `MATCH` expression, so FTS5 operators typed
into the search box (`*`, `NEAR`, a stray quote) are matched literally, and
`creme` and `crème` find the same products. Tokens are OR-ed rather than
AND-ed so a query returns its best partial matches.
