"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

import pytest
import requests

from open_apps import state


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return {"ok": True}


@pytest.fixture
def calls(monkeypatch):
    """Count GETs and skip the backoff sleeps."""
    seen = []
    monkeypatch.setattr(state.time, "sleep", lambda _: None)
    return seen


def _patch_get(monkeypatch, calls, status_code):
    def fake_get(url, timeout):
        calls.append(url)
        return _FakeResponse(status_code)

    monkeypatch.setattr(state.requests, "get", fake_get)


def test_4xx_is_not_retried(monkeypatch, calls):
    """A disabled app's route 404s; retrying it only adds backoff."""
    _patch_get(monkeypatch, calls, 404)
    assert state.safe_get_json("http://x/onlineshop_all", retries=3) == []
    assert len(calls) == 1


def test_5xx_is_retried(monkeypatch, calls):
    _patch_get(monkeypatch, calls, 503)
    assert state.safe_get_json("http://x/todo_all", retries=3) == []
    assert len(calls) == 3


def test_connection_error_is_retried(monkeypatch, calls):
    def fake_get(url, timeout):
        calls.append(url)
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(state.requests, "get", fake_get)
    assert state.safe_get_json("http://x/todo_all", retries=3) == []
    assert len(calls) == 3


def test_success_returns_json(monkeypatch, calls):
    _patch_get(monkeypatch, calls, 200)
    assert state.safe_get_json("http://x/todo_all") == {"ok": True}
    assert len(calls) == 1
