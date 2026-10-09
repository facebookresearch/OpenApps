"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
"""Whether the online shop is served in this run -- the one place that decides.

The shop is the only app that can be absent. Two independent things switch it
off:

* ``apps.onlineshop.enable=False`` -- hidden on purpose;
* a ``content`` pack with no ``products`` (``apps/onlineshop/content=default``)
  -- nothing to sell, so it is absent rather than an empty storefront.

Either way its routes are not registered, so anything that lists apps (start
page tiles, the desktop dock and pins, screenshot routes) and anything that
runs against the shop (an agent task) has to agree on the answer. They all ask
here instead of re-deriving it, which is how the desktop shell came to pin a
shop the classic grid had already hidden.

Reads the config only. Importing ``open_apps.apps.onlineshop_app`` would build
the shop's FastHTML app as a side effect, which every caller here is trying to
avoid.
"""

from omegaconf import OmegaConf


def onlineshop_has_catalog(apps_cfg) -> bool:
    """Whether the selected ``content`` pack has any products.

    The catalog belongs to the pack, not the app: the default ``webshop`` pack
    has 999 products and the chrome-only ``default`` pack has none.
    """
    shop_cfg = getattr(apps_cfg, "onlineshop", None)
    if shop_cfg is None:
        return False
    return bool(shop_cfg.get("products"))


def onlineshop_unavailable_reason(apps_cfg) -> str | None:
    """Why the shop is not served, in words fit for a log line or an error.

    ``None`` means it is served.
    """
    shop_cfg = getattr(apps_cfg, "onlineshop", None)
    if shop_cfg is None:
        return "there is no `apps.onlineshop` config"
    if not shop_cfg.get("enable", True):
        return "it is disabled (`apps.onlineshop.enable=False`)"
    if not onlineshop_has_catalog(apps_cfg):
        return (
            "the selected `content` pack has no products "
            "(the default `apps/onlineshop/content=webshop` has 999)"
        )
    return None


def onlineshop_is_served(apps_cfg) -> bool:
    """Whether the shop's routes are registered: switched on and has a catalog."""
    return onlineshop_unavailable_reason(apps_cfg) is None


def task_needs_onlineshop(task_cfg) -> bool:
    """Whether a task config refers to the shop anywhere.

    Matches the app key (``target_app: onlineshop``, a ``/onlineshop`` URL) and
    the state key rewards read (``online_shop``). Deliberately broad: a false
    positive costs one clear error at launch, a false negative costs a whole
    agent run spent looking for an app that is not there.
    """
    if task_cfg is None:
        return False
    container = (
        OmegaConf.to_container(task_cfg, resolve=False)
        if OmegaConf.is_config(task_cfg)
        else task_cfg
    )
    text = repr(container)
    return "onlineshop" in text or "online_shop" in text
