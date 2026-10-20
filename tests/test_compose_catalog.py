"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for the config composer's catalog (``open_apps.compose.catalog``).

The catalog is what the composer UI renders pills from, so the property that
matters is that it can never drift from ``config/``: every group and option on
disk is in it, its defaults are the ones Hydra actually composes, and every
option has a description to show. The filesystem walk here is deliberately an
independent check on the Hydra-loader walk inside the catalog.

No browser, no web app: the catalog is built once per module from the real
config tree (a few seconds of Hydra composition).
"""

import json
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.compose import catalog as catalog_mod
from open_apps.compose.catalog import (
    RULES_FILE,
    build_catalog,
    humanize,
    parse_header,
)

CONFIG = config_dir()


@pytest.fixture(scope="module")
def catalog():
    return build_catalog()


def _yaml_files() -> list[Path]:
    return sorted(CONFIG.rglob("*.yaml"))


def _group_dirs_on_disk() -> dict[str, set[str]]:
    """{group path: option stems} for every directory below config/ holding YAML."""
    groups: dict[str, set[str]] = {}
    for path in _yaml_files():
        rel = path.parent.relative_to(CONFIG)
        if rel == Path("."):
            continue  # primary configs and the rules file are not group options
        groups.setdefault(rel.as_posix(), set()).add(path.stem)
    return groups


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_every_group_on_disk_is_discovered(catalog):
    assert set(catalog.groups) == set(_group_dirs_on_disk())


def test_every_option_on_disk_is_discovered(catalog):
    for path, stems in _group_dirs_on_disk().items():
        assert {o.name for o in catalog.groups[path].options} == stems, path


def test_hydra_internal_groups_are_excluded(catalog):
    assert not any(p == "hydra" or p.startswith("hydra/") for p in catalog.groups)


def test_rules_file_is_not_a_group_option(catalog):
    """config/compose_rules.yaml sits at the root, where Hydra lists primary
    configs, never group options -- so no `x=compose_rules` override exists."""
    assert RULES_FILE.parent == CONFIG
    assert RULES_FILE.exists()
    for group in catalog.groups.values():
        assert RULES_FILE.stem not in {o.name for o in group.options}


def test_nested_group_package_comes_from_the_header(catalog):
    """`# @package apps.todo` is what makes `apps/todo/layout=kanban_board`
    land under apps.todo; the catalog reads it through Hydra, not by regex."""
    assert catalog.groups["apps/todo/layout"].package == "apps.todo"
    assert catalog.groups["experiment"].package == "_global_"


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def _top_level_defaults(config_name: str) -> dict[str, str | None]:
    raw = OmegaConf.to_container(OmegaConf.load(CONFIG / f"{config_name}.yaml"))
    out: dict[str, str | None] = {}
    for entry in raw["defaults"]:
        if isinstance(entry, dict):
            ((key, value),) = entry.items()
            out[key] = value
    return out


@pytest.mark.parametrize("config_name", ["config", "config_parallel_tasks"])
def test_top_level_defaults_match_the_primary_config(catalog, config_name):
    for key, value in _top_level_defaults(config_name).items():
        assert catalog.groups[key].defaults[config_name] == value, key


def test_nested_defaults_come_from_the_composed_defaults_tree(catalog):
    # apps/todo/default.yaml selects `layout: default`; onlineshop picks the
    # `webshop` content pack -- neither is visible in config.yaml itself.
    assert catalog.groups["apps/todo/layout"].defaults["config"] == "default"
    assert catalog.groups["apps/onlineshop/content"].defaults["config"] == "webshop"


def test_null_default_group_is_nullable_but_not_append(catalog):
    theme = catalog.groups["apps/theme"]
    assert theme.defaults["config"] is None
    assert theme.nullable
    assert not theme.append  # `apps/theme=dark` binds without a `+`


def test_group_absent_from_defaults_needs_append(catalog):
    experiment = catalog.groups["experiment"]
    assert experiment.append  # rendered as `+experiment=phone`
    assert experiment.nullable
    assert experiment.defaults == {"config": None, "config_parallel_tasks": None}


def test_a_nullable_group_names_its_unset_option(catalog):
    """The unset choice says what it means rather than "None": no shared
    theme means each app keeps its own look."""
    theme = catalog.groups["apps/theme"]
    assert theme.null_label == "App defaults"
    assert "own" in theme.null_summary
    assert catalog.groups["experiment"].null_label == "No experiment"
    for group in catalog.groups.values():
        assert (group.null_label is not None) == group.nullable, group.path
        assert group.null_label != "None"


def test_only_apps_groups_are_sweepable(catalog):
    """create_configs copies only `apps` out of each variation's composition,
    so a device or agent in app_variations would silently do nothing."""
    for path, group in catalog.groups.items():
        assert group.sweepable == path.startswith("apps/"), path


# --------------------------------------------------------------------------
# Descriptions, labels, controls
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", _yaml_files(), ids=lambda p: p.relative_to(CONFIG).as_posix()
)
def test_every_config_yaml_has_a_leading_comment(path):
    header = parse_header(path.read_text("utf-8"))
    assert header.summary, f"{path} has no leading comment block"


def test_every_option_has_summary_and_label(catalog):
    for group in catalog.groups.values():
        for option in group.options:
            assert option.summary and option.label, f"{group.path}={option.name}"


def test_parse_header_skips_package_line_and_defaults_block():
    text = (
        "# @package _global_\n"
        "defaults:\n"
        "  - default\n"
        "  # a defaults-list comment, not the header\n"
        "  - _self_\n"
        "\n"
        "# First sentence here. Second sentence.\n"
        "#\n"
        "# More detail.\n"
        "key: 1\n"
    )
    header = parse_header(text)
    assert header.summary == "First sentence here."
    assert header.details == "First sentence here. Second sentence.\n\nMore detail."


def test_parse_header_prefers_a_block_above_defaults():
    text = "# Top block.\ndefaults:\n  - default\n\n# Field comment.\nkey: 1\n"
    assert parse_header(text).summary == "Top block."


def test_parse_header_extracts_and_strips_label():
    text = "# label: Dummy / smoke test\n# Clicks at random. For debugging.\nk: 1\n"
    header = parse_header(text)
    assert header.label == "Dummy / smoke test"
    assert header.summary == "Clicks at random."
    assert "label:" not in header.details


def test_parse_header_does_not_split_on_abbreviations():
    text = "# Use a laptop, e.g. 1280x800, for this. Then more.\nk: 1\n"
    assert parse_header(text).summary == "Use a laptop, e.g. 1280x800, for this."


def test_parse_header_without_comment_is_empty():
    assert parse_header("key: 1\n").summary == ""


@pytest.mark.parametrize(
    "stem,label",
    [
        ("axtree-only", "AXTree only"),
        ("kanban_board", "Kanban board"),
        ("GPT-5.5-text-only", "GPT 5.5 text only"),
        ("vscode_dark", "Vscode dark"),
        ("no_visual", "No visual"),
        ("sidebar_nav", "Sidebar nav"),
        ("ui_library", "UI library"),
        ("openbanking", "OpenBanking"),
        ("Qwen3.6-VL-computer-use", "Qwen3.6 VL computer use"),
    ],
)
def test_humanize(stem, label):
    assert humanize(stem) == label


@pytest.mark.parametrize(
    "name,label",
    [
        ("dummy", "Dummy / smoke test"),
        ("GPT-5.5-computer-use", "GPT-5.5 computer use"),
        ("axtree-only", "AXTree only"),
        ("screenshot-only", "Screenshot only"),
    ],
)
def test_agent_labels_come_from_the_yaml(catalog, name, label):
    option = catalog.groups["agent"].option(name)
    assert option.label == label


def test_every_agent_config_declares_a_label():
    for path in sorted((CONFIG / "agent").glob("*.yaml")):
        assert parse_header(path.read_text("utf-8")).label, path


def test_control_hint_follows_option_count(catalog):
    for group in catalog.groups.values():
        n = len(group.options)
        assert group.control == ("pills" if n <= 5 else "select"), group.path
        assert group.searchable == (n > 12), group.path


# --------------------------------------------------------------------------
# Free-form scalars and tasks
# --------------------------------------------------------------------------


def test_typed_scalars_come_from_the_composed_default(catalog):
    s = catalog.scalars
    assert (s["seed"].type, s["seed"].default) == ("int", 233)
    assert (s["use_wandb"].type, s["use_wandb"].default) == ("bool", False)
    assert (s["record_video"].type, s["record_video"].default) == ("bool", False)
    assert s["browsergym_env_args.max_steps"].type == "int"
    assert s["browsergym_env_args.max_steps"].default == 30
    assert s["browsergym_env_args.headless"].default is True


def test_task_name_is_an_enum_over_the_selected_tasks_group(catalog):
    task_name = catalog.scalars["task_name"]
    assert task_name.type == "enum"
    assert task_name.default == "add_meeting_with_dennis"
    assert task_name.control == "select" and task_name.searchable
    names = {t.name for t in catalog.tasks["all_tasks"]}
    assert "add_meeting_with_dennis" in names
    assert "add_meeting_with_dennis__casual_1" in names
    lh = {t.name for t in catalog.tasks["longer_horizon"]}
    assert lh and lh.isdisjoint({"add_meeting_with_dennis"})


def test_every_tasks_option_has_a_task_list(catalog):
    assert set(catalog.tasks) == {o.name for o in catalog.groups["tasks"].options}


def test_the_paper_task_set_says_what_it_holds(catalog):
    """all_tasks is the paper's inventory, not every task: a user who picked
    "All tasks" expected OpenBanking in it. The label and summary say what it
    is, and that the other sets are separate groups to combine with it."""
    option = catalog.groups["tasks"].option("all_tasks")
    assert option.label == "Paper tasks (original + goal variations)"
    for word in ("OpenBanking", "longer-horizon", "combine"):
        assert word in option.summary, word


def test_no_label_spells_openbanking_as_one_lowercase_word(catalog):
    assert catalog.groups["tasks"].option("openbanking").label == "OpenBanking"
    labels = [o.label for g in catalog.groups.values() for o in g.options]
    labels += [g.label for g in catalog.groups.values()]
    assert not [label for label in labels if "Openbanking" in label]


def test_task_label_is_the_goals_first_sentence(catalog):
    task = catalog.task("all_tasks", "add_call_mom_to_my_todo")
    assert task.label == "Add 'Call Mom' to my todo list."
    assert task.goal.startswith("Add 'Call Mom'")


@pytest.mark.parametrize(
    "tasks,name,apps",
    [
        ("all_tasks", "add_meeting_with_dennis", ["calendar"]),
        ("all_tasks", "message_bob_to_meet", ["messenger"]),
        ("all_tasks", "save_paris_to_my_favorite_places", ["maps"]),
        ("all_tasks", "navigate_to_onlineshop", ["onlineshop"]),
        ("all_tasks", "navigate_from_todo_to_map", ["maps", "todo"]),
        ("longer_horizon", "plan_elena_birthday_todo_message", ["calendar", "todo"]),
    ],
)
def test_task_apps_are_derived_from_task_classes(catalog, tasks, name, apps):
    assert catalog.task(tasks, name).apps == apps


def test_task_apps_agree_with_the_launch_time_shop_check(catalog):
    """`task_app: onlineshop` must fire exactly when launcher.check_task_can_run
    would refuse the run, so the composer warns about what launch enforces."""
    from open_apps.apps.availability import task_needs_onlineshop

    for tasks_name, entries in catalog.tasks.items():
        raw = catalog_mod.load_tasks_config(tasks_name)
        for entry in entries:
            expected = task_needs_onlineshop(raw[entry.name])
            assert ("onlineshop" in entry.apps) == expected, entry.name


def test_option_sets_record_watched_values(catalog):
    """What a group choice does to the scalars rules look at."""
    assert catalog.groups["browsergym_env_args"].option("screenshot").sets == {
        "browsergym_env_args.max_steps": 25
    }
    assert (
        catalog.groups["experiment"]
        .option("phone")
        .sets["browsergym_env_args.max_steps"]
        == 25
    )
    gemma = catalog.groups["agent"].option("gemma-4-2B-computer-use")
    assert gemma.sets["agent.client_type"] == "vllm"
    # Same as the baseline (config.yaml's agent=default), so not recorded.
    assert "agent.hostname" not in gemma.sets
    assert catalog.baseline["agent.hostname"] is None
    assert catalog.groups["agent"].option("dummy").sets["agent.client_type"] == "dummy"


# --------------------------------------------------------------------------
# Targets, capabilities, JSON
# --------------------------------------------------------------------------


def test_targets(catalog):
    assert set(catalog.targets) == {"single", "sweep", "pool", "slurm_pool"}
    # The SLURM pool is the Worker pool pill with Mode=slurm_cluster, not a
    # pill of its own.
    assert [t.name for t in catalog.targets.values() if t.pill] == [
        "single",
        "sweep",
        "pool",
    ]
    assert catalog.targets["sweep"].config_name == "config_parallel_tasks"
    assert catalog.targets["single"].config_name == "config"


def test_capabilities_default_to_static(catalog):
    assert catalog.capabilities == "static"


def test_to_json_round_trips(catalog):
    data = json.loads(catalog.to_json())
    assert data["capabilities"] == "static"
    assert data["groups"]["agent"]["options"][0]["label"]
    assert data["scalars"]["seed"]["default"] == 233
    assert data["rules"], "rules ship with the catalog for the JS evaluator"


def test_cli_writes_catalog(tmp_path):
    out = tmp_path / "catalog.json"
    catalog_mod.main(["--out", str(out)])
    assert json.loads(out.read_text())["groups"]


# --------------------------------------------------------------------------
# A group only another primary config names (the leaderboard's `benchmark`)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bench(benchmark_config_dir):
    return build_catalog(benchmark_config_dir)


def test_a_group_only_the_sweep_config_names_builds(bench):
    """config/benchmark/ is a _global_ group that only config_parallel_tasks'
    defaults list names; composing its options against config.yaml crashed
    the build ("Could not override 'benchmark'")."""
    group = bench.groups["benchmark"]
    assert [o.name for o in group.options] == ["align_v1", "heldout_seeds.example"]
    assert group.package == "_global_"
    assert group.defaults == {"config": None, "config_parallel_tasks": None}
    assert not group.append  # `benchmark=align_v1`, no `+`: it is in a defaults list
    assert not group.sweepable
    assert group.targets == ["sweep"]
    assert group.pins_sweep
    # Composed against config_parallel_tasks, where the override binds.
    option = group.option("align_v1")
    assert option.label == "Align v1"
    assert option.summary == "OpenApps-Align v1 -- the frozen leaderboard suite."
    assert option.sets == {"browsergym_env_args.max_steps": 45}


def test_groups_config_yaml_names_keep_every_target(bench, catalog):
    for path, group in catalog.groups.items():
        assert group.targets == list(catalog.targets), path
        assert not group.pins_sweep, path
        assert bench.groups[path].targets == group.targets, path


def test_a_sweep_only_group_leads_the_order(bench):
    assert next(iter(bench.groups)) == "benchmark"
