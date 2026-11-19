"""`apps/language=<lang>` switches every app's content pack at once.

Language lives in each app's own `apps/<app>/content` group, so a translated
session used to take one override per app. The `apps/language` options
override all of those groups -- and not `apps/pop_ups`, whose german/mandarin
packs add a pop-up the default session does not have. The test finds
the apps from the packs on disk, so an app that gains a German or Mandarin pack
fails here until it is added to the switch.
"""
from __future__ import annotations

import pytest
from hydra import compose, initialize_config_dir

from open_apps import config_dir

LANGUAGES = ("german", "mandarin")


def _groups_with(language: str) -> set[str]:
    """Every content group that has a ``<language>`` pack."""
    apps = config_dir() / "apps"
    groups = {
        f"apps/{d.parent.parent.name}/content"
        for d in apps.glob(f"*/content/{language}.yaml")
    }
    return groups


def _choices(*overrides: str, config: str = "config") -> dict:
    with initialize_config_dir(version_base=None, config_dir=str(config_dir())):
        cfg = compose(config, overrides=list(overrides), return_hydra_config=True)
    return dict(cfg.hydra.runtime.choices)


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_switch_selects_every_apps_pack(language):
    groups = _groups_with(language)
    assert len(groups) >= 11, groups
    choices = _choices(f"apps/language={language}")
    assert {g: choices.get(g) for g in groups} == {g: language for g in groups}
    assert choices["apps/pop_ups"] == "default"  # no pop-up switched on


def test_a_per_app_override_still_wins():
    choices = _choices("apps/language=german", "apps/todo/content=long_descriptions")
    assert choices["apps/todo/content"] == "long_descriptions"
    assert choices["apps/calendar/content"] == "german"


def test_default_is_the_same_as_no_language():
    groups = _groups_with("german")
    baseline = _choices()
    assert {g: baseline.get(g) for g in groups} == {
        g: _choices("apps/language=default").get(g) for g in groups
    }
    # English packs: `default` everywhere except the shop's `webshop`.
    assert all(baseline[g] in ("default", "webshop") for g in groups)


@pytest.mark.parametrize(
    "config", ["config_parallel_tasks", "config_parallel_tasks_across_goal_variations"]
)
def test_sweeps_take_the_switch_too(config):
    """A sweep composes each app variation against its own primary config, so
    `apps/language` has to be in that defaults list as well."""
    choices = _choices("apps/language=mandarin", config=config)
    groups = _groups_with("mandarin")
    assert {g: choices.get(g) for g in groups} == {g: "mandarin" for g in groups}
