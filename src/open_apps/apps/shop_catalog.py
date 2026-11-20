"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
"""The online shop's products, from the config or the catalog file it names.

A content pack gives its products one of two ways:

* inline, as ``products:`` -- small catalogs (``content/fixture.yaml``, the
  empty ``default`` pack, a one-off override);
* by name, as ``catalog: webshop`` -- ``config/apps/onlineshop/catalogs/
  webshop.json``. A path to a ``.json`` file works too.

An inline list that is not empty wins. The file exists because Hydra parses
every YAML file in the defaults list on every composition, in pure Python:
with the 999-product WebShop catalog inline, each composition of
``config.yaml`` took ~3 s, paid by every launch, every sweep child and every
test that composes the config. JSON is invisible to Hydra and parses in
milliseconds, so the cost moves to the one place that needs the products --
the shop seeding its table -- and is paid once per process.

Imports nothing from the shop app, for the same reason as
:mod:`open_apps.apps.availability`: importing the app builds it.
"""

import copy
import json
from functools import lru_cache
from pathlib import Path

from omegaconf import OmegaConf

from open_apps import config_dir


def catalog_path(name: str) -> Path:
    """Where ``catalog: <name>`` points: a bare name, or a path to a ``.json``."""
    if name.endswith(".json") or "/" in name:
        return Path(name).expanduser()
    return config_dir() / "apps" / "onlineshop" / "catalogs" / f"{name}.json"


@lru_cache(maxsize=None)
def _load(path: str, mtime_ns: int) -> tuple[dict, ...]:
    # Keyed on the mtime as well, so a regenerated catalog is picked up by a
    # long-lived process (a test session) rather than served stale.
    products = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(products, list):
        raise ValueError(f"Shop catalog {path} is not a JSON list of products")
    return tuple(products)


def _products(shop_cfg):
    """The pack's products, uncopied: an inline list, or the cached file's."""
    if shop_cfg is None:
        return ()
    inline = shop_cfg.get("products")
    if inline:
        return inline
    name = shop_cfg.get("catalog")
    if not name:
        return ()
    path = catalog_path(str(name))
    if not path.is_file():
        raise FileNotFoundError(
            f"apps.onlineshop.catalog={name!r} names {path}, which does not "
            "exist. Pick a file under config/apps/onlineshop/catalogs/, or "
            "rebuild the WebShop one with `uv run scripts/fetch_webshop.py`."
        )
    return _load(str(path.resolve()), path.stat().st_mtime_ns)


def has_products(shop_cfg) -> bool:
    """Whether the pack has anything to sell, without copying the catalog."""
    return bool(_products(shop_cfg))


def shop_products(shop_cfg) -> list[dict]:
    """The selected pack's products as plain dicts (copies; safe to mutate)."""
    products = _products(shop_cfg)
    if OmegaConf.is_config(products):
        return OmegaConf.to_container(products, resolve=True)
    return copy.deepcopy(list(products))
