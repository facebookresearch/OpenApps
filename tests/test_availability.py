"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
"""The online-shop gate, and the surfaces that have to agree with it.

`open_apps.apps.availability` is the single answer to "is the shop served in
this run?". These tests pin that answer for each way of switching the shop
off, and check that the launcher refuses a shop task up front rather than
letting an agent spend a run looking for an app that is not there.
"""
from types import SimpleNamespace

import pytest
from hydra import compose, initialize

from open_apps.apps.availability import (
    onlineshop_has_catalog,
    onlineshop_is_served,
    onlineshop_unavailable_reason,
    require_files,
    task_needs_onlineshop,
)
from open_apps.launcher import AgentLauncher

ENABLED_OFF = "apps.onlineshop.enable=False"
NO_CATALOG = "apps/onlineshop/content=default"


def compose_config(*overrides, task_name="add_meeting_with_dennis"):
    with initialize(version_base=None, config_path="../config/"):
        return compose(
            config_name="config",
            overrides=[
                "logs_dir=/tmp/openapps-availability",
                f"task_name={task_name}",
                *overrides,
            ],
        )


class TestTheGate:
    def test_the_shipped_default_serves_the_shop(self):
        apps = compose_config().apps
        assert onlineshop_is_served(apps)
        assert onlineshop_unavailable_reason(apps) is None

    @pytest.mark.parametrize(
        "override, phrase",
        [(ENABLED_OFF, "disabled"), (NO_CATALOG, "no products")],
    )
    def test_each_switch_turns_it_off_and_says_which(self, override, phrase):
        apps = compose_config(override).apps
        assert not onlineshop_is_served(apps)
        assert phrase in onlineshop_unavailable_reason(apps)

    def test_disabled_wins_over_having_a_catalog(self):
        """The two switches are independent; either one is enough."""
        apps = compose_config(ENABLED_OFF).apps
        assert onlineshop_has_catalog(apps)
        assert not onlineshop_is_served(apps)

    def test_a_config_without_the_shop_does_not_raise(self):
        assert not onlineshop_is_served(SimpleNamespace())


class TestTasksThatNeedTheShop:
    def test_a_shop_task_is_recognised(self):
        config = compose_config()
        assert task_needs_onlineshop(config.tasks["navigate_to_onlineshop"])

    def test_an_unrelated_task_is_not(self):
        config = compose_config()
        assert not task_needs_onlineshop(config.tasks["add_meeting_with_dennis"])

    def test_no_task_needs_nothing(self):
        assert not task_needs_onlineshop(None)


class TestTheLauncherRefusesUpFront:
    def check(self, *overrides, task_name):
        config = compose_config(*overrides, task_name=task_name)
        # Only `self.config` is read, so no browser or web app is started.
        AgentLauncher.check_task_can_run(SimpleNamespace(config=config))

    @pytest.mark.parametrize("override", [ENABLED_OFF, NO_CATALOG])
    def test_a_shop_task_without_a_shop_fails_with_the_reason(self, override):
        with pytest.raises(ValueError, match="does not serve it"):
            self.check(override, task_name="navigate_to_onlineshop")

    def test_a_shop_task_with_a_shop_is_fine(self):
        self.check(task_name="navigate_to_onlineshop")

    def test_other_tasks_do_not_care_whether_the_shop_is_on(self):
        self.check(ENABLED_OFF, task_name="add_meeting_with_dennis")


class TestFilesIsAlwaysServed:
    """Files can't be switched off: the bank and shop write into it, the code
    editor works in it, and purchase tasks score its receipts."""

    def test_the_shipped_default_has_it(self):
        require_files(compose_config().apps)

    def test_removing_it_is_refused_with_the_reason(self):
        with pytest.raises(ValueError, match="no Files app"):
            require_files(compose_config("~apps/files").apps)

    def test_the_launcher_refuses_it_before_any_task_check(self):
        config = compose_config("~apps/files", task_name="add_meeting_with_dennis")
        with pytest.raises(ValueError, match="no Files app"):
            AgentLauncher.check_task_can_run(SimpleNamespace(config=config))

    def test_setting_up_the_apps_refuses_it(self):
        from open_apps.apps.start_page.main import initialize_routes_and_configure_task

        with pytest.raises(ValueError, match="no Files app"):
            initialize_routes_and_configure_task(compose_config("~apps/files").apps)
