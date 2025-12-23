"""Defines logic for running multiple tasks in parallel."""

from abc import ABC, abstractmethod
from typing import List
from omegaconf import DictConfig
from itertools import product
import hydra


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

    def __init__(self, app_variations: list[list[str]], task_names: list[str]) -> None:
        self.app_variations = app_variations
        self.task_names = task_names

    def create_configs(self, default_config: DictConfig) -> list[DictConfig]:
        configs = []
        for app_variation, task_name in product(self.app_variations, self.task_names):
            config = default_config.copy()
            config.task_name = task_name

            overrides = app_variation + [f"task_name={task_name}"]
            config_with_overrides = hydra.compose(
                config_name="config", overrides=overrides
            )
            config.apps = config_with_overrides.apps
            # for logging, track app overrides
            config.app_overrides = app_variation
            configs.append(config)
        return configs
