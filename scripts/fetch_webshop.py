#!/usr/bin/env python3
"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Optional setup: build the online shop's catalog from the WebShop dataset.

The shop ships with no catalog, so it does not appear in OpenApps until this
script has been run. Running it writes a `webshop` content pack, which is
gitignored -- the scraped product data stays on the machine that downloaded it
and is never committed to this repository.

    uv run scripts/fetch_webshop.py --inspect      # look, write nothing
    uv run scripts/fetch_webshop.py                # write the content pack
    uv run launch.py apps/onlineshop/content=webshop

Source: https://huggingface.co/datasets/YWZBrandon/webshop-data, a mirror of
the item dump from WebShop (Yao et al., 2022, princeton-nlp/WebShop). The
records are scraped Amazon listings, so the fields carry real brand names and
real marketing copy. Two consequences are baked into the conversion below:

* `MainImage` and every other URL is dropped. The eval nodes have no outbound
  network, `tests/test_no_egress.py` fails a page that references one, and the
  shop draws its own line art instead (`onlineshop_app.main.product_image`).
* Nothing this script produces is checked in. `.gitignore` covers the output.

Field names are read defensively: the dataset has been through several
re-exports and the casing is not stable across them. `--inspect` prints the
keys actually present so a mismatch is a one-line fix to `_FIELDS` rather than
a stack trace.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "config" / "apps" / "onlineshop" / "content" / "webshop.yaml"

HF_REPO = "YWZBrandon/webshop-data"

# Preference order for the item dump. The 1000-item file is the one the
# original WebShop demo used and is a few MB; `items_shuffle.json` is the full
# ~1.2M-product export and over a gigabyte. Whichever exists first wins, and
# `--file` overrides.
CANDIDATE_FILES = (
    "items_shuffle_1000.json",
    "items_human_ins.json",
    "items_shuffle.json",
)

# Candidate source keys per output field, tried in order. Extend from the
# `--inspect` output rather than guessing.
# Verified against items_shuffle_1000.json. The leading names are the ones
# that dump actually uses; the rest are older/other exports kept as fallbacks.
_FIELDS: dict[str, tuple[str, ...]] = {
    "sku": ("asin", "ASIN", "product_id", "id"),
    "title": ("name", "Title", "title"),
    "description": ("full_description", "Description", "description", "desc"),
    "bullets": ("small_description", "BulletPoints", "bullet_points", "bullets"),
    "price": ("pricing", "Price", "price"),
    "rating": ("average_rating", "Rating", "rating", "stars"),
    "category": ("category", "Category", "main_category"),
    "breadcrumb": ("product_category", "category_path", "breadcrumb"),
    "options": ("customization_options", "options", "Options"),
    "images": ("images", "Images", "MainImage", "image_urls"),
}

# WebShop's top-level `category` is one of five coarse Amazon storefronts
# (garden, electronics, grocery, fashion, beauty), which does not line up with
# the eight slugs the shop's category strip and `_CATEGORY_GLYPHS` use --
# "garden" alone covers furniture, cookware and camping gear. The breadcrumb
# in `product_category` is far more specific ("Home & Kitchen > Furniture >
# Living Room Furniture > Tables"), so `_category` reads that first, deepest
# crumb first, and only falls back to the coarse field.
#
# Order matters within a single crumb: furniture is listed before kitchen so
# that "Kitchen & Dining Furniture" is furniture rather than home_kitchen.
_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("furniture", "furniture"), ("table", "furniture"), ("chair", "furniture"),
    ("desk", "furniture"), ("sofa", "furniture"), ("couch", "furniture"),
    ("bookcase", "furniture"), ("shelv", "furniture"), ("dresser", "furniture"),
    ("nightstand", "furniture"), ("bed frame", "furniture"),
    ("electronic", "electronics"), ("computer", "electronics"),
    ("cell phone", "electronics"), ("camera", "electronics"),
    ("headphone", "electronics"), ("audio", "electronics"),
    ("monitor", "electronics"), ("television", "electronics"),
    ("office", "office"), ("stationery", "office"), ("school supplies", "office"),
    ("writing", "office"), ("notebook", "office"), ("paper", "office"),
    ("beauty", "beauty"), ("personal care", "beauty"), ("skin", "beauty"),
    ("hair", "beauty"), ("makeup", "beauty"), ("fragrance", "beauty"),
    ("nail", "beauty"), ("shave", "beauty"),
    ("grocery", "grocery"), ("food", "grocery"), ("gourmet", "grocery"),
    ("beverage", "grocery"), ("snack", "grocery"), ("coffee", "grocery"),
    ("tea", "grocery"), ("pantry", "grocery"),
    ("fashion", "apparel"), ("clothing", "apparel"), ("apparel", "apparel"),
    ("shoe", "apparel"), ("jewelry", "apparel"), ("watch", "apparel"),
    ("handbag", "apparel"), ("men's", "apparel"), ("women's", "apparel"),
    ("outdoor", "outdoors"), ("sports", "outdoors"), ("camping", "outdoors"),
    ("patio", "outdoors"), ("garden", "outdoors"), ("hiking", "outdoors"),
    ("home & kitchen", "home_kitchen"), ("kitchen", "home_kitchen"),
    ("appliance", "home_kitchen"), ("cookware", "home_kitchen"),
    ("bath", "home_kitchen"), ("bedding", "home_kitchen"),
    ("storage", "home_kitchen"), ("decor", "home_kitchen"),
)
_FALLBACK_CATEGORY = "home_kitchen"

_URL_RE = re.compile(r"https?://\S+")
_PRICE_RE = re.compile(r"\d+(?:\.\d+)?")

# Caps. The listing pages paginate at ten and truncate descriptions, so a
# 4000-character scraped description is bytes on disk and nothing on screen.
MAX_BULLETS = 5
MAX_DESCRIPTION = 400
MAX_OPTION_GROUPS = 3
MAX_OPTION_VALUES = 6
# Carousel dots are styled with one CSS rule per position in the shop's
# stylesheet; keep these in step.
MAX_IMAGES = 4

# Amazon serves a 1x1 transparent spacer in the image list of nearly every
# record. It is not a product photo and would render as an empty carousel
# slide, so it is dropped rather than counted.
_PLACEHOLDER_IMAGE_RE = re.compile(r"transparent-pixel|/G/01/x-locale/", re.I)
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp)(\?|$)", re.I)


def _get(record: dict, field: str):
    """First present, non-empty value among the candidate keys for `field`."""
    for key in _FIELDS[field]:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _clean(text) -> str:
    """Collapse whitespace and strip any URL out of free text."""
    text = _URL_RE.sub("", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _price(raw) -> float:
    """Parse WebShop's price field, which is a float, `$19.99`, or a range.

    Ranges (`19.99 to 29.99`) take the low end: the shop has one price per
    product and the low end is what a listing page would advertise.
    """
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)
    # Strip thousands separators first: "$1,299.00" would otherwise match the
    # leading "1" and price a monitor at a dollar.
    found = _PRICE_RE.findall(str(raw or "").replace(",", ""))
    return round(float(found[0]), 2) if found else 0.0


def _match_category(text: str) -> str | None:
    for needle, slug in _CATEGORY_RULES:
        if needle in text:
            return slug
    return None


def _category(record: dict) -> str:
    """Map a record onto one of the shop's eight category slugs.

    Deepest breadcrumb crumb first, because that is the most specific
    statement about what the thing is; the coarse `category` field is only a
    tiebreak.
    """
    for crumb in reversed(_breadcrumb(record)):
        slug = _match_category(crumb.lower())
        if slug:
            return slug
    return _match_category(_clean(_get(record, "category")).lower()) or _FALLBACK_CATEGORY


def _breadcrumb(record: dict) -> list[str]:
    raw = _get(record, "breadcrumb")
    if isinstance(raw, list):
        crumbs = [_clean(c) for c in raw]
    else:
        # Amazon renders the path with an angle-quote; exports vary.
        crumbs = [_clean(c) for c in re.split(r"[›>|/]", str(raw or ""))]
    return [c for c in crumbs if c][:4]


def _bullets(record: dict) -> list[str]:
    raw = _get(record, "bullets") or []
    if isinstance(raw, str):
        raw = [raw]
    return [b for b in (_clean(b) for b in raw) if b][:MAX_BULLETS]


def _options(record: dict) -> dict[str, list[str]]:
    """Normalise WebShop's `customization_options` to {name: [values]}.

    The source shape is `{"Color": [{"value": "Bath Ball", "url": ..., "image":
    ..., "price": ...}, ...]}`, and it is `""` rather than `{}` when a product
    has no options at all. Only `value` is carried over: the sibling keys hold
    amazon.com and media-amazon.com URLs, which must not reach a page (see
    `tests/test_no_egress.py`).
    """
    raw = _get(record, "options")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, values in list(raw.items())[:MAX_OPTION_GROUPS]:
        if not values:
            continue
        if not isinstance(values, (list, tuple)):
            values = [values]
        cleaned = []
        for value in values:
            # Dict entries are the real format; bare scalars are tolerated so
            # a differently-shaped export still yields something usable.
            text = _clean(value.get("value") if isinstance(value, dict) else value)
            if text and text not in cleaned:
                cleaned.append(text)
        if cleaned:
            out[_clean(name).lower()] = cleaned[:MAX_OPTION_VALUES]
    return out


def _images(record: dict) -> list[str]:
    """Product photo URLs, deduplicated, spacers removed.

    Kept in the pack so `apps.onlineshop.product_images=hotlink` has something
    to render. They are inert under the default `glyphs` mode -- the shop
    never emits an `<img>` for them -- but they do mean the generated file
    contains amazon.com CDN links, which is the other reason it is gitignored.
    """
    raw = _get(record, "images") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for value in raw:
        url = str(value or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        if _PLACEHOLDER_IMAGE_RE.search(url) or not _IMAGE_EXT_RE.search(url):
            continue
        if url not in out:
            out.append(url)
    return out[:MAX_IMAGES]


def _rating(record: dict, synth: bool) -> float:
    raw = _get(record, "rating")
    if raw is not None:
        try:
            return round(min(5.0, max(0.0, float(raw))), 1)
        except (TypeError, ValueError):
            pass
    if not synth:
        return 0.0
    # Deterministic from the asin so a product's stars are stable across runs.
    # Off by default: it is invented data, and a catalog of honest zeroes is
    # better than a catalog of plausible fictions unless the caller asks.
    sku = str(_get(record, "sku") or "")
    return round(3.5 + (sum(sku.encode()) % 15) / 10.0, 1)


def convert(records: list[dict], limit: int, synth_ratings: bool) -> list[dict]:
    """WebShop records -> the shop's product schema, skipping unusable rows."""
    products, seen = [], set()
    for record in records:
        if len(products) >= limit:
            break
        if not isinstance(record, dict):
            continue
        sku, title = _clean(_get(record, "sku")), _clean(_get(record, "title"))
        if not sku or not title or sku in seen:
            continue
        seen.add(sku)
        products.append(
            {
                "sku": sku,
                "title": title[:120],
                "price": _price(_get(record, "price")),
                "category": _category(record),
                "breadcrumb": _breadcrumb(record),
                "rating": _rating(record, synth_ratings),
                "options": _options(record),
                "bullets": _bullets(record),
                "description": _clean(_get(record, "description"))[:MAX_DESCRIPTION],
                "images": _images(record),
            }
        )
    return products


# Every field except `images` is rendered as text on a page, so a URL in any
# of them would put a link on the page under either image mode. `images` is
# the one place a URL is legitimate, and only `product_images=hotlink` emits it.
_TEXT_FIELDS = ("sku", "title", "category", "breadcrumb", "bullets",
                "description", "options")


def url_leaks(product: dict) -> list[str]:
    """Fields that wrongly contain a URL, for the pre-write check."""
    return [f for f in _TEXT_FIELDS if _URL_RE.search(json.dumps(product[f]))]


def glyph_report(products: list[dict]) -> tuple[Counter, int]:
    """How many products match a glyph keyword vs fall through to the category.

    The keyword table in `onlineshop_app.main` was written against short,
    clean product names. Real Amazon titles are long and brand-led, so this is
    the number that says whether the table needs widening for this dataset.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    # `_GLYPH_PATTERNS`, not `_GLYPH_KEYWORDS`: the app matches on word
    # boundaries, so counting bare substrings here would report a coverage the
    # rendered pages do not actually have.
    from open_apps.apps.onlineshop_app.main import _GLYPH_PATTERNS

    hits, fallbacks = Counter(), 0
    for product in products:
        title = product["title"].lower()
        for pattern, glyph in _GLYPH_PATTERNS:
            if pattern.search(title):
                hits[glyph] += 1
                break
        else:
            fallbacks += 1
    return hits, fallbacks


def download(filename: str | None, inspect: bool) -> tuple[str, list[dict]]:
    """Fetch the item dump, returning (filename, records).

    Uses `huggingface_hub` when it is importable so the download lands in the
    shared HF cache and a re-run is free. Falls back to a plain HTTPS GET of
    the `resolve/main` URL, which needs no extra dependency.
    """
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        HfApi = hf_hub_download = None

    available = []
    if HfApi is not None:
        try:
            available = HfApi().list_repo_files(HF_REPO, repo_type="dataset")
        except Exception as exc:  # network, auth, renamed repo -- all non-fatal
            print(f"    could not list repo files ({exc}); trying known names")

    if inspect and available:
        print(f"\nFiles in {HF_REPO}:")
        for name in sorted(available):
            print(f"    {name}")

    if filename:
        wanted = [filename]
    elif available:
        wanted = [f for f in CANDIDATE_FILES if f in available] or [
            f for f in sorted(available) if f.endswith(".json")
        ]
    else:
        wanted = list(CANDIDATE_FILES)
    if not wanted:
        raise SystemExit(f"No .json item dump found in {HF_REPO}; pass --file")

    last_error = None
    for name in wanted:
        try:
            if hf_hub_download is not None:
                path = hf_hub_download(HF_REPO, name, repo_type="dataset")
                text = Path(path).read_text()
            else:
                import urllib.request

                url = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/{name}"
                with urllib.request.urlopen(url) as response:
                    text = response.read().decode()
            records = json.loads(text)
            if isinstance(records, dict):
                # Some exports wrap the list in {"items": [...]} or key by asin.
                records = records.get("items") or list(records.values())
            print(f"    loaded {name} ({len(records)} records)")
            return name, records
        except Exception as exc:
            last_error = exc
            print(f"    {name}: {exc}")
    raise SystemExit(f"Could not load any item dump from {HF_REPO}: {last_error}")


def inspect(records: list[dict]) -> None:
    """Print the real schema so `_FIELDS` can be corrected against it."""
    sample = [r for r in records[:200] if isinstance(r, dict)]
    if not sample:
        raise SystemExit("No dict records to inspect")

    keys = Counter(k for record in sample for k in record)
    print(f"\nKeys present (of {len(sample)} sampled records):")
    for key, count in keys.most_common():
        example = next((r[key] for r in sample if r.get(key) not in (None, "", [])), "")
        example = _clean(json.dumps(example, default=str))[:70]
        print(f"    {count:>4}x  {key:<28} e.g. {example}")

    print("\nMapping this script expects:")
    for field, candidates in _FIELDS.items():
        matched = next((c for c in candidates if c in keys), None)
        mark = "OK  " if matched else "MISS"
        print(f"    {mark} {field:<12} -> {matched or ' / '.join(candidates)}")

    categories = Counter(_category(r) for r in sample)
    print("\nCategory mapping over the sample:")
    for slug, count in categories.most_common():
        print(f"    {count:>4}x  {slug}")
    raw = Counter(_clean(_get(r, "category")) for r in sample)
    print("\nRaw source category values:")
    for value, count in raw.most_common(15):
        print(f"    {count:>4}x  {value or '(none)'}")


def write_pack(products: list[dict], source_file: str, path: Path) -> None:
    header = f"""# @package apps.onlineshop
#
# GENERATED -- do not edit, and do not commit. Rebuild with:
#     uv run scripts/fetch_webshop.py
#
# Catalog built from {HF_REPO} ({source_file}), a mirror of the WebShop item
# dump (Yao et al., 2022). These are scraped Amazon listings: the titles,
# bullets and descriptions below are the retailer's own copy, and `images`
# holds amazon.com CDN links. That is why this file is gitignored and lives
# only on the machine that generated it.
#
# `images` is inert by default: the shop draws generated line art and emits no
# <img> at all unless you opt in with `apps.onlineshop.product_images=hotlink`,
# which makes every page reference that CDN.

defaults:
  - default

"""
    body = yaml.safe_dump(
        {"products": products}, sort_keys=False, allow_unicode=True, width=100
    )
    # The seeded cart gives the app some initial state without inventing an
    # order history (which would need customer names and addresses).
    cart = yaml.safe_dump(
        {"cart": [{"sku": p["sku"], "options": {}, "quantity": 1}
                  for p in products[:2]]},
        sort_keys=False, allow_unicode=True,
    )
    path.write_text(header + body + "\n" + cart)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--inspect", action="store_true",
                        help="print the dataset's real schema and write nothing")
    parser.add_argument("--file", help=f"item dump to use (default: first of "
                                       f"{', '.join(CANDIDATE_FILES)})")
    parser.add_argument("--limit", type=int, default=200,
                        help="products to keep (default: 200)")
    parser.add_argument("--synth-ratings", action="store_true",
                        help="derive stable ratings from the sku when the "
                             "dataset has none (invented data; off by default)")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    print(f"--> Fetching WebShop data from {HF_REPO}")
    source_file, records = download(args.file, args.inspect)

    if args.inspect:
        inspect(records)
        return 0

    products = convert(records, args.limit, args.synth_ratings)
    if not products:
        raise SystemExit(
            "Converted 0 products -- the field mapping is probably wrong. "
            "Run with --inspect and correct `_FIELDS` in this script."
        )

    leaked = [(p["sku"], url_leaks(p)) for p in products if url_leaks(p)]
    if leaked:
        sku, fields = leaked[0]
        raise SystemExit(
            f"URLs survived conversion into text fields of {len(leaked)} "
            f"products (e.g. {sku}: {', '.join(fields)}). Those render on the "
            f"page under any image mode; only `images` may hold a URL."
        )

    if not any(p["rating"] for p in products):
        print("    note: no ratings in this dataset; pass --synth-ratings to "
              "populate stars")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_pack(products, source_file, args.out)

    hits, fallbacks = glyph_report(products)
    # `--out` may point anywhere, so only shorten the path when it is actually
    # inside the repo.
    try:
        shown = args.out.resolve().relative_to(REPO_ROOT)
    except ValueError:
        shown = args.out
    print(f"\n--> Wrote {len(products)} products to {shown}")
    print(f"    categories: {dict(Counter(p['category'] for p in products))}")
    print(f"    glyphs: {sum(hits.values())} matched a keyword, "
          f"{fallbacks} fell back to the category default")
    if fallbacks > len(products) // 2:
        print("    (most titles missed the keyword table -- widen "
              "`_GLYPH_KEYWORDS` in onlineshop_app/main.py for this dataset)")
    print("\n--> Launch with:  uv run launch.py apps/onlineshop/content=webshop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
