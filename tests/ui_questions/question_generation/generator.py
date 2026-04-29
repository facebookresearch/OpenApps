"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Generates multiple-choice questions from app state and Hydra config.

Usage:
    # From a running server
    questions = generate_questions_from_server("http://localhost:5011")

    # From a TestClient (in tests)
    questions = generate_questions_from_client(client, config)

    # From a saved state JSON file
    questions = generate_questions_from_state(state_dict, config_dict)
"""

import json
import random
from pathlib import Path

import requests
from omegaconf import DictConfig, OmegaConf

from tests.ui_questions.question_generation.templates import ALL_TEMPLATES, MCQuestion


def _fetch_state_from_server(base_url: str) -> dict:
    """Fetch current app state from a running OpenApps server."""
    state = {}
    endpoints = {
        "todo": "/todo_all",
        "calendar": "/calendar_all",
        "map": "/maps/landmarks",
        "messenger": "/messages_all",
    }
    for key, path in endpoints.items():
        try:
            resp = requests.get(base_url + path)
            resp.raise_for_status()
            state[key] = resp.json()
        except requests.exceptions.RequestException:
            state[key] = []
    return state


def _fetch_state_from_client(client) -> dict:
    """Fetch current app state from a Starlette TestClient."""
    state = {}
    endpoints = {
        "todo": "/todo_all",
        "calendar": "/calendar_all",
        "map": "/maps/landmarks",
        "messenger": "/messages_all",
    }
    for key, path in endpoints.items():
        resp = client.get(path)
        if resp.status_code == 200:
            state[key] = resp.json()
        else:
            state[key] = []
    return state


def _config_to_dict(config) -> dict:
    """Convert Hydra DictConfig to a plain dict, or pass through if already dict."""
    if isinstance(config, DictConfig):
        return OmegaConf.to_container(config, resolve=True)
    return config if config else {}


def _get_app_config(config: dict, app_name: str) -> dict:
    """Extract the sub-config for a specific app."""
    apps_config = config.get("apps", config)
    key_map = {
        "todo": "todo",
        "calendar": "calendar",
        "messenger": "messenger",
        "map": "maps",
        "start_page": "start_page",
    }
    key = key_map.get(app_name, app_name)
    if isinstance(key, dict):
        return key
    return apps_config.get(key, {}) if isinstance(apps_config, dict) else {}


def generate_questions(
    state: dict,
    config: dict | None = None,
    seed: int = 42,
    apps: list[str] | None = None,
) -> list[MCQuestion]:
    """
    Generate all questions from templates given app state and config.

    Args:
        state: Dict with keys like 'todo', 'calendar', 'messenger', 'map'
               (as returned by the /*_all endpoints).
        config: Hydra config dict (optional, used for appearance/content-aware questions).
        seed: Random seed for reproducible question generation.
        apps: List of app names to generate questions for. None = all.

    Returns:
        List of MCQuestion instances.
    """
    config = config or {}
    rng = random.Random(seed)
    questions = []

    target_apps = apps or list(ALL_TEMPLATES.keys())

    for app_name in target_apps:
        templates = ALL_TEMPLATES.get(app_name, [])
        app_config = _get_app_config(config, app_name)

        for template_fn in templates:
            try:
                qs = template_fn(state, app_config, rng)
                questions.extend(qs)
            except (KeyError, IndexError, ValueError):
                continue

    return questions


def generate_questions_from_server(
    base_url: str,
    config: dict | None = None,
    seed: int = 42,
    apps: list[str] | None = None,
) -> list[MCQuestion]:
    """Generate questions by fetching state from a running server."""
    state = _fetch_state_from_server(base_url)
    return generate_questions(state, config, seed, apps)


def generate_questions_from_client(
    client,
    config=None,
    seed: int = 42,
    apps: list[str] | None = None,
) -> list[MCQuestion]:
    """Generate questions by fetching state from a Starlette TestClient."""
    state = _fetch_state_from_client(client)
    config_dict = _config_to_dict(config)
    return generate_questions(state, config_dict, seed, apps)


def generate_questions_from_state(
    state: dict,
    config: dict | None = None,
    seed: int = 42,
    apps: list[str] | None = None,
) -> list[MCQuestion]:
    """Generate questions from a pre-loaded state dict (e.g., from a JSON file)."""
    return generate_questions(state, config, seed, apps)


APP_TO_SCREENSHOT = {
    "todo": "tests/generated_screenshots/default/todo.png",
    "calendar": "tests/generated_screenshots/default/calendar.png",
    "messenger": "tests/generated_screenshots/default/messages.png",
    "map": "tests/generated_screenshots/default/maps.png",
    "codeeditor": "tests/generated_screenshots/default/codeeditor.png",
    "start_page": "tests/generated_screenshots/default/start_page.png",
}


def questions_to_json(questions: list[MCQuestion]) -> list[dict]:
    """Serialize a list of MCQuestion to JSON-friendly dicts."""
    return [
        {
            "question": q.question,
            "choices": q.choices,
            "correct": q.correct,
            "category": q.category,
            "app": q.app,
            "difficulty": q.difficulty,
            "formatted": q.format_as_prompt(),
            "screenshot_path": APP_TO_SCREENSHOT.get(q.app, ""),
        }
        for q in questions
    ]
