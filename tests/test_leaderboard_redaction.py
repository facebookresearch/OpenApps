"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Nothing secret or cluster-specific reaches a bundle -- the repo it lands in
is public. Covers the shapes the review found leaking through ``redact``
(list items, camelCase and hyphenated keys, ``*_url`` suffixes, headers,
secrets in string values), free text in records (error messages, chat), and
the per-record ``agent_config_hash``, which hashed the *unredacted* config
and so let anyone confirm a guessed hostname or key offline.
"""

from __future__ import annotations

import gzip
import json
import shutil

import pytest
import yaml

from open_apps.leaderboard import bundle as B
from open_apps.leaderboard.pack import pack
from open_apps.leaderboard.validate import FAIL, OK, validate_bundle
from tests._leaderboard_helpers import (
    DUMMY_AGENT,
    EXAMPLE_BUNDLE,
    FIXED_DATE,
    TINY_SUITE,
    write_logs_tree,
)

# The review's agent config (/tmp/lbexp/exp2.py): every value but model_name leaks.
REVIEW_CFG = {
    "model_name": "x",
    "api_key": ["sk-live-a", "sk-live-b"],
    "api_keys": ["sk-live-c"],
    "api-key": "sk-live-d",
    "default_headers": {"Authorization": "Bearer sk-live-e"},
    "openai_base_url": "http://learnfair1234:8000/v1",
    "vllm_url": "http://gpu-node-12:8000/v1",
    "server_url": "https://user:pass@internal.example:8000",
    "baseUrl": "http://node5:8000",
    "env": ["OPENAI_API_KEY=sk-live-f"],
    "model_args": {"base_url": "http://ok-caught"},
    "accessKeyId": "AKIA...",
    "bearer": "sk-live-g",
    "auth": "sk-live-h",
}


def _leaves(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield path, node


def test_every_review_shape_is_redacted():
    out, paths = B.redact(REVIEW_CFG)
    leaked = {p: v for p, v in _leaves(out) if v != B.REDACTED and p != "model_name"}
    assert leaked == {}, leaked
    assert out["model_name"] == "x"
    blob = json.dumps(out)
    for secret in ("sk-live", "learnfair", "gpu-node", "user:pass", "node5", "AKIA"):
        assert secret not in blob
    # Paths name each leaf, list items included.
    assert {
        "api_key[0]",
        "api_key[1]",
        "default_headers.Authorization",
        "env[0]",
    } <= set(paths)


@pytest.mark.parametrize(
    "key",
    [
        "api_key", "apiKey", "API-KEY", "api-key", "accessKeyId", "access_key_id",
        "aws_session_token", "client_secret", "mySecretThing", "baseUrl", "base-url",
        "openai_base_url", "vllm_url", "redirect_uri", "Authorization", "cookie",
        "default_headers", "bearer", "auth", "password", "db_password", "hostname",
        "endpoint", "api_keys", "signing_key",
    ],
)  # fmt: skip
def test_secret_keys_are_recognised_in_any_spelling(key):
    assert B.is_secret_key(key), key


@pytest.mark.parametrize(
    "key",
    ["max_tokens", "model_name", "temperature", "use_html", "client_type", "author"],
)
def test_lookalikes_are_not_secret(key):
    assert not B.is_secret_key(key), key


@pytest.mark.parametrize(
    "value",
    [
        "sk-proj-abcdef123456",
        "AKIAABCDEFGHIJKLMNOP",
        "Bearer abc.def.ghi",
        "https://user:hunter2@host.example/v1",
        "OPENAI_API_KEY=whatever",
        "HF_TOKEN=hf_abc",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
    ],
)
def test_secret_values_are_redacted_under_any_key(value):
    out, paths = B.redact({"notes": value, "extra": [value]})
    assert out == {"notes": B.REDACTED, "extra": [B.REDACTED]}
    assert paths == ["notes", "extra[0]"]


def test_redaction_is_idempotent_and_keeps_nulls():
    out, _ = B.redact(REVIEW_CFG)
    again, paths = B.redact(out)
    assert again == out and paths == []
    assert B.redact({"api_key": None, "base_url": ""}) == (
        {"api_key": None, "base_url": ""},
        [],
    )


# --- free text in records ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, gone",
    [
        ("ConnectionError: http://learnfair1234:8000/v1/chat refused", "learnfair1234"),
        ("could not reach gpu-node-12:8000", "gpu-node-12"),
        ("auth failed for Bearer sk-live-xyz123", "sk-live-xyz123"),
        ("OPENAI_API_KEY=sk-abcdef1234 not set", "sk-abcdef1234"),
        ("posting to https://user:pw@internal.example/x", "user:pw"),
    ],
)
def test_free_text_secrets_and_hosts_are_scrubbed(text, gone):
    out, hits = B.scrub_text(text)
    assert gone not in out and hits
    assert B.scrub_text(out) == (out, [])  # idempotent


def test_local_app_urls_and_ordinary_text_survive():
    for text in (
        "TimeoutError: page.goto http://localhost:5001/todo timed out",
        "Meet at 12:30 on Mar 01, 1:20PM",
        "Done, I added Call Mom to your list.",
    ):
        assert B.scrub_text(text) == (text, [])


def _logs_with_leaky_text(root):
    """A sweep whose records carry a cluster hostname in an error message,
    a step error and a chat message."""
    logs = write_logs_tree(root)
    for path in logs.rglob("episode.json"):
        record = json.loads(path.read_text())
        record["termination"]["err_msg"] = (
            "APIConnectionError: http://learnfair1234:8000/v1"
        )
        record["steps"][0]["last_action_error"] = "upstream gpu-node-12:8000 refused"
        record["steps"][-1]["chat_messages"].append(
            {
                "role": "assistant",
                "message": "key OPENAI_API_KEY=sk-live-abc123",
                "timestamp": 2.0,
            }
        )
        path.write_text(json.dumps(record))
    return logs


def test_pack_scrubs_free_text_and_the_bundle_validates(tmp_path):
    logs = _logs_with_leaky_text(tmp_path / "logs")
    out = pack(
        [logs],
        org="example",
        agent="dummy",
        out=tmp_path / EXAMPLE_BUNDLE.name,
        date=FIXED_DATE,
    ).out
    blob = gzip.decompress((out / B.EPISODES_FILE).read_bytes()).decode()
    for leak in ("learnfair1234", "gpu-node-12", "sk-live-abc123"):
        assert leak not in blob
    report = validate_bundle(out, suite=TINY_SUITE)
    assert report.errors == [] and report.status == OK


def _lines(path):
    with gzip.open(path / B.EPISODES_FILE, "rt") as fh:
        return [json.loads(line) for line in fh]


@pytest.fixture
def bundle(tmp_path):
    dst = tmp_path / EXAMPLE_BUNDLE.name
    shutil.copytree(EXAMPLE_BUNDLE, dst)
    return dst


@pytest.mark.parametrize(
    "where",
    ["termination.err_msg", "steps[0].last_action_error", "steps[-1].chat_messages"],
)
def test_validate_fails_on_a_free_text_leak(bundle, where):
    lines = _lines(bundle)
    leak = "see http://learnfair1234:8000/v1"
    if where == "termination.err_msg":
        lines[0]["termination"]["err_msg"] = leak
    elif where == "steps[0].last_action_error":
        lines[0]["steps"][0]["last_action_error"] = leak
    else:
        lines[0]["steps"][-1]["chat_messages"].append(
            {"role": "user", "message": leak, "timestamp": 1.0}
        )
    B.write_episodes(lines, bundle / B.EPISODES_FILE)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.status == FAIL
    assert any("unredacted" in e and "re-pack" in e for e in report.errors), (
        report.errors
    )
    assert not any("learnfair1234" in e for e in report.errors)  # never echoed


# --- the per-record agent_config_hash ---------------------------------------------


def test_record_hashes_are_the_normalized_redacted_hash(tmp_path):
    """The record's own hash covered the unredacted config: with the
    redacted agent.yaml in hand, guessing the hostname or key and hashing
    confirmed it (/tmp/lbexp/exp3.py). pack rewrites every record's hash to
    meta's, which anyone can recompute from agent.yaml."""
    logs = write_logs_tree(tmp_path / "logs")
    out = pack(
        [logs], org="example", agent="dummy", out=tmp_path / "b", date=FIXED_DATE
    ).out
    meta = yaml.safe_load((out / B.META_FILE).read_text())
    agent = yaml.safe_load((out / B.AGENT_FILE).read_text())
    assert meta["agent_config_hash"] == B.normalized_agent_hash(agent)
    hashes = {line["identity"]["agent_config_hash"] for line in _lines(out)}
    assert hashes == {meta["agent_config_hash"]}
    unredacted = B.agent_config_hash(DUMMY_AGENT)
    assert unredacted not in hashes


def test_normalized_hash_ignores_secret_values_only():
    base = B.normalized_agent_hash(DUMMY_AGENT)
    assert B.normalized_agent_hash(dict(DUMMY_AGENT, hostname="other-node")) == base
    assert B.normalized_agent_hash(dict(DUMMY_AGENT, api_key=None)) == base
    assert B.normalized_agent_hash(dict(DUMMY_AGENT, max_tokens=1)) != base


def test_a_record_with_another_agent_hash_fails(bundle):
    lines = _lines(bundle)
    lines[0]["identity"]["agent_config_hash"] = "0123456789ab"
    B.write_episodes(lines, bundle / B.EPISODES_FILE)
    report = validate_bundle(bundle, suite=TINY_SUITE)
    assert report.status == FAIL
    assert any(
        "agent_config_hash" in e and "one agent config per bundle" in e
        for e in report.errors
    )
