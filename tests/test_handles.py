"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Phone/email handle normalisation, shared by Contacts, the messenger and the
bank's payment recipients. Every app has to agree on one spelling of a
handle, or "(555) 010-0001" in a task goal would miss "+15550100001" in state.
"""

import pytest

from open_apps.handles import format_handle, handle_kind, normalize_handle


@pytest.mark.parametrize(
    "raw",
    [
        "+15550100001",
        "+1 555 010 0001",
        "(555) 010-0001",
        "555.010.0001",
        "555-010-0001",
        "5550100001",
        "15550100001",
        "  +1 (555) 010-0001  ",
    ],
)
def test_us_phone_spellings_collapse_to_e164(raw):
    assert normalize_handle(raw) == "+15550100001"


def test_international_numbers_keep_their_country_code():
    assert normalize_handle("+44 20 7946 0958") == "+442079460958"


def test_short_codes_are_kept_as_digits():
    # Verification and spam senders text from 5-6 digit short codes.
    assert normalize_handle("72524") == "72524"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice@Example.com", "alice@example.com"),
        ("  rent@northgateprop.example ", "rent@northgateprop.example"),
    ],
)
def test_emails_are_lowercased_and_stripped(raw, expected):
    assert normalize_handle(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "call me", "555-0100", "12345678901234567", "a@@b.com", "no-at.example", "x@y", None],
)
def test_malformed_input_is_none(raw):
    assert normalize_handle(raw) is None


@pytest.mark.parametrize(
    "handle,shown",
    [
        ("+15550100001", "+1 (555) 010-0001"),
        ("+442079460958", "+442079460958"),
        ("72524", "72524"),
        ("bob@example.com", "bob@example.com"),
    ],
)
def test_format_is_human_readable_and_round_trips(handle, shown):
    assert format_handle(handle) == shown
    assert normalize_handle(shown) == handle


def test_kind():
    assert handle_kind("+15550100001") == "phone"
    assert handle_kind("72524") == "phone"
    assert handle_kind("bob@example.com") == "email"
