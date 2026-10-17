"""Defines logic for running multiple tasks in parallel."""

from abc import ABC, abstractmethod
from typing import List, Union
from omegaconf import DictConfig
from itertools import product
import hydra

# Sentinel value for task_names that expands to every task in the tasks config.
ALL_TASKS = "all"


def _is_device_override(override: str) -> bool:
    """``device=phone`` / ``device.viewport=[..]`` (with or without ``+``/``++``)."""
    key = str(override).lstrip("+").split("=", 1)[0]
    return key == "device" or key.startswith(("device.", "device/"))


class ParallelTasksConfig(ABC):
    """Defines method for creating multiple experiment configs to be run in parallel."""

    @abstractmethod
    def create_configs(self, default_config: DictConfig) -> list[DictConfig]:
        """Creates a list of hydra configs for each experiment.

        Returns:
            List[DictConfig]: A list of configuration objects for different experiments

        Raises:
            NotImplementedError: If the child class doesn't implement this method
        """
        raise NotImplementedError


class AppVariationParallelTasksConfig(ParallelTasksConfig):
    """Runs each task per app variation"""

    def __init__(
        self,
        app_variations: list[list[str]],
        task_names: Union[str, list[str]],
    ) -> None:
        self.app_variations = app_variations
        self.task_names = task_names

    def _resolve_task_names(self, default_config: DictConfig) -> list[str]:
        """Resolves task_names to a concrete list of task names.

        Accepts either the string "all" (expands to every task in the loaded
        tasks config) or an explicit list of task names to run a subset.
        """
        if self.task_names == ALL_TASKS:
            return list(default_config.tasks.keys())
        if isinstance(self.task_names, str):
            raise ValueError(
                f"task_names must be a list of task names or '{ALL_TASKS}', "
                f"got string {self.task_names!r}."
            )
        return list(self.task_names)

    def create_configs(self, default_config: DictConfig) -> list[DictConfig]:
        task_names = self._resolve_task_names(default_config)
        configs = []
        for app_variation, task_name in product(self.app_variations, task_names):
            config = default_config.copy()
            config.task_name = task_name

            overrides = app_variation + [f"task_name={task_name}"]
            config_with_overrides = hydra.compose(
                config_name="config", overrides=overrides
            )
            config.apps = config_with_overrides.apps
            # Device is a root-level group, not under `apps`, so the swap above
            # drops it; `apps.device`, the viewport and the input model all
            # interpolate `${device}` from the root, so copying the node is all
            # it takes to make device a cell axis like theme or content.
            if any(_is_device_override(o) for o in app_variation):
                config.device = config_with_overrides.device
            # for logging, track app overrides
            config.app_overrides = app_variation
            configs.append(config)
        return configs
