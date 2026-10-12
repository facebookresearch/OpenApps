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

from open_apps.apps.start_page import main as start_page_main
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
    # A dedicated temp dir, not the shared base temp other modules seed into.
    logs_dir = str(tmpdir_factory.mktemp("test_questions"))
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


class TestStartPageLayoutAwareness:
    """The start page ships two compositions and the default is `desktop`.

    A template that describes the tile gallery is simply false against the
    desktop shell, and generating it raises nothing -- the corpus just goes
    quietly wrong. These pin the branch so flipping the default layout, or
    adding a third composition, fails here instead.
    """

    @staticmethod
    def _start_page_questions(layout):
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config",
                overrides=[f"apps/start_page/layout={layout}"],
            )
        # Attach the global config the way a live run does: the online shop is
        # gated on Java 21 from a sibling node the start page cannot see, and
        # without this it would count as a pinned app with no tile.
        start_page_main.app.config = config.apps
        return [
            q
            for q in generate_questions_from_state({}, config)
            if q.app == "start_page"
        ], config

    def test_tile_questions_only_exist_for_the_gallery(self):
        desktop, _ = self._start_page_questions("desktop")
        assert desktop, "the default layout must still get start-page coverage"
        for q in desktop:
            assert "tile" not in q.question.lower(), q.question

        gallery, _ = self._start_page_questions("gallery")
        assert any("tile" in q.question.lower() for q in gallery)

    def test_shell_questions_only_exist_for_the_desktop(self):
        gallery, _ = self._start_page_questions("gallery")
        for q in gallery:
            assert "toolbar" not in q.question.lower(), q.question
            assert "pinned to the desktop" not in q.question.lower(), q.question

    @pytest.mark.parametrize(
        "layout,expected",
        [
            ("gallery", "Welcome to OpenApps!"),
            ("desktop", "An open source environment for digital agents"),
        ],
    )
    def test_headline_question_follows_the_layout(self, layout, expected):
        """The desktop shell overrides the start page's own headline copy."""
        questions, _ = self._start_page_questions(layout)
        headline_qs = [q for q in questions if "headline" in q.question.lower()]
        assert len(headline_qs) == 1
        q = headline_qs[0]
        assert q.choices[q.correct] == expected

    def test_only_apps_actually_in_the_dom_are_asked_about(self):
        """The desktop shell renders only the pinned apps.

        Everything else is behind the launcher popover and absent from the
        initial HTML, so naming one as "shown" would be wrong.
        """
        from open_apps.apps.start_page.main import _desktop_config, resolve_pinned

        questions, config = self._start_page_questions("desktop")
        sp = config.apps.start_page
        pinned_keys = resolve_pinned(_desktop_config(sp), sp)
        unpinned_titles = {
            a.title
            for k, a in sp.apps.items()
            if k not in pinned_keys and a.get("title")
        }
        shown_as_correct = {
            q.choices[q.correct]
            for q in questions
            if "no desktop shortcut" not in q.question
        }
        assert not (unpinned_titles & shown_as_correct)

    def test_unpinning_creates_the_launcher_question(self):
        """`desktop.unpinned` is the knob that makes the launcher load-bearing,
        so the corpus should be able to ask about it."""
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config",
                overrides=[
                    "apps/start_page/layout=desktop",
                    "apps.start_page.desktop.unpinned=[messages]",
                ],
            )
        start_page_main.app.config = config.apps
        questions = generate_questions_from_state({}, config)
        launcher_qs = [q for q in questions if "no desktop shortcut" in q.question]
        assert len(launcher_qs) == 1
        q = launcher_qs[0]
        assert q.choices[q.correct] == config.apps.start_page.apps.messages.title

    def test_everything_pinned_asks_a_count_instead(self):
        """With no unpinned app there is no wrong answer to offer, so the
        "which is pinned" question would have been unanswerable."""
        questions, _ = self._start_page_questions("desktop")
        assert not any("pinned to the desktop" in q.question for q in questions)
        counts = [q for q in questions if "How many app shortcuts" in q.question]
        assert len(counts) == 1
        # Seven: todo, calendar, messages, maps, code editor, the shop (on by
        # default now that it needs no JDK) and OpenBanking. The UI Library's
        # tile is disabled by default.
        assert counts[0].choices[counts[0].correct] == "7"
