"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Recurring events in the month grid and the agenda.

A series starts on its own date, so no month before it shows an instance,
and the month it starts in shows every instance from that date on. Both
views are asked for the same months and have to agree. The dates are fixed
and passed in the URL, so nothing here depends on today.
"""

from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.calendar_app import main as cal


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    saved = {
        name: getattr(cal, name)
        for name in ("events", "logo_title_container")
        if hasattr(cal, name)
    }
    saved_config = getattr(cal.app, "config", None)
    logs_dir = tmp_path_factory.mktemp("cal-recurring")
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    cal.set_environment(config.apps)
    test_client = TestClient(cal.app)
    # Tue 2027-03-16 weekly: Mar 16, 23, 30; Apr 6, 13, 20, 27.
    for title, date, recurring in (
        ("Weekly standup", "2027-03-16", "weekly"),
        ("Monthly rent", "2027-03-05", "monthly"),
        ("Yearly review", "2028-06-10", "yearly"),
        ("One-off lunch", "2027-03-16", "none"),
    ):
        response = test_client.post(
            "/calendar/create_event/save_text",
            data={"title": title, "date": date, "recurring": recurring},
            follow_redirects=False,
        )
        assert response.status_code == 303
    yield test_client
    for name, value in saved.items():
        setattr(cal, name, value)
    if saved_config is not None:
        cal.app.config = saved_config


def shown(client, title, year, month, view):
    html = client.get(f"/calendar/calendar_content/{year}/{month}?view={view}").text
    return html.count(f">{title}<")


@pytest.mark.parametrize("view", ["calendar", "agenda"])
@pytest.mark.parametrize(
    "title, year, month, expected",
    [
        ("Weekly standup", 2027, 2, 0),
        ("Weekly standup", 2027, 3, 3),
        ("Weekly standup", 2027, 4, 4),
        ("Monthly rent", 2027, 2, 0),
        ("Monthly rent", 2027, 3, 1),
        ("Monthly rent", 2027, 4, 1),
        ("Yearly review", 2027, 6, 0),
        ("Yearly review", 2028, 6, 1),
        ("Yearly review", 2029, 6, 1),
        ("One-off lunch", 2027, 3, 1),
        ("One-off lunch", 2027, 4, 0),
    ],
)
def test_recurring_instances_start_on_the_event_date(client, view, title, year, month, expected):
    assert shown(client, title, year, month, view) == expected
