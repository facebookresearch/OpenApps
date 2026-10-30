"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Send money in a real browser, scored by the real task.

`test_openbanking_send.py` covers each route; this covers what joins them,
which is the path an agent takes: the link on the account page, the htmx
recipient search and its click-to-fill, the GET review, the Save to
Contacts fields, the confirm POST and its redirect -- then the bank and
Contacts state the reward reads. A break anywhere between the click and
`/openbanking_all` zeroes every payment task while each page still renders.
"""

import pytest

from open_apps.state import get_current_state
from open_apps.tasks.tasks import SendMoneyTask

from tests.test_todo_toggle import open_any_browser


@pytest.mark.slow
def test_paying_someone_new_and_saving_them(tmp_path_factory):
    playwright_api = pytest.importorskip("playwright.sync_api")

    from tests.save_screenshots import launch_variation, stop_server

    runtime_dir = tmp_path_factory.mktemp("send_money_browser")
    launcher, process = launch_variation("send_money", runtime_dir, overrides=[])
    try:
        base_url = launcher.web_app_url
        initial = get_current_state(base_url)
        task = SendMoneyTask(
            goal="Pay Sam Ortiz $60 and save them to my contacts.",
            amount=60,
            from_account_last4="4417",
            to="sam.ortiz@example.com",
            save_contact={"first_name": "Sam", "last_name": "Ortiz"},
        )
        assert not task.check_if_task_is_complete(initial, get_current_state(base_url))

        with playwright_api.sync_playwright() as p:
            browser, page = open_any_browser(p, playwright_api, viewport={"width": 1280, "height": 800})
            try:
                page.goto(f"{base_url}/openbanking/accounts/3")
                page.click("#ob-send-link")
                page.wait_for_selector("#ob-send-form")

                # The search fills the field from a contact...
                page.fill("#ob-send-to", "Bob")
                page.click(".ob-send-match[data-handle='bob@example.com']")
                assert page.input_value("#ob-send-to") == "bob@example.com"

                # ...and says what to do when nobody matches.
                page.fill("#ob-send-to", "Sam")
                page.wait_for_selector(".ob-send-none")

                page.fill("#ob-send-to", "sam.ortiz@example.com")
                page.fill("#ob-send-amount", "60")
                page.fill("#ob-send-memo", "harness")
                page.click("#ob-send-form button[type=submit]")
                page.wait_for_selector("#ob-send-review")

                page.check("#ob-send-save")
                page.fill("#ob-send-first_name", "Sam")
                page.fill("#ob-send-last_name", "Ortiz")
                page.click("#ob-send-review button[type=submit]")
                toast = page.wait_for_selector("#ob-sent-toast")
                assert "Sent $60.00 to Sam Ortiz" in toast.inner_text()
            finally:
                browser.close()

        assert task.check_if_task_is_complete(initial, get_current_state(base_url))
    finally:
        stop_server(launcher, process)
