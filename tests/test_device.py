"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Unit tests for the device axis (``config/device/`` + ``open_apps.device``).

Three things are worth locking down, and all three fail quietly rather than
loudly:

* **composition** -- every device file resolves, and the viewport reaches both
  consumers (the browser, via the env args; the apps, via ``apps.device``). A
  broken interpolation here means a sweep runs at the wrong size and reports a
  number that looks perfectly plausible;
* **fallback** -- a config with no device node, or an unknown form factor,
  degrades to desktop instead of raising. The apps are handed a config that
  may predate this group (a saved ``config.yaml`` from an earlier run);
* **emulation** -- ``DeviceEnvArgs`` actually forwards the Playwright flags.
  Without them ``device=phone`` is a narrow window and nothing more, which is
  exactly the state this axis was added to get out of.
"""

from dataclasses import fields
from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf

from open_apps import device

CONFIG_DIR = str((Path(__file__).resolve().parent.parent / "config").resolve())

#: Every option in the group, and the size each one asks for.
DEVICES = {
    "desktop": ([1920, 1080], "desktop"),
    "laptop": ([1280, 800], "desktop"),
    "tablet": ([820, 1180], "tablet"),
    "phone": ([390, 844], "phone"),
}


def compose_config(overrides: list[str]):
    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=CONFIG_DIR):
        return compose(config_name="config", overrides=overrides)


class TestConfigGroup:

    @pytest.mark.parametrize("name", sorted(DEVICES))
    def test_every_device_composes(self, name):
        viewport, form_factor = DEVICES[name]
        cfg = compose_config([f"device={name}"])
        assert cfg.device.name == name
        assert cfg.device.form_factor == form_factor
        assert list(cfg.device.viewport) == viewport

    @pytest.mark.parametrize("name", sorted(DEVICES))
    def test_viewport_reaches_the_browser(self, name):
        viewport, _ = DEVICES[name]
        cfg = compose_config([f"device={name}"])
        resolution = cfg.browsergym_env_args.task_kwargs.screen_resolution
        assert list(resolution) == viewport

    @pytest.mark.parametrize("name", sorted(DEVICES))
    def test_device_reaches_the_apps(self, name):
        # The web server is handed cfg.apps and nothing else, so a device that
        # does not appear under it cannot affect a single rendered page.
        _, form_factor = DEVICES[name]
        cfg = compose_config([f"device={name}"])
        assert cfg.apps.device.form_factor == form_factor
        assert device.form_factor(cfg.apps) == form_factor

    def test_default_device_keeps_the_historical_viewport(self):
        # 1920x1080 is what browsergym_env_args/default.yaml hardcoded before
        # the group existed; a default run has to be untouched by all of this.
        cfg = compose_config([])
        assert list(cfg.browsergym_env_args.task_kwargs.screen_resolution) == [1920, 1080]

    def test_screenshot_preset_still_takes_a_device(self):
        cfg = compose_config(["browsergym_env_args=screenshot", "device=laptop"])
        assert list(cfg.browsergym_env_args.task_kwargs.screen_resolution) == [1280, 800]
        assert cfg.browsergym_env_args.max_steps == 25

    def test_scale_factor_is_one_everywhere(self):
        # Screenshots are captured in device pixels and actions dispatched in
        # CSS pixels with nothing rescaling between them, so anything but 1
        # silently offsets every grounded click. See config/device/desktop.yaml.
        for name in DEVICES:
            cfg = compose_config([f"device={name}"])
            assert cfg.device.device_scale_factor == 1, name


class TestResolution:

    def test_missing_device_falls_back_to_desktop(self):
        assert device.form_factor(OmegaConf.create({})) == "desktop"
        assert device.form_factor(None) == "desktop"
        assert device.viewport(None) == (1920, 1080)

    def test_unknown_form_factor_falls_back_to_desktop(self):
        cfg = OmegaConf.create({"device": {"form_factor": "watch"}})
        assert device.form_factor(cfg) == "desktop"

    def test_partial_device_is_filled_in(self):
        resolved = device.as_dict({"form_factor": "phone"})
        assert resolved["form_factor"] == "phone"
        assert resolved["viewport"] == [1920, 1080]  # untouched default
        assert resolved["is_mobile"] is False

    def test_unknown_keys_survive(self):
        # A device file may carry fields for a consumer this module has not
        # been told about yet.
        assert device.as_dict({"orientation": "landscape"})["orientation"] == "landscape"

    def test_plain_dicts_and_configs_agree(self):
        cfg = compose_config(["device=phone"])
        assert device.as_dict(cfg.device) == device.as_dict(
            OmegaConf.to_container(cfg.device, resolve=True)
        )


class TestContextKwargs:

    def test_desktop_asks_for_nothing(self):
        # An empty dict lets the caller leave the stock BrowserGym path alone.
        assert device.context_kwargs(compose_config(["device=desktop"]).device) == {}

    def test_phone_is_mobile_and_touch(self):
        kwargs = device.context_kwargs(compose_config(["device=phone"]).device)
        assert kwargs == {"is_mobile": True, "has_touch": True}

    def test_scale_and_user_agent_pass_through_when_set(self):
        kwargs = device.context_kwargs(
            {"device_scale_factor": 3, "user_agent": "Mozilla/5.0 (iPhone)"}
        )
        assert kwargs["device_scale_factor"] == 3
        assert kwargs["user_agent"] == "Mozilla/5.0 (iPhone)"


class TestDeviceEnvArgs:
    """The subclass that gets those flags into the Playwright context."""

    def make(self, device_cfg):
        from open_apps.agent.env_args import DeviceEnvArgs

        return DeviceEnvArgs(task_name="none", device=device_cfg)

    class FakeEnv:
        """Stands in for the gym env: only ``pw_context_kwargs`` matters."""

        def __init__(self):
            self.pw_context_kwargs = {}

        @property
        def unwrapped(self):
            return self

    def test_it_is_still_env_args(self):
        from browsergym.experiments import EnvArgs

        from open_apps.agent.env_args import DeviceEnvArgs

        assert issubclass(DeviceEnvArgs, EnvArgs)
        # Every field BrowserGym's own loop reads has to survive the subclass.
        assert {f.name for f in fields(EnvArgs)} <= {f.name for f in fields(DeviceEnvArgs)}

    def test_phone_flags_reach_the_context(self, monkeypatch):
        env = self.FakeEnv()
        args = self.make({"is_mobile": True, "has_touch": True})
        monkeypatch.setattr(
            type(args).__mro__[1], "make_env", lambda self, *a, **k: env
        )
        assert args.make_env(None, None) is env
        assert env.pw_context_kwargs == {"is_mobile": True, "has_touch": True}

    def test_desktop_leaves_the_context_alone(self, monkeypatch):
        env = self.FakeEnv()
        args = self.make(None)
        monkeypatch.setattr(
            type(args).__mro__[1], "make_env", lambda self, *a, **k: env
        )
        args.make_env(None, None)
        assert env.pw_context_kwargs == {}

    def test_a_browsergym_without_the_attribute_warns_and_survives(
        self, monkeypatch, caplog
    ):
        class Bare:
            unwrapped = None

        bare = Bare()
        bare.unwrapped = bare
        args = self.make({"is_mobile": True})
        monkeypatch.setattr(
            type(args).__mro__[1], "make_env", lambda self, *a, **k: bare
        )
        assert args.make_env(None, None) is bare
        assert "device emulation" in caplog.text


class TestExperimentBundles:
    """Device + layout + episode settings that only make sense together."""

    def test_phone_bundle_pairs_the_device_with_the_layout(self):
        # `device=phone` alone leaves the start page on the gallery layout,
        # which has no phone variant -- the interesting half would be missing.
        cfg = compose_config(["+experiment=phone"])
        assert cfg.device.name == "phone"
        assert cfg.apps.start_page.layout == "desktop"
        assert cfg.apps.start_page.desktop.variants.phone == "home_screen"
        assert cfg.browsergym_env_args.max_steps == 25

    def test_a_bundle_still_loses_to_the_command_line(self):
        cfg = compose_config(["+experiment=screenshot_agent", "device=phone"])
        assert cfg.device.name == "phone"
        assert list(cfg.browsergym_env_args.task_kwargs.screen_resolution) == [390, 844]
