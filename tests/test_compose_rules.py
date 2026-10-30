"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for the composer's constraint rules (``open_apps.compose.rules``).

Two kinds of check. The fixture cases (shared with the JS port) pin what the
evaluator reports for a selection. The premise checks hold each rule to the
behaviour it claims -- a rule that warns about something the code no longer
does is worse than no rule.
"""

import json
from pathlib import Path

import pytest
from hydra import compose

from open_apps.apps.availability import onlineshop_unavailable_reason
from open_apps.compose.catalog import WATCHED_PATHS, build_catalog, hydra_context
from open_apps.compose.command import Selection
from open_apps.compose.rules import (
    FACTS,
    apply_fix,
    check_option,
    evaluate,
    facts,
    load_rules,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "compose" / "rules_cases.json").read_text()
)
CASES = FIXTURES["cases"]
RULES = load_rules()


@pytest.fixture(scope="module")
def catalog():
    return build_catalog()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_evaluate(catalog, case):
    findings = evaluate(Selection.from_dict(case["selection"]), catalog)
    assert [f.rule_id for f in findings] == case["findings"]


def test_plus_extras_set_facts_and_deletions_do_not(catalog):
    known = facts(
        Selection(extra=["++a.b=false", "+a.c=3", "~a.d=1", "+++a.e=1"]), catalog
    )
    assert known["a.b"] == [False]
    assert known["a.c"] == [3]
    assert "a.d" not in known and "~a.d" not in known
    assert "a.e" not in known and "+a.e" not in known  # not Hydra grammar


# --------------------------------------------------------------------------
# The rules file itself
# --------------------------------------------------------------------------


def test_rule_ids_are_unique():
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("rule", RULES, ids=[r.id for r in RULES])
def test_rule_keys_are_known(catalog, rule):
    """A typo'd key would silently never match (or always match `null`)."""
    known = set(catalog.groups) | set(catalog.scalars) | set(WATCHED_PATHS) | FACTS
    # config/benchmark/ arrives with the leaderboard branch; until then the
    # rule never fires, and the temp-tree tests below exercise it.
    known.add("benchmark")
    for key in [*rule.when, *(rule.fix or {})]:
        assert key in known or key.startswith("apps."), key


@pytest.mark.parametrize("rule", RULES, ids=[r.id for r in RULES])
def test_rule_group_values_name_real_options(catalog, rule):
    for key, value in [*rule.when.items(), *(rule.fix or {}).items()]:
        if key in catalog.groups:
            names = {o.name for o in catalog.groups[key].options}
            for v in value if isinstance(value, list) else [value]:
                assert v in names, f"{key}={v}"


def test_rules_ship_in_the_catalog(catalog):
    assert [r["id"] for r in catalog.rules] == [r.id for r in RULES]


def test_bad_action_is_rejected(tmp_path):
    bad = tmp_path / "rules.yaml"
    bad.write_text("- {id: x, when: {seed: 1}, then: explode, reason: r}\n")
    with pytest.raises(ValueError, match="explode"):
        load_rules(bad)


# --------------------------------------------------------------------------
# Fixes and option checks
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_every_fix_clears_its_finding(catalog, case):
    selection = Selection.from_dict(case["selection"])
    for finding in evaluate(selection, catalog):
        if finding.fix:
            fixed = apply_fix(selection, finding.fix, catalog)
            assert finding.rule_id not in [f.rule_id for f in evaluate(fixed, catalog)]


def test_fix_on_an_untracked_path_becomes_an_extra(catalog):
    sel = Selection.from_dict(
        {
            "choices": {"task_name": ["navigate_to_onlineshop"]},
            "extra": ["apps.onlineshop.enable=False"],
        }
    )
    fixed = apply_fix(sel, {"apps.onlineshop.enable": True}, catalog)
    assert fixed.extra == ["apps.onlineshop.enable=True"]


def test_check_option_reports_what_a_pill_would_trigger(catalog):
    """What the UI asks before enabling a pill: what would this choice cause?"""
    sel = Selection.from_dict({"choices": {"task_name": ["navigate_to_onlineshop"]}})
    found = check_option(sel, "apps/onlineshop/content", "default", catalog)
    assert [f.rule_id for f in found] == ["shop_task_without_catalog"]
    assert check_option(sel, "apps/onlineshop/content", "german", catalog) == []


def test_facts_overlay_order(catalog):
    """Defaults < group options (experiment last) < explicit scalars < extras."""
    base = {"agent": ["dummy"], "tasks": ["longer_horizon"]}
    f = facts(Selection.from_dict({"choices": base}), catalog)
    assert f["browsergym_env_args.max_steps"] == [30]
    f = facts(
        Selection.from_dict({"choices": {**base, "experiment": ["screenshot_agent"]}}),
        catalog,
    )
    assert f["browsergym_env_args.max_steps"] == [25]
    f = facts(
        Selection.from_dict(
            {
                "choices": {**base, "browsergym_env_args.max_steps": [12]},
                "extra": ["browsergym_env_args.max_steps=60"],
            }
        ),
        catalog,
    )
    assert f["browsergym_env_args.max_steps"] == [60]
    assert f["target"] == ["single"]


def test_slurm_pool_turns_wandb_on(catalog):
    f = facts(Selection.from_dict({"choices": {"mode": ["slurm_cluster"]}}), catalog)
    assert f["use_wandb"] == [True]


# --------------------------------------------------------------------------
# Premises: each rule's claim about the code still holds
# --------------------------------------------------------------------------


def test_premise_chrome_only_shop_pack_is_not_served():
    with hydra_context():
        cfg = compose(
            config_name="config", overrides=["apps/onlineshop/content=default"]
        )
        assert onlineshop_unavailable_reason(cfg.apps)
        cfg = compose(
            config_name="config",
            overrides=[
                "apps/onlineshop/content=default",
                "apps.onlineshop.enable=False",
            ],
        )
        assert "disabled" in onlineshop_unavailable_reason(cfg.apps)


def test_premise_gpt55_configs_read_the_key_from_the_environment():
    rule = next(r for r in RULES if r.id == "gpt55_agent_needs_key")
    root = Path(__file__).parents[1] / "config" / "agent"
    for name in rule.when["agent"]:
        assert "${oc.env:GPT55_API_KEY}" in (root / f"{name}.yaml").read_text()
    # ... and no other agent config does, or the rule's list is stale.
    others = {
        p.stem
        for p in root.glob("*.yaml")
        if "GPT55_API_KEY" in p.read_text() and p.stem not in rule.when["agent"]
    }
    assert not others


def test_premise_vllm_url_uses_the_hostname(monkeypatch):
    from open_apps.agent import vLLM_agent

    # Capture the client arguments instead of building a client.
    monkeypatch.setattr(vLLM_agent, "VLLMChatModel", lambda **kwargs: kwargs)
    args = vLLM_agent.ModelArgs(client_type="vllm", hostname="node-9", port="8000")
    assert args.make_model()["client_args"]["base_url"] == "http://node-9:8000/v1"


# --------------------------------------------------------------------------
# A frozen suite pins the sweep axes (the leaderboard's `benchmark` group)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bench(benchmark_config_dir):
    return build_catalog(benchmark_config_dir)


SUITE = {"benchmark": ["align_v1"], "agent": ["claude_4_sonnet"]}


def test_a_benchmark_alone_trips_nothing(bench):
    assert evaluate(Selection(choices=SUITE), bench) == []


@pytest.mark.parametrize(
    "key,value", [("apps/theme", "dark"), ("task_name", "navigate_to_todo")]
)
def test_a_benchmark_disables_the_sweep_axes(bench, key, value):
    sel = Selection(choices={**SUITE, key: [value]})
    findings = evaluate(sel, bench)
    assert [f.rule_id for f in findings] == ["benchmark_pins_sweep_axes"]
    assert findings[0].then == "disable"
    assert "frozen suite pins these" in findings[0].reason
    # The pill that would complete it is disabled.
    offered = check_option(Selection(choices=SUITE), key, value, bench)
    assert [f.then for f in offered] == ["disable"]


def test_a_sweep_axis_without_a_benchmark_is_fine(bench):
    sel = Selection(choices={"apps/theme": ["dark", "mono"]})
    assert evaluate(sel, bench) == []
    assert facts(sel, bench)["sweep_axes"] == ["apps/theme"]
