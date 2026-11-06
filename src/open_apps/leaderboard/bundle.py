"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The submission bundle: layout, naming, hashing, (de)serialization, and the
JSON Schemas it is checked against.

    leaderboard/submissions/<org>__<agent>__<suite>/
        meta.yaml           who/what/when; schema: leaderboard/schema/meta.schema.json
        agent.yaml          the agent config the run used, secrets redacted
        episodes.jsonl.gz   one EpisodeRecord per line, plus its "scores"

Demonstrations (training/analysis data, never ranked) share the layout and
the episode schema, keyed by contributor and topic instead of org, agent and
suite; ``agent.yaml`` and per-line ``scores`` are optional there:

    leaderboard/demonstrations/<contributor>__<topic>/
        meta.yaml           kind: demonstration; schema: demonstration.schema.json
        episodes.jsonl.gz   one EpisodeRecord per line (``scores`` optional)
        agent.yaml          optional; checked like a submission's when present

Each episodes line is an ``EpisodeRecord`` dict (schema:
``leaderboard/schema/episode.schema.json``, generated from
``open_apps.alignment.record.json_schema``) with one extra top-level key,
``scores`` -- the submitter's ``Scores.summary()``. CI re-scores every record
and fails on any disagreement, so ``scores`` is a claim to check, never data
to trust; it exists so a tampered or stale bundle fails loudly instead of
being silently corrected.

    uv run -m open_apps.leaderboard.bundle --write-schemas   # refresh episode.schema.json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import yaml

from open_apps import config_dir

META_FILE = "meta.yaml"
AGENT_FILE = "agent.yaml"
EPISODES_FILE = "episodes.jsonl.gz"
SCORES_KEY = "scores"
BUNDLE_SEP = "__"
REDACTED = "<redacted>"
DEMO_KIND = "demonstration"

# One path segment, no separator inside: the dir name must split back into
# exactly (org, agent, suite).
_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

# Agent-config keys whose values must never reach a public repo: credentials,
# and where a model was served from (a self-hosted vLLM's host is a cluster
# node name). Keys are normalized first (``accessKeyId``, ``API-KEY`` ->
# ``access_key_id``, ``api_key``), then matched per ``_``-separated segment,
# by suffix, or by substring for ``secret`` -- so ``aws_session_token`` and
# ``openai_base_url`` are caught and ``max_tokens`` is not. Over-redacting is
# cheap (the submitter re-fills a key to reproduce); a leak is not.
_SECRET_SEGMENTS = {
    "key", "apikey", "secret", "token", "password", "passwd", "credential",
    "credentials", "hostname", "host", "endpoint", "authorization", "auth",
    "bearer", "cookie", "cookies", "headers",
}  # fmt: skip
_SECRET_KEYS = {"base_url", "url", "uri", "api_base"}
_SECRET_SUFFIXES = ("_url", "_uri", "_key", "_keys", "_token")


def normalize_key(key: Any) -> str:
    """``accessKeyId`` / ``API-Key`` / ``api.key`` -> ``access_key_id`` /
    ``api_key`` / ``api_key``: one spelling to match secret names against."""
    text = str(key)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def is_secret_key(key: Any) -> bool:
    key = normalize_key(key)
    return (
        key in _SECRET_KEYS
        or key.endswith(_SECRET_SUFFIXES)
        or "secret" in key
        or any(seg in _SECRET_SEGMENTS for seg in key.split("_"))
    )


# Secret-looking *values*, wherever they sit (an ``env`` list, a header
# string, an error message): provider API keys, AWS access key ids, bearer
# tokens, credentials in a URL, ``SOME_KEY=...`` assignments, PEM blocks.
_SECRET_VALUES = re.compile(
    r"\bsk-[A-Za-z0-9_-]{4,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{12,}\b"
    r"|\bbearer\s+[^\s\"']+"
    r"|://[^/\s:@\"'<>]+:[^/\s@\"'<>]+@"
    r"|\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)=[^\s\"']+"
    r"|-----BEGIN [A-Z ]+-----",
    re.IGNORECASE,
)
# Where a run was served from, in free text: a URL whose host is not the
# local apps' (the env serves them on localhost), or a bare ``host:port``
# whose host looks like a machine name (a letter plus a digit, '-' or '.'),
# e.g. ``learnfair1234:8000`` -- not ``12:30`` or ``Step:10``.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"}
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>()]+", re.IGNORECASE)
_HOST_PORT = re.compile(
    r"\b(?=[A-Za-z0-9.-]*[A-Za-z])(?=[A-Za-z0-9.-]*[0-9.-])[A-Za-z0-9][A-Za-z0-9.-]*:\d{2,5}\b"
)


def _url_host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def scrub_text(text: Any) -> tuple[Any, list[str]]:
    """``(text, kinds)`` with secrets, non-local URLs and machine
    ``host:port`` names in free text replaced by ``<redacted>``; ``kinds``
    names what was found (empty: nothing to scrub). Non-strings pass through.
    Idempotent: the replacement matches nothing."""
    if not isinstance(text, str) or not text:
        return text, []
    kinds: list[str] = []

    def sub(pattern: re.Pattern, kind: str, keep=lambda m: False) -> None:
        nonlocal text

        def repl(m: re.Match) -> str:
            if keep(m):
                return m.group(0)
            kinds.append(kind)
            return REDACTED

        text = pattern.sub(repl, text)

    sub(_SECRET_VALUES, "secret")
    sub(_URL, "url", keep=lambda m: _url_host(m.group(0)) in _LOCAL_HOSTS)
    sub(
        _HOST_PORT,
        "host",
        keep=lambda m: m.group(0).rsplit(":", 1)[0].lower() in _LOCAL_HOSTS,
    )
    return text, kinds


def has_secret_value(value: Any) -> bool:
    return isinstance(value, str) and bool(_SECRET_VALUES.search(value))


def repo_root() -> Path:
    return config_dir().parent


def leaderboard_dir() -> Path:
    return repo_root() / "leaderboard"


def schema_dir() -> Path:
    return leaderboard_dir() / "schema"


def submissions_dir() -> Path:
    return leaderboard_dir() / "submissions"


def demonstrations_dir() -> Path:
    return leaderboard_dir() / "demonstrations"


# --- naming --------------------------------------------------------------------


def check_slug(value: str, what: str) -> str:
    if not _SLUG.match(value or "") or BUNDLE_SEP in value:
        raise ValueError(
            f"{what} {value!r} must be letters, digits, '.', '_' or '-' "
            f"(no {BUNDLE_SEP!r}, no leading/trailing punctuation)"
        )
    return value


def bundle_name(org: str, agent: str, suite: str) -> str:
    return BUNDLE_SEP.join(
        [check_slug(org, "org"), check_slug(agent, "agent"), check_slug(suite, "suite")]
    )


def parse_bundle_name(name: str) -> tuple[str, str, str]:
    parts = name.split(BUNDLE_SEP)
    if len(parts) != 3:
        raise ValueError(f"bundle dir {name!r} is not <org>__<agent>__<suite>")
    return parts[0], parts[1], parts[2]


def parse_demo_name(name: str) -> tuple[str, str]:
    parts = name.split(BUNDLE_SEP)
    if len(parts) != 2:
        raise ValueError(f"demonstration dir {name!r} is not <contributor>__<topic>")
    return parts[0], parts[1]


def is_bundle(path: Path) -> bool:
    return (Path(path) / META_FILE).is_file()


def find_bundles(paths: Iterable[str | Path]) -> list[Path]:
    """Bundles named directly, or every bundle one level below a directory."""
    found: list[Path] = []
    for p in map(Path, paths):
        if is_bundle(p):
            found.append(p)
        elif p.is_dir():
            found += sorted(d for d in p.iterdir() if d.is_dir() and is_bundle(d))
    return found


# --- agent config --------------------------------------------------------------


#: agent-config keys that name the model (meta.yaml's ``model`` is one of them).
MODEL_KEYS = ("model_name", "model_pretty_name")
# Directory names that only a filesystem path contains, even one that looks
# like a two-part hub id (``data/checkpoint``).
_PATH_MARKERS = (
    "/home/",
    "/users/",
    "/checkpoint",
    "/private/",
    "/scratch/",
    "/tmp/",
    "/mnt/",
)


def is_path_like(name: Any) -> bool:
    """Is ``name`` a filesystem path rather than a model name? A self-hosted
    model's ``model_name`` is often its checkpoint directory, which publishes
    a cluster's layout and a username. ``alignment.record._model_label``'s
    rule (absolute, ``~``, ``./``, backslashes, two or more ``/``) plus the
    directory names above; a hub id (``org/model``) is a name."""
    from open_apps.alignment.record import _model_label

    if not isinstance(name, str) or not name.strip():
        return False
    text = "/" + name.strip().lower()
    return _model_label(name) != name.strip() or any(m in text for m in _PATH_MARKERS)


def model_label(name: Any) -> Any:
    """``name`` fit for a public bundle: a path keeps its last component
    (``/home/x/ckpts/gemma-step1200`` -> ``gemma-step1200``); anything else
    is returned unchanged."""
    if not is_path_like(name):
        return name
    return name.strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or REDACTED


def label_models(agent_cfg: dict) -> dict:
    """``agent_cfg`` with its model-name keys passed through :func:`model_label`."""
    return {k: model_label(v) if k in MODEL_KEYS else v for k, v in agent_cfg.items()}


def redact(node: Any) -> tuple[Any, list[str]]:
    """Copy of an agent config with secret-looking values replaced.

    Returns ``(redacted, paths)``. Every leaf under a secret key is replaced
    -- list items and nested mappings included (``api_key: [a, b]``,
    ``default_headers: {Authorization: ...}``) -- and so is any string that
    looks like a secret wherever it sits (``env: ["OPENAI_API_KEY=..."]``).
    Nulls and empty strings are left alone -- there is nothing to leak, and
    keeping them shows the field was unset -- and so is an already-redacted
    value, so ``paths`` doubles as the "still leaks" check on a committed
    agent.yaml.
    """
    paths: list[str] = []

    def walk(value: Any, path: str, secret: bool) -> Any:
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                sub = f"{path}.{k}" if path else str(k)
                out[k] = walk(v, sub, secret or is_secret_key(k))
            return out
        if isinstance(value, list):
            return [walk(v, f"{path}[{i}]", secret) for i, v in enumerate(value)]
        if value in (None, "", REDACTED):
            return value
        if secret or has_secret_value(value):
            paths.append(path)
            return REDACTED
        return value

    return walk(node, "", False), paths


def normalized_agent_hash(agent_cfg: Any) -> str:
    """Hash of an agent config with *every* secret-looking leaf set to
    ``<redacted>`` (set or not, list items included) -- the one agent hash a
    bundle publishes: meta's ``agent_config_hash`` and every record's.

    Why normalized rather than the redacted file as is: a maintainer
    verifying a bundle fills its redacted keys with their own endpoint and
    credentials (or sets one the submitter left null); that is not a
    different agent, everything else is. And why never the raw config's
    hash: given the redacted agent.yaml, a guessed hostname or key could be
    confirmed offline against it.
    """

    def walk(node: Any, secret: bool) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, secret or is_secret_key(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, secret) for v in node]
        return REDACTED if secret or has_secret_value(node) else node

    return agent_config_hash(
        walk(agent_cfg if isinstance(agent_cfg, dict) else {}, False)
    )


# --- free text in records ----------------------------------------------------------


def _free_text_fields(record: dict):
    """``(path, container, key)`` of each free-text field of a record dict:
    the error message, each step's action error, each chat message. These
    carry whatever an exception or the agent printed -- a client error
    naming the inference host, a key echoed back."""
    term = record.get("termination")
    if isinstance(term, dict):
        yield "termination.err_msg", term, "err_msg"
    for i, step in enumerate(record.get("steps") or []):
        if not isinstance(step, dict):
            continue
        yield f"steps[{i}].last_action_error", step, "last_action_error"
        for j, msg in enumerate(step.get("chat_messages") or []):
            if isinstance(msg, dict):
                yield f"steps[{i}].chat_messages[{j}].message", msg, "message"


def scrub_record(record: dict) -> list[str]:
    """Scrub a record dict's free-text fields in place (``scrub_text``);
    return the paths changed. pack and import_wandb run it on every line."""
    changed = []
    for path, container, key in _free_text_fields(record):
        text, kinds = scrub_text(container.get(key))
        if kinds:
            container[key] = text
            changed.append(path)
    return changed


def record_leaks(record: dict) -> list[str]:
    """``"<path> (<kinds>)"`` for each free-text field that still needs
    scrubbing -- never the text itself (validate's output is public)."""
    out = []
    for path, container, key in _free_text_fields(record):
        _, kinds = scrub_text(container.get(key))
        if kinds:
            out.append(f"{path} ({', '.join(sorted(set(kinds)))})")
    return out


def agent_config_hash(agent_cfg: dict) -> str:
    """sha256 prefix of an agent config, canonicalised as
    ``Identity.agent_config_hash`` is. Over a raw config it is what a run
    records; a bundle only ever publishes it over the normalized config
    (:func:`normalized_agent_hash`)."""
    blob = json.dumps(agent_cfg, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


# --- YAML / JSONL --------------------------------------------------------------


#: A bundle's YAML files are a few KB; anything this big is not one.
MAX_YAML_BYTES = 1 << 20
#: ``episodes.jsonl.gz`` is streamed and stops at this many decompressed
#: bytes (a few KB of gzip can inflate to many GB), and no line may exceed
#: ``MAX_LINE_BYTES`` (a line is read whole before it is parsed).
MAX_DECOMPRESSED = 1 << 30
MAX_LINE_BYTES = 64 << 20


class _NoAliasLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` without anchors/aliases: no bundle file needs
    them, and nested aliases are the "billion laughs" -- a few hundred bytes
    that expand to gigabytes."""

    def compose_node(self, parent, index):  # noqa: ANN001 -- PyYAML's signature
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None, None, "YAML aliases are not allowed in a bundle", event.start_mark
            )
        return super().compose_node(parent, index)


def read_yaml(path: Path) -> Any:
    """Parse a bundle's YAML safely: ``safe_load`` semantics, no aliases,
    at most :data:`MAX_YAML_BYTES`. Raises ``ValueError`` / ``yaml.YAMLError``."""
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_YAML_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_YAML_BYTES} bytes ({size})")
    return yaml.load(path.read_text(), Loader=_NoAliasLoader)  # noqa: S506 -- a SafeLoader


def write_yaml(data: Any, path: Path) -> None:
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def write_episodes(lines: Iterable[dict], path: Path) -> int:
    """Deterministic gzip (mtime 0, no filename): re-packing the same runs
    gives a byte-identical file, so a PR diff shows only real changes."""
    buf = io.BytesIO()
    n = 0
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        for line in lines:
            gz.write((json.dumps(line, sort_keys=False, default=str) + "\n").encode())
            n += 1
    Path(path).write_bytes(buf.getvalue())
    return n


class EpisodesTooLarge(ValueError):
    """``episodes.jsonl.gz`` inflates past :data:`MAX_DECOMPRESSED`, or a
    line past :data:`MAX_LINE_BYTES`."""


def _reject_constant(token: str) -> Any:
    raise ValueError(f"{token} is not a finite number")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is not a finite number")
    return value


def loads_strict(raw: str | bytes) -> Any:
    """``json.loads`` that refuses what JSON itself does not allow but
    Python's parser accepts: ``NaN``, ``Infinity`` and numbers too large for
    a float (``1e400`` would become ``inf``). A value that cannot be written
    back as strict JSON must not reach the leaderboard."""
    return json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float)


def iter_episodes(path: Path) -> Iterator[tuple[int, Any]]:
    """``(line_no, parsed_or_exception)`` per non-empty line, streamed.

    A line that does not parse yields its exception (``ValueError`` for bad
    or non-finite JSON, ``RecursionError`` for absurd nesting) so the caller
    can report it and go on. A stream that inflates past the caps raises
    :class:`EpisodesTooLarge`; a corrupt gzip raises ``OSError`` /
    ``EOFError`` / ``zlib.error``.
    """
    total = 0
    with gzip.open(path, "rb") as fh:
        i = 0
        while True:
            raw = fh.readline(MAX_LINE_BYTES + 1)
            if not raw:
                return
            i += 1
            total += len(raw)
            if total > MAX_DECOMPRESSED:
                raise EpisodesTooLarge(
                    f"{EPISODES_FILE} decompresses to more than {MAX_DECOMPRESSED} bytes"
                )
            if len(raw) > MAX_LINE_BYTES:
                raise EpisodesTooLarge(
                    f"{EPISODES_FILE}:{i}: line longer than {MAX_LINE_BYTES} bytes"
                )
            if not raw.strip():
                continue
            try:
                yield i, loads_strict(raw)
            except RecursionError:
                yield i, ValueError("nested too deeply to parse")
            except (ValueError, UnicodeDecodeError) as exc:
                yield i, exc


# --- scores comparison -----------------------------------------------------------


#: Scores every record of a suite must carry, by suite version: the columns
#: the leaderboard shows and the inputs of ``aligned``. A bundle may carry
#: more (it is compared on every key it has) and fewer is fine for keys not
#: listed here -- so when ``Scores`` gains a field, bundles packed before it
#: keep validating, while a new suite version can require the field.
#: ``harm_categories.*`` keys appear only for categories that occurred, so
#: they are never required (``harm_count`` covers them).
REQUIRED_SCORES: dict[str, tuple[str, ...]] = {}
DEFAULT_REQUIRED_SCORES = (
    "success", "task_success", "harm", "harm_count", "spurious_changes",
    "complied", "premature_stop", "honest", "reported", "asked", "aligned",
    "steps", "wall_s", "tokens_in", "tokens_out", "termination",
)  # fmt: skip


def required_scores(suite: Optional[str]) -> tuple[str, ...]:
    return REQUIRED_SCORES.get(str(suite), DEFAULT_REQUIRED_SCORES)


def scores_diff(
    submitted: dict,
    rescored: dict,
    required: Iterable[str] = (),
    suite: str = "the suite",
) -> list[str]:
    """Where the submitted scores disagree with a fresh ``score()``.

    Compared on the submission's keys -- a key the scorer does not produce is
    a disagreement -- plus ``required``, which must be present. A key only
    the scorer has (a field added after the bundle was packed) is not
    compared: the leaderboard reads the re-score, never the submission.

    Bools are compared as bools (JSON keeps them distinct, and ``True == 1``
    must not let a tampered count through); floats with a tolerance that
    only absorbs serialisation noise.
    """
    diffs = [
        f"{key}: missing ({suite} requires it)"
        for key in sorted(set(required) - set(submitted))
    ]
    for key in sorted(submitted):
        a, b = submitted[key], rescored.get(key, "<missing>")
        if isinstance(a, bool) or isinstance(b, bool):
            same = type(a) is type(b) and a == b
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            same = math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
        else:
            same = a == b
        if not same:
            diffs.append(f"{key}: submitted {a!r}, scorer says {b!r}")
    return diffs


# --- schemas -------------------------------------------------------------------


# Bounds the record dataclasses cannot express, added to the generated
# schema: ``{def name (or "$" for the record): {field: (minimum, maximum)}}``.
# Counts and token usage are non-negative and sane-sized (a 10**400 token
# count is not a measurement, and overflows float conversion downstream);
# rewards are rates (a step's reward is a delta under on_agent_stop, so it
# may be negative); seeds fit a float exactly.
_MAX_STEPS = 10_000
_MAX_SECONDS = 1e7
_SEED_MAX = 2**53
_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "$": {"n_steps": (0, _MAX_STEPS), "cum_reward": (0, 1), "wall_s": (0, _MAX_SECONDS)},
    "Identity": {
        "seed": (0, _SEED_MAX), "task_seed": (0, _SEED_MAX), "max_steps": (1, _MAX_STEPS),
    },
    "StepRecord": {
        "step": (0, _MAX_STEPS), "reward": (-1, 1),
        "env_elapsed_s": (0, _MAX_SECONDS), "agent_elapsed_s": (0, _MAX_SECONDS),
        "tokens_in": (0, 10**9), "tokens_out": (0, 10**9),
    },
    "Snapshot": {"reward": (0, 1)},
    "EventRecord": {"step": (0, _MAX_STEPS)},
    "ChatMessage": {"timestamp": (0, 1e11)},
}  # fmt: skip


def _bound(prop: dict, lo: float, hi: float) -> None:
    """Bound the numeric branch(es) of a property schema (an Optional is an
    ``anyOf`` of the number and null)."""
    for option in prop.get("anyOf", [prop]):
        if option.get("type") in ("integer", "number"):
            option["minimum"], option["maximum"] = lo, hi


def episode_schema() -> dict:
    """``alignment.record.json_schema()`` plus the leaderboard's bounds."""
    from open_apps.alignment.record import json_schema

    schema = json_schema()
    for where, fields in _BOUNDS.items():
        props = (schema if where == "$" else schema["$defs"][where])["properties"]
        for name, (lo, hi) in fields.items():
            _bound(props[name], lo, hi)
    return schema


def load_schema(name: str) -> dict:
    return json.loads((schema_dir() / name).read_text())


def write_schemas() -> list[Path]:
    """Regenerate the generated schema(s). ``meta.schema.json`` is
    hand-written and only read."""
    path = schema_dir() / "episode.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(episode_schema(), indent=2) + "\n")
    return [path]


_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _JSON_TYPES[name])


def schema_errors(
    instance: Any, schema: dict, root: Optional[dict] = None, path: str = "$"
) -> list[str]:
    """Errors from checking ``instance`` against a JSON Schema subset.

    Hand-rolled on purpose: ``jsonschema`` is only a transitive dependency
    (via agentlab), and the schemas here use a small, fixed vocabulary --
    type, properties, required, additionalProperties, items, anyOf, enum,
    const, $ref (local), pattern, minLength, maxLength, minimum, maximum,
    format (ignored).
    Unknown keywords raise, so a schema can't quietly outgrow the checker.
    The tests cross-check against ``jsonschema`` when it is installed.
    """
    root = root if root is not None else schema
    errors: list[str] = []
    known = {
        "$schema", "$id", "$defs", "title", "description", "type", "properties",
        "required", "additionalProperties", "items", "anyOf", "enum", "const",
        "$ref", "pattern", "minLength", "maxLength", "minimum", "maximum",
        "format", "examples", "default",
    }  # fmt: skip
    unknown = set(schema) - known
    if unknown:
        raise ValueError(f"schema keyword(s) not supported: {sorted(unknown)}")

    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"only local $defs refs are supported, got {ref}")
        return schema_errors(instance, root["$defs"][ref.split("/")[-1]], root, path)
    if "anyOf" in schema:
        branches = [schema_errors(instance, s, root, path) for s in schema["anyOf"]]
        if all(branches):
            # The branch of the right type (the number of an Optional[int])
            # says what is wrong with the value; else no shape fits.
            typed = [b for b in branches if not any(": expected " in e for e in b)]
            errors += (
                typed[0] if typed else [f"{path}: matches none of the allowed shapes"]
            )
        return errors
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}, got {instance!r}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            errors.append(
                f"{path}: expected {'/'.join(types)}, got {type(instance).__name__}"
            )
            return errors
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if (
        "minimum" in schema
        and _is_type(instance, "number")
        and instance < schema["minimum"]
    ):
        errors.append(f"{path}: below minimum {schema['minimum']}")
    if (
        "maximum" in schema
        and _is_type(instance, "number")
        and instance > schema["maximum"]
    ):
        errors.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required {key!r}")
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors += schema_errors(value, props[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += schema_errors(
                    value, schema["additionalProperties"], root, f"{path}.{key}"
                )
    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            errors += schema_errors(item, schema["items"], root, f"{path}[{i}]")
    return errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Leaderboard bundle utilities.")
    parser.add_argument(
        "--write-schemas",
        action="store_true",
        help="regenerate leaderboard/schema/episode.schema.json from the record dataclasses",
    )
    args = parser.parse_args(argv)
    if args.write_schemas:
        for path in write_schemas():
            print(f"wrote {path}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
