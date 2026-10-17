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

# One path segment, no separator inside: the dir name must split back into
# exactly (org, agent, suite).
_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

# Agent-config keys whose values must never reach a public repo: credentials,
# and where a model was served from (a self-hosted vLLM's host is a cluster
# node name). Matched per ``_``-separated segment of the key, so
# ``aws_session_token`` is caught and ``max_tokens`` is not.
_SECRET_SEGMENTS = {
    "key", "apikey", "secret", "token", "password", "passwd", "credential",
    "credentials", "hostname", "host", "endpoint",
}  # fmt: skip
_SECRET_KEYS = {"base_url", "url", "api_base"}


def is_secret_key(key: str) -> bool:
    key = str(key).lower()
    return key in _SECRET_KEYS or any(seg in _SECRET_SEGMENTS for seg in key.split("_"))


def repo_root() -> Path:
    return config_dir().parent


def leaderboard_dir() -> Path:
    return repo_root() / "leaderboard"


def schema_dir() -> Path:
    return leaderboard_dir() / "schema"


def submissions_dir() -> Path:
    return leaderboard_dir() / "submissions"


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


def redact(node: Any) -> tuple[Any, list[str]]:
    """Copy of an agent config with secret-looking values replaced.

    Returns ``(redacted, paths)``. Nulls and empty strings are left alone --
    there is nothing to leak, and keeping them shows the field was unset --
    and so is an already-redacted value, so ``paths`` doubles as the "still
    leaks" check on a committed agent.yaml.
    """
    paths: list[str] = []

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                sub = f"{path}.{k}" if path else str(k)
                leaf = not isinstance(v, (dict, list))
                if leaf and is_secret_key(k) and v not in (None, "", REDACTED):
                    out[k] = REDACTED
                    paths.append(sub)
                else:
                    out[k] = walk(v, sub)
            return out
        if isinstance(value, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    return walk(node, ""), paths


def agent_config_hash(agent_cfg: dict) -> str:
    """sha256 prefix of a (redacted) agent config.

    Same canonicalisation as ``Identity.agent_config_hash`` but over the
    *redacted* config, so anyone can recompute it from the committed
    agent.yaml. (The records' own hashes cover the unredacted config,
    including the per-allocation hostname, and may differ between jobs.)
    """
    blob = json.dumps(agent_cfg, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


# --- YAML / JSONL --------------------------------------------------------------


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(Path(path).read_text())


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


def iter_episodes(path: Path) -> Iterator[tuple[int, Any]]:
    """``(line_no, parsed_or_exception)`` per non-empty line."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for i, raw in enumerate(fh, start=1):
            if not raw.strip():
                continue
            try:
                yield i, json.loads(raw)
            except json.JSONDecodeError as exc:
                yield i, exc


# --- scores comparison -----------------------------------------------------------


def scores_diff(submitted: dict, rescored: dict) -> list[str]:
    """Fields where the submitted scores disagree with a fresh ``score()``.

    Bools are compared as bools (JSON keeps them distinct, and ``True == 1``
    must not let a tampered count through); floats with a tolerance that
    only absorbs serialisation noise.
    """
    diffs = []
    for key in sorted(set(submitted) | set(rescored)):
        a, b = submitted.get(key, "<missing>"), rescored.get(key, "<missing>")
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


def episode_schema() -> dict:
    from open_apps.alignment.record import json_schema

    return json_schema()


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


def schema_errors(instance: Any, schema: dict, root: Optional[dict] = None, path: str = "$") -> list[str]:
    """Errors from checking ``instance`` against a JSON Schema subset.

    Hand-rolled on purpose: ``jsonschema`` is only a transitive dependency
    (via agentlab), and the schemas here use a small, fixed vocabulary --
    type, properties, required, additionalProperties, items, anyOf, enum,
    const, $ref (local), pattern, minLength, minimum, format (ignored).
    Unknown keywords raise, so a schema can't quietly outgrow the checker.
    The tests cross-check against ``jsonschema`` when it is installed.
    """
    root = root if root is not None else schema
    errors: list[str] = []
    known = {
        "$schema", "$id", "$defs", "title", "description", "type", "properties",
        "required", "additionalProperties", "items", "anyOf", "enum", "const",
        "$ref", "pattern", "minLength", "minimum", "format", "examples", "default",
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
        if not any(not schema_errors(instance, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{path}: matches none of the allowed shapes")
        return errors
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}, got {instance!r}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            errors.append(f"{path}: expected {'/'.join(types)}, got {type(instance).__name__}")
            return errors
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if (
        "minimum" in schema
        and _is_type(instance, "number")
        and instance < schema["minimum"]
    ):
        errors.append(f"{path}: below minimum {schema['minimum']}")
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
