"""The agent's goto must not reach the `/<app>_all` state endpoints rewards read."""

import pytest
from browsergym.core.action.base import execute_python_code

from open_apps.agent.utils import CustomActionSetArgs


class _FakePage:
    def __init__(self):
        self.visited = []

    def goto(self, url):
        self.visited.append(url)


def _run(action):
    page = _FakePage()
    code = CustomActionSetArgs(subsets=("custom",)).make_action_set().to_python_code(action)
    execute_python_code(code, page, lambda _: None, lambda _: None)
    return page.visited


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5001/todo_all",
        "/calendar_all",
        "/messages_all/",
        "/onlineshop_all?include_catalog=true",
        "http://localhost:5001/openbanking_all#x",
        "/desktop%5Fall",
    ],
)
def test_goto_refuses_state_endpoints(url):
    with pytest.raises(ValueError, match="not allowed"):
        _run(f"goto({url!r})")


@pytest.mark.parametrize("url", ["http://localhost:5001/todo", "/calendar?view=all", "/"])
def test_goto_allows_app_pages(url):
    assert _run(f"goto({url!r})") == [url]
