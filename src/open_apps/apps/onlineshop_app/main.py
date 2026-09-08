"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
"""The OpenApps online shop.

A rewrite of the original port of Princeton's WebShop (Yao et al., 2022). The
previous implementation needed OpenJDK 21 and a ~1000-product dataset pulled
from Google Drive by ``setup.sh``: products were searched through a Lucene
index built by ``pyserini``, which is a JNI binding and hence the JDK. None of
that is reachable on an offline eval node, so the app shipped disabled.

What replaced it:

* **Catalog** -- seeded from ``config/apps/onlineshop/content/*.yaml`` like
  every other app, so ``content`` becomes a real variation axis (german,
  long_descriptions, adversarial_descriptions, ...) instead of a fixed scrape.
* **Search** -- SQLite FTS5 with its built-in ``bm25()`` ranking. Same family
  of ranking function Lucene provided, no JDK, no extra dependency: FTS5 is
  compiled into the ``sqlite3`` module in every supported interpreter here.
* **Persistence** -- one SQLite file of ordinary relational tables. The old
  code kept the cart in memory and mirrored it to ``cart.json`` /
  ``orders.json``, keying order lines by a *stringified Python tuple*
  (``"('B07YPYJ32Z', '{}')"``) that had to be round-tripped through
  ``ast.literal_eval``. Nothing consumed that shape, so the rewrite uses plain
  tables other apps can read directly.
* **Rendering** -- FastHTML against the shared design tokens, matching todo,
  so ``apps/theme=`` and ``apps/onlineshop/layout=`` both apply here.

The scoring surface is ``/onlineshop_all``, which returns the cart and the
order history as plain JSON for ``open_apps.state.get_current_state``.
"""
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from fasthtml.common import *
# Svg/Rect/Text are not re-exported by fasthtml.common.
from fasthtml.svg import Circle, G, Path, Rect, Svg

from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.theme import theme_style


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
#
# `breadcrumb`, `bullets` and `options` are JSON-encoded because SQLite has no
# array type and a normalised `product_options` table would buy nothing: the
# options of a product are only ever read as a whole, never queried across
# products. `CartItem.options` and `OrderItem.options` store the *chosen*
# values, canonicalised by `_options_key` so that "the same product with the
# same options" is one row regardless of key order.


@dataclass
class Product:
    sku: str
    title: str
    price: float
    category: str
    rating: float
    description: str
    breadcrumb: str
    bullets: str
    options: str
    # JSON list of image URLs from the source catalog. Only rendered when
    # `apps.onlineshop.product_images=hotlink`; stored regardless so the mode
    # can be flipped without re-importing.
    images: str


@dataclass
class CartItem:
    id: int
    sku: str
    options: str
    quantity: int
    selected: bool


@dataclass
class Order:
    order_id: str
    name: str
    address: str
    date: str
    status: str
    total: float


@dataclass
class OrderItem:
    id: int
    order_id: str
    sku: str
    options: str
    quantity: int
    unit_price: float


app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())
logo_title_container = None

db = None
products = None
cart_items = None
orders = None
order_items = None


# Static, theme-agnostic component styles. All colors/fonts are design tokens
# resolved per-request via `theme_style()`, so this block never needs
# rebuilding when the theme or app config changes.
styles = Style("""
    body {
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        color: var(--color-fg);
        background-color: var(--color-bg);
    }
    a { color: var(--color-primary); text-decoration: none; }
    a:hover { text-decoration: underline; }
    h1, h2, h3, h4 { font-family: var(--font-heading); color: var(--color-fg); }

    .shop-bar {
        display: flex;
        gap: var(--space);
        align-items: center;
        flex-wrap: wrap;
        margin-bottom: 1rem;
    }
    .shop-bar form { display: flex; gap: var(--space); flex: 1 1 320px; margin: 0; }
    .shop-bar input[type="search"], .shop-bar input[type="text"] { margin: 0; }
    .shop-promo {
        background-color: var(--color-surface);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 0.6rem 0.9rem;
        margin-bottom: 1rem;
    }
    .shop-categories { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 1rem; }
    .shop-category {
        background-color: var(--color-surface);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 0.25rem 0.7rem;
        color: var(--color-fg);
    }
    .shop-category.active {
        background-color: var(--color-primary);
        color: var(--color-on-primary);
        border-color: var(--color-primary);
    }

    .card {
        background-color: var(--color-surface);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 0.9rem;
        margin-bottom: 0.9rem;
    }
    .card-title { margin: 0 0 0.3rem 0; font-size: 1.05rem; }
    .card-price { color: var(--color-primary); font-weight: 700; font-size: 1.1rem; }
    .card-rating { color: var(--color-muted); margin-left: 0.5rem; }
    .card-desc { color: var(--color-muted); margin: 0.4rem 0; }

    /* default layout: image left, details right */
    .product-row { display: flex; gap: 0.9rem; align-items: flex-start; }
    .product-row .product-thumb { flex: 0 0 120px; }
    .product-row .product-body { flex: 1 1 auto; }

    /* grid layout */
    .product-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
        gap: 0.9rem;
    }
    .product-grid .card { display: flex; flex-direction: column; margin-bottom: 0; }
    .product-grid .product-thumb { align-self: center; }

    /* compact_table layout */
    .product-table { width: 100%; border-collapse: collapse; }
    .product-table th, .product-table td {
        text-align: left;
        padding: 0.45rem 0.6rem;
        border-bottom: 1px solid var(--color-border);
    }
    .product-table thead th { color: var(--color-muted); font-weight: 600; }

    .btn {
        display: inline-block;
        border-radius: var(--radius);
        padding: 0.35rem 0.8rem;
        border: 1px solid transparent;
        cursor: pointer;
        font-size: 0.95rem;
        width: auto;
    }
    .btn-primary { background-color: var(--color-primary); color: var(--color-on-primary); }
    .btn-accent { background-color: var(--color-accent); color: var(--color-btn-fg); border-color: var(--color-accent); }
    .btn-danger { background-color: var(--color-danger); color: var(--color-btn-fg); border-color: var(--color-danger); }
    .btn-neutral { background-color: var(--color-neutral); color: var(--color-btn-fg); border-color: var(--color-neutral); }

    .breadcrumb { color: var(--color-muted); margin-bottom: 0.8rem; font-size: 0.9rem; }
    .option-group { margin-bottom: 0.7rem; }
    .option-group label { font-weight: 600; display: block; margin-bottom: 0.25rem; }
    .option-values { display: flex; gap: 0.4rem; flex-wrap: wrap; }

    .option-chips { display: flex; gap: 0.3rem; flex-wrap: wrap; margin: 0.25rem 0 0.4rem 0; }
    .option-chip {
        background-color: var(--color-bg);
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 0.05rem 0.45rem;
        font-size: 0.85rem;
        color: var(--color-fg);
    }
    /* Hotlinked product imagery (apps.onlineshop.product_images=hotlink).
       A CSS-only carousel: one radio per slide, one label per dot, so the
       controls are real clickable elements without any JavaScript. The glyph
       stays in the markup as the fallback and is revealed by `images-failed`,
       which each <img>'s onerror sets when a fetch fails. */
    .product-media {
        position: relative;
        width: var(--thumb-size, 120px);
        height: var(--thumb-size, 120px);
    }
    .product-media .carousel-state { display: none; }
    .product-media .carousel-slide {
        display: none;
        width: 100%;
        height: 100%;
        object-fit: contain;
        background-color: var(--color-surface);
        border-radius: var(--radius);
    }
    /* Each radio reveals the image immediately following it. */
    .product-media .carousel-state:checked + .carousel-slide { display: block; }
    /* The glyph sits underneath and only shows if every image failed. */
    .product-media > svg { display: none; }
    .product-media.images-failed .carousel-slide { display: none !important; }
    .product-media.images-failed > svg { display: block; }
    .product-media.images-failed .carousel-dots { display: none; }
    .carousel-dots {
        position: absolute;
        bottom: 4px;
        left: 0;
        right: 0;
        display: flex;
        justify-content: center;
        gap: 5px;
    }
    .carousel-dot {
        width: 9px;
        height: 9px;
        border-radius: 50%;
        background-color: var(--color-border);
        border: 1px solid var(--color-muted);
        cursor: pointer;
        margin: 0;
    }
    .carousel-dot:hover { background-color: var(--color-primary); }
    /* Fill the dot matching the selected slide. One rule per position rather
       than `:has()`, and capped at MAX_IMAGES in scripts/fetch_webshop.py. */
    .carousel-state:nth-of-type(1):checked ~ .carousel-dots .carousel-dot:nth-child(1),
    .carousel-state:nth-of-type(2):checked ~ .carousel-dots .carousel-dot:nth-child(2),
    .carousel-state:nth-of-type(3):checked ~ .carousel-dots .carousel-dot:nth-child(3),
    .carousel-state:nth-of-type(4):checked ~ .carousel-dots .carousel-dot:nth-child(4) {
        background-color: var(--color-primary);
        border-color: var(--color-primary);
    }

    .cart-line { display: flex; gap: 0.8rem; align-items: center; }
    .cart-line .product-thumb { flex: 0 0 72px; }
    .cart-line-body { flex: 1 1 auto; }
    .cart-total { font-size: 1.2rem; font-weight: 700; color: var(--color-fg); }
    .muted { color: var(--color-muted); }
    .pagination { display: flex; gap: 0.6rem; align-items: center; margin-top: 1rem; }
    .order-head { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; }
    .status-pill {
        background-color: var(--color-primary);
        color: var(--color-on-primary);
        border-radius: var(--radius);
        padding: 0.1rem 0.55rem;
        font-size: 0.85rem;
    }
""")


# --------------------------------------------------------------------------
# Config helpers
# --------------------------------------------------------------------------

def _cfg():
    return app.config.onlineshop


def current_layout():
    """Selected layout, or ``default`` before ``set_environment`` has run."""
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.onlineshop, "layout", "default")


def shop_theme():
    """The active theme's ``:root`` token block, resolved per-request so live
    ``reconfigure`` theme swaps take effect."""
    return theme_style(app.config, "onlineshop")


def _currency():
    return getattr(_cfg(), "currency_symbol", "$")


def money(amount: float) -> str:
    return f"{_currency()}{amount:,.2f}"


def _per_page() -> int:
    return int(getattr(_cfg(), "products_per_page", 10))


def image_mode() -> str:
    """``glyphs`` (default) or ``hotlink``.

    Read per-request rather than cached so a live ``reconfigure`` can flip it,
    and tolerant of being called before ``set_environment`` has run.
    """
    config = getattr(app, "config", None)
    if config is None:
        return "glyphs"
    mode = str(getattr(config.onlineshop, "product_images", "glyphs")).lower()
    return mode if mode in ("glyphs", "hotlink") else "glyphs"


def _categories() -> dict:
    """Category slug -> display label, as an ordinary dict."""
    raw = getattr(_cfg(), "categories", {}) or {}
    return {str(k): str(v) for k, v in raw.items()}


def _plain(value):
    """OmegaConf containers -> plain Python, leaving primitives untouched."""
    from omegaconf import DictConfig, ListConfig, OmegaConf

    if isinstance(value, (DictConfig, ListConfig)):
        return OmegaConf.to_container(value, resolve=True)
    return value


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------

def _row(table, pk):
    """Look up a row by primary key, or ``None`` if it does not exist.

    ``fastlite``'s ``Table.get`` raises ``NotFoundError`` unless it is given an
    explicit default, so every miss -- an unknown sku in a URL, a cart line
    deleted in another tab -- would otherwise surface as a 500.
    """
    return table.get(pk, default=None)


def _options_key(options: dict) -> str:
    """Canonical JSON for a chosen-options mapping.

    Sorted keys so that ``{"size": "m", "color": "navy"}`` and
    ``{"color": "navy", "size": "m"}`` collapse to the same cart line.
    """
    return json.dumps({str(k): str(v) for k, v in sorted((options or {}).items())})


def _build_fts():
    """(Re)build the FTS5 index over the catalog.

    Standalone rather than an external-content table: the catalog is a few
    dozen rows re-seeded on every launch, so the duplicated text costs
    nothing and avoids having to keep triggers in sync.
    """
    db.execute("DROP TABLE IF EXISTS products_fts")
    db.execute(
        "CREATE VIRTUAL TABLE products_fts USING fts5("
        "sku UNINDEXED, title, bullets, description, options)"
    )
    for product in products():
        # Option values are indexed as well, matching the document the original
        # built for Lucene (title + description + first bullet + option text),
        # so a search for "walnut" or "espresso" finds products offering it.
        option_text = " ".join(
            f"{name} {' '.join(values)}"
            for name, values in json.loads(product.options).items()
        )
        db.execute(
            "INSERT INTO products_fts (sku, title, bullets, description, options) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                product.sku,
                product.title,
                " ".join(json.loads(product.bullets)),
                product.description,
                option_text,
            ),
        )


def _seed_products(config):
    for raw in _plain(getattr(config.onlineshop, "products", [])) or []:
        products.insert(
            Product(
                sku=str(raw["sku"]),
                title=str(raw.get("title", "")),
                price=float(raw.get("price", 0.0)),
                category=str(raw.get("category", "")),
                rating=float(raw.get("rating", 0.0)),
                description=str(raw.get("description", "")).strip(),
                breadcrumb=json.dumps([str(c) for c in raw.get("breadcrumb", [])]),
                bullets=json.dumps([str(b) for b in raw.get("bullets", [])]),
                options=json.dumps(
                    {
                        str(name): [str(v) for v in values]
                        for name, values in (raw.get("options", {}) or {}).items()
                    }
                ),
                images=json.dumps([str(u) for u in (raw.get("images", []) or [])]),
            )
        )


def _validated_options(sku: str, chosen: dict) -> dict:
    """Drop configured options the product does not actually offer.

    A typo in the seed config should not take the whole launch down, but it
    also should not silently create a cart line the UI can never reproduce, so
    the bad option is dropped with a warning.
    """
    product = _row(products, sku)
    available = json.loads(product.options)
    kept = {}
    for name, value in (chosen or {}).items():
        name, value = str(name), str(value)
        if name in available and value in available[name]:
            kept[name] = value
        else:
            print(f"Warning: onlineshop seed: {sku} has no option {name}={value!r}, dropping")
    return kept


def _seed_cart(config):
    for raw in _plain(getattr(config.onlineshop, "cart", [])) or []:
        sku = str(raw["sku"])
        if _row(products, sku) is None:
            print(f"Warning: onlineshop seed: cart references unknown sku {sku!r}, skipping")
            continue
        cart_items.insert(
            CartItem(
                id=None,
                sku=sku,
                options=_options_key(_validated_options(sku, raw.get("options", {}))),
                quantity=int(raw.get("quantity", 1)),
                selected=bool(raw.get("selected", True)),
            )
        )


def _seed_orders(config):
    for raw in _plain(getattr(config.onlineshop, "orders", [])) or []:
        lines = []
        total = 0.0
        for line in raw.get("items", []) or []:
            sku = str(line["sku"])
            product = _row(products, sku)
            if product is None:
                print(f"Warning: onlineshop seed: order references unknown sku {sku!r}, skipping")
                continue
            quantity = int(line.get("quantity", 1))
            lines.append((sku, _options_key(_validated_options(sku, line.get("options", {}))),
                          quantity, product.price))
            total += product.price * quantity

        order_id = str(raw.get("order_id") or uuid.uuid4().hex[:8])
        orders.insert(
            Order(
                order_id=order_id,
                name=str(raw.get("name", "")),
                address=str(raw.get("address", "")),
                date=str(raw.get("date", "")),
                status=str(raw.get("status", "Processing")),
                # Prefer the configured total when given, so a fixture can pin
                # a historical price that no longer matches the catalog.
                total=float(raw["total"]) if raw.get("total") is not None else round(total, 2),
            )
        )
        for sku, options, quantity, unit_price in lines:
            order_items.insert(
                OrderItem(
                    id=None,
                    order_id=order_id,
                    sku=sku,
                    options=options,
                    quantity=quantity,
                    unit_price=unit_price,
                )
            )


def set_environment(config):
    """Create the tables and re-seed them from the Hydra config."""
    global app, logo_title_container
    global db, products, cart_items, orders, order_items

    app.config = config
    db = database(config.onlineshop.database_path)

    # Table names are given explicitly. Left to itself fastlite derives them
    # from the class name and singularises, which produces a table called
    # `order` -- a SQL reserved word that has to be quoted in every hand-written
    # query. The point of this schema is that other apps and humans can read it
    # directly, so the names are plural and boring.
    products = db.create(Product, name="products", pk="sku")
    cart_items = db.create(CartItem, name="cart_items", pk="id")
    orders = db.create(Order, name="orders", pk="order_id")
    order_items = db.create(OrderItem, name="order_items", pk="id")

    # set_environment is called again on reset, so clear before re-seeding
    # rather than relying on the caller having dropped the tables.
    for table in (order_items, orders, cart_items, products):
        table.delete_where()

    _seed_products(config)
    _build_fts()
    _seed_cart(config)
    _seed_orders(config)

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.onlineshop,
        base_url="/onlineshop",
        current_file_path=__file__,
    )


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _fts_match_query(text: str) -> str | None:
    """Turn free text into an FTS5 MATCH expression.

    Tokens are quoted and OR-ed: quoting keeps FTS5 operators in user input
    (``*``, ``NEAR``, an unbalanced quote) from being interpreted or raising,
    and OR rather than AND matches how the Lucene-backed original behaved --
    a query returns its best partial matches instead of nothing.
    """
    tokens = re.findall(r"[0-9a-z]+", (text or "").lower())
    if not tokens:
        return None
    return " OR ".join(f'"{token}"' for token in tokens)


def search_products(keywords: str) -> list:
    """Products matching ``keywords``, best first, ranked by FTS5 BM25.

    Column weights favour a title hit over an option or bullet hit over a
    description hit. ``bm25()`` returns a negative score where more negative
    is a better match, so ascending order is best-first.
    """
    match = _fts_match_query(keywords)
    if match is None:
        return list(products())
    rows = db.q(
        "SELECT sku FROM products_fts WHERE products_fts MATCH ? "
        "ORDER BY bm25(products_fts, 0.0, 10.0, 3.0, 1.0, 3.0)",
        (match,),
    )
    return [_row(products, row["sku"]) for row in rows]


def paginate(items: list, page: int) -> list:
    per_page = _per_page()
    return items[(page - 1) * per_page: page * per_page]


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

# Product imagery is generated rather than fetched. The original pointed every
# thumbnail at an Amazon CDN URL, which is outbound network the eval nodes do
# not have (and which `tests/test_no_egress.py` exists to prevent). A
# deterministic swatch keyed on the sku gives each product a stable, visually
# distinct image with no files and no requests.
_SWATCH_HUES = (12, 38, 64, 96, 140, 175, 205, 232, 262, 291, 318, 344)

# Line-art glyphs, each drawn inside a 100x100 box. An entry is either a path
# `d` string or a ("c", cx, cy, r) circle. Stroke-only, so a glyph inherits the
# swatch's foreground colour and stays legible at 72px in the cart.
_GLYPHS: dict[str, list] = {
    # electronics
    "headphones": ["M22 64 V50 a28 28 0 0 1 56 0 V64",
                   "M15 62 h15 v25 h-15 z", "M70 62 h15 v25 h-15 z"],
    "monitor": ["M14 24 h72 v44 h-72 z", "M50 68 v10", "M34 82 h32"],
    "keyboard": ["M12 36 h76 v34 h-76 z",
                 "M22 46 h8 M38 46 h8 M54 46 h8 M70 46 h8", "M32 60 h36"],
    "speaker": ["M28 14 h44 v72 h-44 z", ("c", 50, 36, 11), ("c", 50, 65, 7)],
    "hub": ["M18 46 h64 v20 h-64 z", "M32 46 v-16 M50 46 v-22 M68 46 v-16"],
    # home & kitchen
    "pan": ["M18 44 h50 v28 a12 12 0 0 1 -12 12 h-26 a12 12 0 0 1 -12 -12 z",
            "M68 52 h18", "M14 44 h58"],
    "mug": ["M24 38 h40 v34 a10 10 0 0 1 -10 10 h-20 a10 10 0 0 1 -10 -10 z",
            "M64 46 h8 a11 11 0 0 1 0 22 h-8",
            "M36 28 q5 -8 0 -15 M52 28 q5 -8 0 -15"],
    "kettle": ["M30 46 h34 v28 a10 10 0 0 1 -10 10 h-14 a10 10 0 0 1 -10 -10 z",
               "M64 52 q17 -5 14 -24", "M36 46 q13 -18 26 0"],
    "board": ["M26 18 h40 a8 8 0 0 1 8 8 v48 a8 8 0 0 1 -8 8 h-40 a8 8 0 0 1 -8 -8 "
              "v-48 a8 8 0 0 1 8 -8 z", ("c", 46, 30, 4)],
    # furniture
    "table": ["M12 40 h76", "M24 40 v36 M76 40 v36"],
    "chair": ["M32 16 v42 M68 16 v42", "M24 58 h52", "M32 58 v26 M68 58 v26",
              "M32 30 h36"],
    "shelf": ["M22 14 h56 v72 h-56 z", "M22 38 h56 M22 62 h56"],
    "desk": ["M14 38 h72", "M50 38 v36", "M32 80 h36"],
    # apparel
    "shirt": ["M36 18 l-20 12 l10 14 l10 -6 v44 h28 v-44 l10 6 l10 -14 l-20 -12 z",
              "M42 18 q8 10 16 0"],
    "jacket": ["M36 18 l-20 12 l10 14 l10 -6 v44 h28 v-44 l10 6 l10 -14 l-20 -12 z",
               "M50 32 v50"],
    "shoe": ["M14 68 h58 a12 12 0 0 0 12 -11 l-24 -22 l-10 8 l-14 -6 l-22 10 z",
             "M40 46 l9 7 M50 41 l9 7"],
    "scarf": ["M30 20 q20 14 40 0", "M34 36 q16 12 32 0", "M40 50 v32 M60 50 v32"],
    # outdoors
    "tent": ["M12 78 L50 20 L88 78 z", "M50 78 L38 46 M50 78 L62 46"],
    "sleepingbag": ["M32 18 h36 a10 10 0 0 1 10 10 v42 a16 16 0 0 1 -16 16 h-24 "
                    "a16 16 0 0 1 -16 -16 v-42 a10 10 0 0 1 10 -10 z", "M60 22 v60"],
    "poles": ["M34 16 v68 M66 16 v68", "M28 16 h12 M60 16 h12"],
    "bottle": ["M42 14 h16 v14 l7 12 v48 a7 7 0 0 1 -7 7 h-16 a7 7 0 0 1 -7 -7 "
               "v-48 l7 -12 z", "M35 56 h30"],
    "backpack": ["M26 34 h48 v46 a7 7 0 0 1 -7 7 h-34 a7 7 0 0 1 -7 -7 z",
                 "M38 34 q12 -20 24 0", "M36 60 h28 v20 h-28 z"],
    # beauty
    "dropper": ["M38 40 h24 v34 a9 9 0 0 1 -9 9 h-6 a9 9 0 0 1 -9 -9 z",
                "M44 40 v-18 h12 v18", "M44 16 h12"],
    "jar": ["M30 46 h40 v28 a9 9 0 0 1 -9 9 h-22 a9 9 0 0 1 -9 -9 z",
            "M26 32 h48 v14 h-48 z"],
    "tube": ["M38 34 h24 v42 a9 9 0 0 1 -9 9 h-6 a9 9 0 0 1 -9 -9 z",
             "M42 20 h16 v14 h-16 z", "M38 34 h24"],
    # grocery
    "beans": [("c", 40, 42, 13), "M40 29 q7 13 0 26", ("c", 62, 64, 13),
              "M62 51 q7 13 0 26"],
    "tin": ["M32 32 h36 v52 h-36 z", "M28 20 h44 v12 h-44 z", "M32 56 h36"],
    "oil": ["M44 14 h12 v18 l8 12 v42 h-28 v-42 l8 -12 z", "M36 60 h28"],
    "honey": ["M32 40 h36 v34 a9 9 0 0 1 -9 9 h-18 a9 9 0 0 1 -9 -9 z",
              "M32 40 q18 -14 36 0", "M40 56 h20"],
    "chocolate": ["M22 28 h56 v46 h-56 z", "M22 51 h56 M41 28 v46 M59 28 v46"],
    # office
    "notebook": ["M28 16 h44 v70 h-44 z", "M37 16 v70",
                 "M45 34 h20 M45 48 h20 M45 62 h20"],
    "pen": ["M60 16 l24 24 l-44 44 l-24 -24 z", "M16 60 l-4 30 l30 -4",
            "M52 24 l24 24"],
    "organizer": ["M18 40 h64 v40 h-64 z", "M40 40 v40 M60 40 v40",
                  "M26 40 v-14 M32 40 v-18"],
    "lamp": ["M32 20 h36 l10 22 h-56 z", "M50 42 v34", "M32 82 h36"],
    "shredder": ["M18 30 h64 v24 h-64 z", ("c", 70, 42, 4),
                 "M30 62 v22 M42 62 v16 M54 62 v22 M66 62 v16"],
}

# Title keyword -> glyph. First match wins, so order matters where a title
# could hit two entries ("standing desk" must beat nothing, "dining table"
# must not be caught by a later, broader word).
#
# Keywords match on word boundaries, not as bare substrings -- see
# `_GLYPH_PATTERNS`. Real catalog titles are long retailer strings, and
# "pen" inside "Open Toe Sandal" or "tent" inside "content" put obviously
# wrong line art on the listing before the boundaries went in.
_GLYPH_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("headphone", "headphones"), ("earbud", "headphones"), ("headset", "headphones"),
    ("monitor", "monitor"), ("keyboard", "keyboard"), ("speaker", "speaker"),
    # The hub glyph is a box with leads coming out of it, which reads well for
    # anything that plugs into something else.
    ("hub", "hub"), ("cable", "hub"), ("cord", "hub"), ("charger", "hub"),
    ("adapter", "hub"), ("usb", "hub"), ("hard drive", "hub"),
    ("cookware", "pan"), ("skillet", "pan"), ("frying pan", "pan"),
    ("pour-over", "mug"), ("coffee set", "mug"), ("mug", "mug"),
    ("kettle", "kettle"), ("cutting board", "board"), ("mirror", "board"),
    # Furniture. "table" is broad but unambiguous within a product title.
    ("dining table", "table"), ("console table", "table"), ("coffee table", "table"),
    ("sofa table", "table"), ("vanity", "table"), ("table", "table"),
    ("office chair", "chair"), ("armchair", "chair"), ("chair", "chair"),
    ("stool", "chair"), ("sofa", "chair"), ("couch", "chair"),
    # "desk" is deliberately late: "Desk Organizer" and "LED Desk Lamp" both
    # contain it and must not be caught by the standing-desk entry.
    ("bookcase", "shelf"), ("shelf", "shelf"), ("shelving", "shelf"),
    ("desk organizer", "organizer"), ("lamp", "lamp"), ("desk", "desk"),
    ("sweater", "shirt"), ("shirt", "shirt"), ("blouse", "shirt"),
    ("dress", "shirt"), ("lingerie", "shirt"), ("sleepwear", "shirt"),
    ("pajama", "shirt"), ("bra", "shirt"), ("shorts", "shirt"),
    ("jacket", "jacket"), ("coat", "jacket"), ("hoodie", "jacket"),
    ("shoes", "shoe"), ("sandal", "shoe"), ("sneaker", "shoe"), ("boot", "shoe"),
    ("scarf", "scarf"),
    ("tent", "tent"), ("sleeping bag", "sleepingbag"), ("poles", "poles"),
    ("water bottle", "bottle"), ("tumbler", "bottle"),
    ("daypack", "backpack"), ("backpack", "backpack"),
    # Beauty. dropper = serum bottle, tube = squeezable, jar = potted.
    ("serum", "dropper"), ("hair treatment", "dropper"), ("essence", "dropper"),
    ("cleanser", "tube"), ("sunscreen", "tube"), ("shampoo", "tube"),
    ("conditioner", "tube"), ("lotion", "tube"), ("lipstick", "tube"),
    ("mascara", "tube"), ("toothpaste", "tube"), ("trimmer", "tube"),
    ("razor", "tube"), ("hair", "tube"),
    ("mask", "jar"), ("makeup", "jar"), ("blush", "jar"), ("cream", "jar"),
    ("balm", "jar"), ("powder", "jar"),
    ("mouthwash", "bottle"), ("perfume", "bottle"), ("fragrance", "bottle"),
    ("coffee beans", "beans"), ("coffee", "beans"), ("matcha", "tin"),
    ("tea", "tin"), ("olive oil", "oil"), ("oil", "oil"),
    ("honey", "honey"), ("chocolate", "chocolate"), ("candy", "chocolate"),
    ("notebook", "notebook"), ("journal", "notebook"), ("planner", "notebook"),
    ("pen", "pen"), ("pencil", "pen"), ("marker", "pen"),
    ("shredder", "shredder"),
)

# Keywords compiled with word boundaries, in table order. `\b` on both ends
# means "pen" matches "Fountain Pen" and "Pen, Black" but not "Open" or
# "Pendant"; multi-word keywords are unaffected. The optional `(?:e?s)?`
# keeps plurals working, which the bare substring match used to get for free
# -- without it "headphone" stops matching "Headphones". Built once at import.
_GLYPH_PATTERNS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(rf"\b{re.escape(keyword)}(?:e?s)?\b"), glyph)
    for keyword, glyph in _GLYPH_KEYWORDS
)

# Fallback when no keyword matches, so a product added to the config without a
# matching keyword still gets something category-appropriate rather than a blank.
_CATEGORY_GLYPHS: dict[str, str] = {
    "electronics": "monitor",
    "home_kitchen": "mug",
    "furniture": "chair",
    "apparel": "shirt",
    "outdoors": "backpack",
    "beauty": "jar",
    "grocery": "tin",
    "office": "notebook",
}


def _glyph_for(product) -> list:
    title = product.title.lower()
    for pattern, glyph in _GLYPH_PATTERNS:
        if pattern.search(title):
            return _GLYPHS[glyph]
    return _GLYPHS[_CATEGORY_GLYPHS.get(product.category, "notebook")]


def _glyph_svg(product, size: int = 120):
    """A deterministic line-art thumbnail for a product.

    Generated rather than fetched, and the default for that reason: the eval
    nodes have no outbound network, so a hotlinked image does not arrive there
    while the page still returns 200 -- the failure `tests/test_no_egress.py`
    exists to catch. Drawing the product category makes a listing scan like a
    shop rather than a spreadsheet while staying deterministic,
    dependency-free and about 2kB.

    Hue is keyed on the sku so a product's colour is stable across pages and
    two products in the same category still look distinct.
    """
    hue = _SWATCH_HUES[sum(product.sku.encode()) % len(_SWATCH_HUES)]
    stroke = f"hsl({hue}, 55%, 28%)"
    marks = []
    for item in _glyph_for(product):
        if isinstance(item, tuple):
            _, cx, cy, r = item
            marks.append(Circle(cx=cx, cy=cy, r=r, fill="none", stroke=stroke,
                                stroke_width=5))
        else:
            marks.append(Path(d=item, fill="none", stroke=stroke, stroke_width=5,
                              stroke_linecap="round", stroke_linejoin="round"))
    return Svg(
        Rect(x=0, y=0, width=100, height=100, rx=10, fill=f"hsl({hue}, 42%, 88%)"),
        G(*marks),
        width=size, height=size,
        viewBox="0 0 100 100",
        role="img",
        aria_label=product.title,
        cls="product-thumb",
    )


def _product_images(product) -> list[str]:
    """The product's hotlinkable image URLs, empty unless the mode allows them.

    Gated here rather than at each call site so that turning the mode off is
    guaranteed to remove every `<img>` on every page, which is what the egress
    tests assert.
    """
    if image_mode() != "hotlink":
        return []
    try:
        return [u for u in json.loads(product.images or "[]") if u]
    except (TypeError, ValueError, AttributeError):
        return []


def product_image(product, size: int = 120):
    """A product thumbnail: hotlinked photos if configured, else line art.

    `apps.onlineshop.product_images` picks between them and defaults to
    `glyphs`. Under `hotlink` the catalog's own image URLs are rendered, as a
    carousel when a product has more than one, with the glyph kept in the
    markup underneath as a fallback -- see `_image_carousel`.
    """
    urls = _product_images(product)
    if not urls:
        return _glyph_svg(product, size)
    return _image_carousel(product, urls, size)


def _image_carousel(product, urls: list[str], size: int):
    """Hotlinked product photos, with the generated glyph as the fallback.

    Two failure modes have to degrade to the glyph rather than to a broken
    image icon, because a screenshot-scored agent is graded on what the page
    looks like:

    * the host has no outbound network, or the CDN refuses the request. Each
      `<img>` carries an `onerror` that marks the container, and the CSS then
      hides the images and reveals the glyph. Inline rather than a script tag
      so it survives the page being rendered in isolation. One failure takes
      the whole carousel down to the glyph deliberately: the common case is
      "no network", where all of them fail anyway, and a half-populated
      carousel is a worse observation than a consistent one.
    * the product has no usable URLs at all, which `product_image` handles
      before getting here.

    Multiple images become a CSS-only carousel: one radio per slide and a
    label per dot, so the controls are real clickable elements for an agent
    without a line of JavaScript. Single-image products skip the controls.
    """
    # A radio group per product, so two carousels on one listing page do not
    # steer each other.
    group = f"carousel-{product.sku}"
    slides, dots = [], []
    for index, url in enumerate(urls):
        slide_id = f"{group}-{index}"
        slides.append(
            Input(type="radio", name=group, id=slide_id, cls="carousel-state",
                  checked=(index == 0))
        )
        slides.append(
            Img(
                src=url,
                alt=f"{product.title} (image {index + 1} of {len(urls)})",
                width=size, height=size,
                loading="lazy",
                cls="carousel-slide",
                onerror="this.closest('.product-media').classList.add('images-failed')",
            )
        )
        dots.append(
            Label(
                "",
                _for=slide_id,
                cls="carousel-dot",
                title=f"Image {index + 1}",
                aria_label=f"Show image {index + 1} of {len(urls)}",
            )
        )

    children = [*slides, _glyph_svg(product, size)]
    if len(urls) > 1:
        children.append(Div(*dots, cls="carousel-dots"))
    return Div(
        *children,
        cls="product-media product-thumb",
        style=f"--thumb-size:{size}px",
    )


def stars(rating: float) -> str:
    return f"★ {rating:.1f}"


def item_href(product, keywords: str = "") -> str:
    suffix = f"?keywords={keywords}" if keywords else ""
    return f"/onlineshop/item/{product.sku}{suffix}"


def page_shell(*content):
    """Theme block, styles, header and a link home around every page."""
    return Div(
        shop_theme(),
        styles,
        logo_title_container,
        *content,
        A("Return to List of Apps", href="/", role="button", cls="contrast",
          style="margin-top: 1rem;"),
    )


def search_bar(value: str = ""):
    """Shop chrome: browse link, search, cart and orders.

    The "Shop" link is the way back to the landing page from anywhere. It
    lives here rather than on the header logo because `clickable_logo` is a
    start-page variation axis (see `config/apps/start_page/appearance/`) and
    defaults to false -- navigation must not depend on a difficulty knob.
    """
    count = sum(row.quantity for row in _cart_rows())
    return Div(
        A("Shop", href="/onlineshop", cls="btn btn-neutral", role="button"),
        Form(
            Input(
                type="search",
                name="search_query",
                placeholder="Search products",
                value=value,
                aria_label="Search products",
            ),
            Button("Search", cls="btn btn-primary", type="submit"),
            action="/onlineshop/search",
            method="post",
        ),
        A(f"Cart ({count})" if count else "Cart",
          href="/onlineshop/cart", cls="btn btn-neutral", role="button"),
        A("Orders", href="/onlineshop/orders", cls="btn btn-neutral", role="button"),
        cls="shop-bar",
    )


def category_strip(active: str = ""):
    links = [
        A(
            label,
            href=f"/onlineshop/category/{slug}/1",
            cls=f"shop-category{' active' if slug == active else ''}",
        )
        for slug, label in _categories().items()
    ]
    return Div(*links, cls="shop-categories") if links else ""


def product_card(product, keywords: str = ""):
    """One product in the `default` (row) or `grid` layout."""
    body = Div(
        H4(A(product.title, href=item_href(product, keywords)), cls="card-title"),
        Div(
            Span(money(product.price), cls="card-price"),
            Span(stars(product.rating), cls="card-rating"),
        ),
        P(truncate(product.description, 140), cls="card-desc"),
        A("View Details", href=item_href(product, keywords), cls="btn btn-primary", role="button"),
        cls="product-body",
    )
    if current_layout() == "grid":
        return Div(product_image(product, 140), body, cls="card")
    return Div(
        Div(product_image(product), body, cls="product-row"),
        cls="card",
    )


def truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def product_table(products_page, keywords: str = ""):
    """The `compact_table` layout: no imagery, one dense row per product."""
    header = Thead(Tr(Th("Product"), Th("Category"), Th("Rating"), Th("Price"), Th("")))
    rows = [
        Tr(
            Td(A(product.title, href=item_href(product, keywords))),
            Td(_categories().get(product.category, product.category), cls="muted"),
            Td(stars(product.rating), cls="muted"),
            Td(money(product.price)),
            Td(A("View", href=item_href(product, keywords), cls="btn btn-primary", role="button")),
        )
        for product in products_page
    ]
    return Table(header, Tbody(*rows), cls="product-table")


def product_listing(products_page, keywords: str = ""):
    """Render a page of products in whichever layout is configured."""
    if not products_page:
        return P("No products matched.", cls="muted")
    layout = current_layout()
    if layout == "compact_table":
        return product_table(products_page, keywords)
    cards = [product_card(product, keywords) for product in products_page]
    if layout == "grid":
        return Div(*cards, cls="product-grid")
    return Div(*cards)


def pagination(base: str, page: int, total: int):
    last = max(1, (total + _per_page() - 1) // _per_page())
    prev_link = (
        A("Previous", href=f"{base}/{page - 1}", cls="btn btn-neutral", role="button")
        if page > 1 else Span("Previous", cls="muted")
    )
    next_link = (
        A("Next", href=f"{base}/{page + 1}", cls="btn btn-neutral", role="button")
        if page < last else Span("Next", cls="muted")
    )
    return Div(prev_link, Span(f"Page {page} of {last} ({total} products)", cls="muted"),
               next_link, cls="pagination")


# --------------------------------------------------------------------------
# Catalog routes
# --------------------------------------------------------------------------

@rt("/onlineshop")
def get():
    cfg = _cfg()
    promo = getattr(cfg, "promotional_message", "")
    featured = list(products())[:_per_page()]
    return page_shell(
        search_bar(),
        Div(promo, cls="shop-promo") if promo else "",
        P(getattr(cfg, "description", ""), cls="muted"),
        category_strip(),
        H3("Featured Products"),
        product_listing(featured),
    )


@rt("/onlineshop/search")
def post(search_query: str = ""):
    keywords = ",".join(search_query.lower().split()) or "all"
    return RedirectResponse(url=f"/onlineshop/search/{keywords}/1", status_code=303)


@rt("/onlineshop/search/{keywords}/{page}")
def get(keywords: str, page: int):
    terms = keywords.replace(",", " ")
    matched = search_products("" if keywords == "all" else terms)
    return page_shell(
        search_bar(terms),
        Div(
            A("Home", href="/onlineshop"), " / ", Span(f'Results for "{terms}"'),
            cls="breadcrumb",
        ),
        H3(f'Search Results for "{terms}"'),
        product_listing(paginate(matched, page), keywords),
        pagination(f"/onlineshop/search/{keywords}", page, len(matched)),
    )


@rt("/onlineshop/category/{slug}/{page}")
def get(slug: str, page: int):
    label = _categories().get(slug, slug)
    matched = [p for p in products() if p.category == slug]
    return page_shell(
        search_bar(),
        Div(A("Home", href="/onlineshop"), " / ", Span(label), cls="breadcrumb"),
        category_strip(active=slug),
        H3(label),
        product_listing(paginate(matched, page)),
        pagination(f"/onlineshop/category/{slug}", page, len(matched)),
    )


@rt("/onlineshop/item/{sku}")
def get(sku: str, keywords: str = ""):
    product = _row(products, sku)
    if product is None:
        return page_shell(H3("Product not found"), A("Back to shop", href="/onlineshop"))

    available = json.loads(product.options)
    option_inputs = [
        Div(
            Label(name.replace("_", " ").title(), _for=f"opt-{name}"),
            Div(
                Select(
                    *[Option(value, value=value) for value in values],
                    id=f"opt-{name}",
                    name=f"option_{name}",
                ),
                cls="option-values",
            ),
            cls="option-group",
        )
        for name, values in available.items()
    ]

    crumbs = json.loads(product.breadcrumb)
    breadcrumb = Div(
        A("Home", href="/onlineshop"),
        *[Span(" / ", crumb) for crumb in crumbs],
        cls="breadcrumb",
    )
    extra = getattr(_cfg(), "additional_info_to_item", "")

    return page_shell(
        search_bar(keywords.replace(",", " ")),
        breadcrumb,
        Div(
            Div(product_image(product, 200), cls="product-thumb"),
            Div(
                H3(product.title, cls="card-title"),
                Div(
                    Span(money(product.price), cls="card-price"),
                    Span(stars(product.rating), cls="card-rating"),
                ),
                P(product.description),
                Ul(*[Li(bullet) for bullet in json.loads(product.bullets)]),
                P(extra, cls="muted") if extra else "",
                Form(
                    *option_inputs,
                    Input(type="number", name="quantity", value="1", min="1", max="99",
                          aria_label="Quantity", style="max-width: 7rem;"),
                    Button("Add to Cart", cls="btn btn-primary", type="submit"),
                    action=f"/onlineshop/cart/add/{product.sku}",
                    method="post",
                ),
                cls="product-body",
            ),
            cls="product-row",
        ),
    )


# --------------------------------------------------------------------------
# Cart routes
# --------------------------------------------------------------------------

def _cart_rows():
    return list(cart_items())


def _cart_total(selected_only: bool = True) -> float:
    total = 0.0
    for row in _cart_rows():
        if selected_only and not row.selected:
            continue
        product = _row(products, row.sku)
        if product is not None:
            total += product.price * row.quantity
    return round(total, 2)


def _find_cart_line(sku: str, options_key: str):
    for row in _cart_rows():
        if row.sku == sku and row.options == options_key:
            return row
    return None


@rt("/onlineshop/cart/add/{sku}")
async def post(req, sku: str):
    """Add to cart. Options arrive as `option_<name>` fields from the item form."""
    if _row(products, sku) is None:
        return RedirectResponse(url="/onlineshop", status_code=303)

    form = await req.form()
    chosen = {
        key[len("option_"):]: value
        for key, value in form.items()
        if key.startswith("option_")
    }
    try:
        quantity = max(1, int(form.get("quantity", 1)))
    except (TypeError, ValueError):
        quantity = 1

    options_key = _options_key(_validated_options(sku, chosen))
    existing = _find_cart_line(sku, options_key)
    if existing is not None:
        existing.quantity += quantity
        cart_items.update(existing)
    else:
        cart_items.insert(
            CartItem(id=None, sku=sku, options=options_key, quantity=quantity, selected=True)
        )
    return RedirectResponse(url="/onlineshop/cart", status_code=303)


def cart_line(row):
    product = _row(products, row.sku)
    if product is None:
        return ""
    chosen = json.loads(row.options)
    # Options render as chips rather than a muted caption: two lines of the
    # same product differ *only* by their options, so that difference has to
    # be the most visible thing on the line or the cart reads as duplicated.
    option_chips = Div(
        *[Span(f"{name}: {value}", cls="option-chip") for name, value in chosen.items()],
        cls="option-chips",
    ) if chosen else ""
    line_total = product.price * row.quantity
    return Div(
        Div(
            product_image(product, 72),
            Div(
                H4(A(product.title, href=item_href(product)), cls="card-title"),
                option_chips,
                Div(
                    Span(f"{money(product.price)} each", cls="muted"),
                    Span(" x ", cls="muted"),
                    Span(str(row.quantity), cls="muted"),
                    Span(" = ", cls="muted"),
                    Span(money(line_total), cls="card-price"),
                ),
                Span("Not selected for checkout", cls="muted") if not row.selected else "",
                cls="cart-line-body",
            ),
            Div(
                Form(
                    Input(type="number", name="quantity", value=str(row.quantity),
                          min="1", max="99", aria_label=f"Quantity for {product.title}",
                          style="max-width: 6rem;"),
                    Button("Update", cls="btn btn-accent", type="submit"),
                    action=f"/onlineshop/cart/quantity/{row.id}",
                    method="post",
                ),
                Form(
                    Button("Remove", cls="btn btn-danger", type="submit"),
                    action=f"/onlineshop/cart/remove/{row.id}",
                    method="post",
                ),
                Form(
                    Button("Deselect" if row.selected else "Select",
                           cls="btn btn-neutral", type="submit"),
                    action=f"/onlineshop/cart/toggle/{row.id}",
                    method="post",
                ),
            ),
            cls="cart-line",
        ),
        cls="card",
    )


@rt("/onlineshop/cart")
def get():
    rows = _cart_rows()
    if not rows:
        return page_shell(
            search_bar(),
            H3("Your Cart"),
            P("Your cart is empty.", cls="muted"),
            A("Browse products", href="/onlineshop", cls="btn btn-primary", role="button"),
        )
    return page_shell(
        search_bar(),
        H3("Your Cart"),
        *[cart_line(row) for row in rows],
        Div(Span("Selected total: ", cls="muted"),
            Span(money(_cart_total()), cls="cart-total")),
        A("Proceed to Checkout", href="/onlineshop/checkout",
          cls="btn btn-primary", role="button", style="margin-top: 0.8rem;"),
    )


@rt("/onlineshop/cart/remove/{item_id}")
def post(item_id: int):
    cart_items.delete(item_id)
    return RedirectResponse(url="/onlineshop/cart", status_code=303)


@rt("/onlineshop/cart/quantity/{item_id}")
def post(item_id: int, quantity: int = 1):
    row = _row(cart_items, item_id)
    if row is not None:
        if quantity <= 0:
            cart_items.delete(item_id)
        else:
            row.quantity = min(99, quantity)
            cart_items.update(row)
    return RedirectResponse(url="/onlineshop/cart", status_code=303)


@rt("/onlineshop/cart/toggle/{item_id}")
def post(item_id: int):
    row = _row(cart_items, item_id)
    if row is not None:
        row.selected = not row.selected
        cart_items.update(row)
    return RedirectResponse(url="/onlineshop/cart", status_code=303)


# --------------------------------------------------------------------------
# Checkout and orders
# --------------------------------------------------------------------------

def _allowed_cards() -> list:
    return [str(c) for c in (_plain(getattr(_cfg(), "allowed_credit_cards", [])) or [])]


@rt("/onlineshop/checkout")
def get(error: str = ""):
    selected = [row for row in _cart_rows() if row.selected]
    if not selected:
        return page_shell(
            search_bar(),
            H3("Checkout"),
            P("No items are selected for checkout.", cls="muted"),
            A("Back to cart", href="/onlineshop/cart", cls="btn btn-primary", role="button"),
        )

    card_field = ""
    if getattr(_cfg(), "enable_credit_card_check", False):
        card_field = Div(
            Label("Card Type", _for="card_type"),
            Select(*[Option(card, value=card) for card in _allowed_cards()],
                   id="card_type", name="card_type"),
            cls="option-group",
        )

    return page_shell(
        search_bar(),
        H3("Checkout"),
        P(f"{len(selected)} item(s) selected.", cls="muted"),
        Div(Span("Order total: ", cls="muted"),
            Span(money(_cart_total()), cls="cart-total"), cls="card"),
        P(error, style="color: var(--color-danger);") if error else "",
        Form(
            Div(Label("Full Name", _for="name"),
                Input(id="name", name="name", required=True), cls="option-group"),
            Div(Label("Shipping Address", _for="address"),
                Input(id="address", name="address", required=True), cls="option-group"),
            card_field,
            Button("Place Order", cls="btn btn-primary", type="submit"),
            action="/onlineshop/checkout",
            method="post",
        ),
    )


@rt("/onlineshop/checkout")
def post(name: str = "", address: str = "", card_type: str = ""):
    selected = [row for row in _cart_rows() if row.selected]
    if not selected:
        return RedirectResponse(url="/onlineshop/cart", status_code=303)

    if getattr(_cfg(), "enable_credit_card_check", False):
        allowed = _allowed_cards()
        if card_type not in allowed:
            message = f"{card_type or 'That card'} is not accepted. Allowed: {', '.join(allowed)}."
            return RedirectResponse(
                url=f"/onlineshop/checkout?error={message}", status_code=303
            )

    order_id = uuid.uuid4().hex[:8]
    orders.insert(
        Order(
            order_id=order_id,
            name=name,
            address=address,
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status="Processing",
            total=_cart_total(),
        )
    )
    for row in selected:
        product = _row(products, row.sku)
        order_items.insert(
            OrderItem(
                id=None,
                order_id=order_id,
                sku=row.sku,
                options=row.options,
                quantity=row.quantity,
                unit_price=product.price if product else 0.0,
            )
        )
        cart_items.delete(row.id)
    return RedirectResponse(url="/onlineshop/orders", status_code=303)


def _lines_for(order_id: str) -> list:
    return [row for row in order_items() if row.order_id == order_id]


def order_card(order):
    lines = []
    for row in _lines_for(order.order_id):
        product = _row(products, row.sku)
        title = product.title if product else row.sku
        chosen = json.loads(row.options)
        detail = ", ".join(f"{name}: {value}" for name, value in chosen.items())
        lines.append(
            Li(f"{row.quantity} x {title}",
               Span(f" ({detail})", cls="muted") if detail else "",
               Span(f" - {money(row.unit_price * row.quantity)}", cls="muted"))
        )
    return Div(
        Div(
            H4(f"Order {order.order_id}", cls="card-title"),
            Span(order.status, cls="status-pill"),
            cls="order-head",
        ),
        P(f"{order.date} - {order.name}, {order.address}", cls="muted"),
        Ul(*lines),
        Div(Span("Total: ", cls="muted"), Span(money(order.total), cls="cart-total")),
        cls="card",
    )


@rt("/onlineshop/orders")
def get():
    all_orders = list(orders())
    if not all_orders:
        return page_shell(search_bar(), H3("Your Orders"),
                          P("You have no orders yet.", cls="muted"))
    return page_shell(
        search_bar(),
        H3("Your Orders"),
        *[order_card(order) for order in reversed(all_orders)],
    )


# --------------------------------------------------------------------------
# Reward surface
# --------------------------------------------------------------------------

@app.get("/onlineshop_all")
def get_all(include_catalog: bool = False):
    """Cart and order history as plain JSON. Used for rewards.

    Deliberately flat and id-free where it can be: ``open_apps.tasks``
    normalises away database ids, and every value here is either a stable sku
    or something the task actually asked for.
    """
    cart = []
    for row in _cart_rows():
        product = _row(products, row.sku)
        cart.append({
            "sku": row.sku,
            "title": product.title if product else None,
            "options": json.loads(row.options),
            "quantity": row.quantity,
            "selected": bool(row.selected),
            "unit_price": product.price if product else None,
        })

    order_list = []
    for order in orders():
        order_list.append({
            "order_id": order.order_id,
            "name": order.name,
            "address": order.address,
            "date": order.date,
            "status": order.status,
            "total": order.total,
            "items": [
                {
                    "sku": row.sku,
                    "options": json.loads(row.options),
                    "quantity": row.quantity,
                    "unit_price": row.unit_price,
                }
                for row in _lines_for(order.order_id)
            ],
        })

    response = {"cart": cart, "orders": order_list}
    if include_catalog:
        response["catalog"] = [
            {
                "sku": product.sku,
                "title": product.title,
                "price": product.price,
                "category": product.category,
                "options": json.loads(product.options),
            }
            for product in products()
        ]
    return Response(json.dumps(response), headers={"Content-Type": "application/json"})


def get_onlineshop_routes():
    return app.routes


if __name__ == "__main__":
    print("Warning: Running onlineshop app in standalone mode")
    app.routes = get_onlineshop_routes()
    serve()
