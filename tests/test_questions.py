"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Tests for the UI understanding question generator.
"""

import json
import pytest
from pathlib import Path
from hydra import initialize, compose
from starlette.testclient import TestClient

from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task
from tests.ui_questions.question_generation.generator import (
    generate_questions_from_client,
    generate_questions_from_state,
    questions_to_json,
)
from tests.ui_questions.question_generation.templates import (
    MCQuestion,
    ALL_TEMPLATES,
    MAX_VISIBLE_TODOS,
)


@pytest.fixture(scope="module")
def client_and_config(tmpdir_factory):
    logs_dir = str(tmpdir_factory.getbasetemp())
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    try:
        initialize_routes_and_configure_task(config.apps)
    # skip if already initialized
    except Exception:
        pass
    return TestClient(app), config


class TestQuestionGeneration:

    def test_generates_questions(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        assert len(questions) > 0
        assert all(isinstance(q, MCQuestion) for q in questions)

    def test_all_questions_have_four_choices(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        for q in questions:
            assert len(q.choices) == 4, f"Question has {len(q.choices)} choices: {q.question}"
            assert set(q.choices.keys()) == {"A", "B", "C", "D"}

    def test_correct_answer_is_valid(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        for q in questions:
            assert q.correct in q.choices, f"Correct answer '{q.correct}' not in choices for: {q.question}"

    def test_no_duplicate_choices(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        for q in questions:
            values = list(q.choices.values())
            assert len(values) == len(set(values)), f"Duplicate choices in: {q.question}"

    def test_todo_counting_questions_correct(self, client_and_config):
        client, config = client_and_config
        todo_state = client.get("/todo_all").json()
        # Counting templates only consider the visible prefix of the todo list.
        visible = todo_state[:MAX_VISIBLE_TODOS]
        done_count = sum(1 for t in visible if t.get("done"))
        not_done_count = len(visible) - done_count

        questions = generate_questions_from_client(client, config, apps=["todo"])
        counting_qs = [q for q in questions if q.category == "element_counting"]
        assert len(counting_qs) >= 2

        for q in counting_qs:
            correct_value = q.choices[q.correct]
            q_lower = q.question.lower()
            if "checked" in q_lower and "unchecked" not in q_lower:
                assert correct_value == str(done_count)
            elif "unchecked" in q_lower or "not done" in q_lower:
                assert correct_value == str(not_done_count)

    def test_generates_questions_for_each_app(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        apps_covered = {q.app for q in questions}
        assert "todo" in apps_covered
        assert "calendar" in apps_covered
        assert "messenger" in apps_covered
        assert "map" in apps_covered

    def test_seed_reproducibility(self, client_and_config):
        client, config = client_and_config
        q1 = generate_questions_from_client(client, config, seed=123)
        q2 = generate_questions_from_client(client, config, seed=123)
        assert len(q1) == len(q2)
        for a, b in zip(q1, q2):
            assert a.question == b.question
            assert a.choices == b.choices
            assert a.correct == b.correct

    def test_different_seeds_produce_different_order(self, client_and_config):
        client, config = client_and_config
        q1 = generate_questions_from_client(client, config, seed=1)
        q2 = generate_questions_from_client(client, config, seed=999)
        if len(q1) > 3:
            choices_differ = any(
                a.choices != b.choices for a, b in zip(q1, q2)
            )
            assert choices_differ

    def test_filter_by_app(self, client_and_config):
        client, config = client_and_config
        todo_only = generate_questions_from_client(client, config, apps=["todo"])
        assert all(q.app == "todo" for q in todo_only)
        assert len(todo_only) > 0

    def test_format_as_prompt(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        for q in questions:
            prompt = q.format_as_prompt()
            assert q.question in prompt
            assert "A)" in prompt
            assert "B)" in prompt

    def test_questions_to_json(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        json_output = questions_to_json(questions)
        assert len(json_output) == len(questions)
        for entry in json_output:
            assert "question" in entry
            assert "choices" in entry
            assert "correct" in entry
            assert "formatted" in entry
        json_str = json.dumps(json_output)
        assert len(json_str) > 0

    def test_from_saved_state_file(self):
        state_path = Path(__file__).parent / "states" / "initial_state.json"
        if not state_path.exists():
            pytest.skip("initial_state.json not found")
        with open(state_path) as f:
            state = json.load(f)
        questions = generate_questions_from_state(state)
        assert len(questions) > 0

    def test_category_coverage(self, client_and_config):
        client, config = client_and_config
        questions = generate_questions_from_client(client, config)
        categories = {q.category for q in questions}
        assert "element_counting" in categories
        assert "element_content" in categories
        assert "element_state" in categories
        assert "element_identification" in categories
        assert "element_interaction" in categories
        assert "navigation" in categories
