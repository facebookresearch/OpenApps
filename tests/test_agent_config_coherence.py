"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Coherence checks between an agent config's prompt and its actual action space.

An agent yaml can advertise an action in ``prompt_txt.action_prompt`` that the
runtime cannot execute: the action has to survive ``flexible_parser`` *and* be
present in the ``HighLevelActionSet`` built from ``custom_actions``. When it
isn't, the model burns a step per attempt and the episode silently scores 0.
That is how ``wait()`` shipped in the UI-TARS-derived configs -- advertised in
the prompt, absent from ``custom_actions``, and not remapped by the parser.
"""

import hydra
import pytest
from hydra import compose, initialize

from open_apps.agent.utils import flexible_parser


def agent_args(agent_name: str):
    with initialize(version_base=None, config_path="../config/"):
        config = compose(config_name="config", overrides=[f"agent={agent_name}"])
    return hydra.utils.instantiate(config.agent)


def executable_actions(args) -> set[str]:
    """Action names the built HighLevelActionSet can actually run."""
    action_set = args.make_flags().action.action_set.make_action_set()
    return set(action_set.action_set.keys())


def parsed_action(response: str) -> str:
    """Run a model response through the agent's parsing path."""
    return flexible_parser(response)["action"]


GEMMA_SOM = "gemma-4-31B-computer-use"
GEMMA_COORDS = "gemma-4-31B-coords"


class TestGemmaSomConfig:
    """The screenshot-only + set-of-marks Gemma config."""

    @pytest.fixture(scope="class")
    def args(self):
        return agent_args(GEMMA_SOM)

    def test_observation_is_screenshot_only_with_marks(self, args):
        assert args.use_screenshot
        assert args.save_som, "bid actions need the annotated screenshot"
        assert not args.use_axtree
        assert not args.use_html
        # save_som is what actually reaches the prompt builder as use_som.
        assert args.make_flags().obs.use_som

    def test_action_space_is_bid_based(self, args):
        assert executable_actions(args) == {
            "click",
            "fill",
            "select_option",
            "scroll",
            "noop",
        }

    def test_no_coordinate_actions_are_reachable(self, args):
        """Gemma is not prompted to ground in pixels, so don't offer it."""
        assert not {"mouse_click", "mouse_dblclick"} & executable_actions(args)

    def test_temperature_is_deterministic(self, args):
        assert args.temperature == 0

    @pytest.mark.parametrize(
        "response,expected",
        [
            ('<think>t</think><action>click("96")</action>', 'click("96")'),
            (
                '<think>t</think><action>fill("92", "a\nb")</action>',
                'fill("92", "a\nb")',
            ),
            (
                '<think>t</think><action>select_option("12", "python")</action>',
                'select_option("12", "python")',
            ),
            # scroll( is a prefix the UI-TARS remap also matches; it must fall
            # through untouched when there is no direction= kwarg.
            ("<think>t</think><action>scroll(0, 400)</action>", "scroll(0, 400)"),
            (
                "<think>t</think><action>noop(wait_ms=5000)</action>",
                "noop(wait_ms=5000)",
            ),
        ],
    )
    def test_parser_passes_bid_actions_through_unmangled(self, response, expected):
        assert parsed_action(response) == expected

    def test_every_advertised_action_is_executable(self, args):
        """The prompt must not name an action the action set cannot run."""
        prompt = args.prompt_txt.action_prompt
        runnable = executable_actions(args)
        advertised = {
            line.split("(")[0].strip()
            for line in prompt.splitlines()
            if "(" in line and not line.startswith(" ") and not line.startswith("-")
        }
        assert advertised, "could not extract any action names from action_prompt"
        assert advertised <= runnable, (
            f"action_prompt advertises {sorted(advertised - runnable)}, which "
            f"the action set cannot execute (runnable: {sorted(runnable)})"
        )

    def test_wait_is_not_advertised(self, args):
        """``wait()`` is neither remapped by the parser nor in action_map."""
        assert "wait()" not in args.prompt_txt.action_prompt

    def test_prompt_does_not_ask_for_coordinates(self, args):
        """Mixed guidance (bids + coordinates) is what produced 0% before."""
        texts = [
            args.prompt_txt.action_prompt,
            args.prompt_txt.action_abstract_example,
            args.prompt_txt.action_concrete_example,
            args.prompt_txt.think_abstract_example,
        ]
        for text in texts:
            assert "coordinate" not in str(text).lower()


class TestGemmaCoordsConfig:
    """The screenshot-only + pixel-coordinate Gemma config.

    Its whole premise is that the declared coordinate grid, the prompt, and
    ``coord_scale`` agree. If they drift apart the clicks land off-target and
    the episode scores 0 without raising, so pin all three together.
    """

    @pytest.fixture(scope="class")
    def args(self):
        return agent_args(GEMMA_COORDS)

    def test_observation_is_screenshot_only_without_marks(self, args):
        assert args.use_screenshot
        assert not args.save_som, "this config measures raw pixel grounding"
        assert not args.use_axtree
        assert not args.use_html

    def test_action_space_is_coordinate_based(self, args):
        assert "mouse_click" in executable_actions(args)
        assert "click" not in executable_actions(args), "bid actions would be unusable without marks"

    def test_coord_scale_matches_the_grid_declared_in_the_prompt(self, args):
        assert args.coord_scale == 1000
        assert "1000x1000" in args.prompt_txt.system_prompt

    def test_coord_scale_reaches_the_action_parser(self, args):
        agent = args.make_agent()
        assert agent.action_parser.coord_scale == 1000

    def test_model_coordinates_are_converted_to_viewport_pixels(self, args):
        """End-to-end: a grid coordinate must come out as a viewport pixel."""
        parser = args.make_agent().action_parser
        response = "<think>t</think><action>mouse_click(x=500, y=500)</action>"
        # 1280x800 is the screenshot env preset this config is paired with.
        out = parser.parse(response, viewport=(1280, 800))
        assert out["action"] == "mouse_click(x=640, y=400)"

    def test_every_advertised_action_is_executable(self, args):
        prompt = args.prompt_txt.action_prompt
        runnable = executable_actions(args)
        advertised = {
            line.split("(")[0].strip()
            for line in prompt.splitlines()
            if "(" in line and not line.startswith(" ") and not line.startswith("-")
        }
        assert advertised, "could not extract any action names from action_prompt"
        assert advertised <= runnable, (
            f"action_prompt advertises {sorted(advertised - runnable)}, which "
            f"the action set cannot execute (runnable: {sorted(runnable)})"
        )

    def test_temperature_is_deterministic(self, args):
        assert args.temperature == 0


class TestScreenshotEnvArgs:
    def test_screenshot_preset_widens_the_step_budget(self):
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config", overrides=["browsergym_env_args=screenshot"]
            )
        env = config.browsergym_env_args
        assert env.max_steps == 25

    def test_the_bundle_pairs_it_with_a_laptop_window(self):
        # The 1280x800 this preset used to hardcode now lives in the `device`
        # group, and a group config cannot select another group's option --
        # hence the experiment bundle. Both halves have to arrive together or
        # the screenshot the model reads is 1920x1080 squashed by its image
        # processor, which is the failure this pairing exists to avoid.
        with initialize(version_base=None, config_path="../config/"):
            config = compose(
                config_name="config", overrides=["+experiment=screenshot_agent"]
            )
        env = config.browsergym_env_args
        assert env.max_steps == 25
        assert config.device.name == "laptop"
        assert list(env.task_kwargs.screen_resolution) == [1280, 800]
