#!/usr/bin/env python3
"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Dev utility: render every product glyph in a content pack to one HTML page.

The shop draws its thumbnails as inline SVG line art keyed on the product
title (`onlineshop_app.main.product_image`). Checking that the shapes are
right otherwise means launching the app and scrolling paginated listings, so
this dumps the whole catalog onto a single contact sheet instead.

    uv run scripts/render_glyph_sheet.py                  # the webshop pack
    uv run scripts/render_glyph_sheet.py fixture          # the test catalog
    uv run scripts/render_glyph_sheet.py --out sheet.html

Products whose title matched no keyword and fell back to their category glyph
are tinted, because that is the number worth looking at: the keyword table was
written against short names and real catalog titles are long retailer strings.
Nothing here is part of the app -- it imports the shop and writes a file.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from hydra import compose, initialize

from open_apps.apps.onlineshop_app import main as shop

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hydra resolves `config_path` relative to this file, so point it at the
# repo's config tree rather than assuming the caller's working directory.
CONFIG_PATH = "../config"


def seed(pack: str):
    """Compose the config for one content pack and seed the shop from it."""
    tmp = tempfile.mkdtemp()
    with initialize(version_base=None, config_path=CONFIG_PATH):
        config = compose(
            config_name="config",
            overrides=[f"logs_dir={tmp}", f"apps/onlineshop/content={pack}"],
        )
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    shop.set_environment(config.apps)


def matched_a_keyword(product) -> bool:
    """Whether the title hit `_GLYPH_KEYWORDS` rather than the category default."""
    title = product.title.lower()
    return any(pattern.search(title) for pattern, _ in shop._GLYPH_PATTERNS)


def render(pack: str, size: int) -> tuple[str, int, int]:
    products = list(shop.products())
    cells, fallbacks = [], 0
    for product in products:
        if matched_a_keyword(product):
            tint = ""
        else:
            tint = "background:#fff3f3;"
            fallbacks += 1
        cells.append(
            f'<figure style="margin:0;text-align:center;width:{size + 20}px;'
            f'{tint}padding:4px;border-radius:8px">'
            f"{shop.product_image(product, size)}"
            f'<figcaption style="font:10px system-ui;line-height:1.25">'
            f"{product.title[:44]}</figcaption></figure>"
        )

    matched = len(products) - fallbacks
    html = (
        '<body style="font-family:system-ui">'
        f"<h3>OpenShop glyphs &mdash; {pack} ({len(products)} products, "
        f"{matched} keyword matches, {fallbacks} category fallbacks, tinted)</h3>"
        '<div style="display:flex;flex-wrap:wrap;gap:10px">'
        + "".join(cells)
        + "</div></body>"
    )
    return html, matched, fallbacks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "pack", nargs="?", default="webshop",
        help="content pack to render (default: webshop, built by "
             "scripts/fetch_webshop.py)",
    )
    parser.add_argument("--out", type=Path, default=Path("/tmp/glyphs.html"))
    parser.add_argument("--size", type=int, default=110, help="glyph px (default: 110)")
    args = parser.parse_args()

    try:
        seed(args.pack)
    except Exception as exc:
        raise SystemExit(
            f"Could not load content pack {args.pack!r}: {exc}\n"
            f"Available: "
            f"{', '.join(sorted(p.stem for p in (REPO_ROOT / 'config/apps/onlineshop/content').glob('*.yaml')))}"
        )

    html, matched, fallbacks = render(args.pack, args.size)
    if not (matched or fallbacks):
        raise SystemExit(
            f"Content pack {args.pack!r} has no products. The shipped `default` "
            f"pack is chrome only -- run `uv run scripts/fetch_webshop.py` to "
            f"build the `webshop` pack first."
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    print(f"--> Wrote {args.out} ({len(html) // 1024} kB, "
          f"{matched + fallbacks} products)")
    print(f"    {matched} matched a glyph keyword, {fallbacks} fell back to "
          f"their category default (tinted)")
    print(f"\n--> open {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
