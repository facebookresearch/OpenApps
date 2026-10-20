"""Defines logic for running multiple tasks in parallel."""

from abc import ABC, abstractmethod
from typing import List, Sequence, Union
from omegaconf import DictConfig, OmegaConf, open_dict
from itertools import product
import hydra

# Sentinel value for task_names that expands to every task in the tasks config.
ALL_TASKS = "all"

#: Keys every run takes from the parent config rather than from its own
#: compose. The launcher settles them before fanning out -- `record_video_dir`
#: is pinned to an absolute path by `freeze_recording_dir` -- and `${now:...}`
#: is not cached, so a fresh compose would stamp each run with its own time.
#: Copied unresolved, so `${job_id}` in a SLURM `logs_dir` still resolves per job.
INHERITED_KEYS = ("logs_dir", "databases_dir", "record_video_dir")


class ParallelTasksConfig(ABC):
    """Defines method for creating multiple experiment configs to be run in parallel."""

    @abstractmethod
    def create_configs(
        self, default_config: DictConfig, cli_overrides: Sequence[str] = ()
    ) -> list[DictConfig]:
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

    def create_configs(
        self,
        default_config: DictConfig,
        cli_overrides: Sequence[str] = (),
        config_name: str = "config_parallel_tasks",
    ) -> list[DictConfig]:
        """One config per (app variation, task), composed from scratch.

        Each run is composed from the command-line overrides plus its own
        variation, rather than copied from the parent with only `apps`
        swapped in. Swapping a subtree drops every command-line override
        inside it: `apps.onlineshop.enable=True` reached the parent config
        and silently vanished from every run. The variation comes last, so it
        wins over a command-line value for the same key.

        Args:
            default_config: The parent config, as `@hydra.main` composed it.
            cli_overrides: The overrides it was composed with
                (`HydraConfig.get().overrides.task`).
            config_name: The root config to compose each run from.
        """
        task_names = self._resolve_task_names(default_config)
        raw = OmegaConf.to_container(default_config, resolve=False)
        inherited = {key: raw[key] for key in INHERITED_KEYS if key in raw}
        configs = []
        for app_variation, task_name in product(self.app_variations, task_names):
            overrides = [*cli_overrides, *app_variation, f"task_name={task_name}"]
            config = hydra.compose(config_name=config_name, overrides=overrides)
            with open_dict(config):
                for key, value in inherited.items():
                    config[key] = value
                config.task_name = task_name
                # for logging, track app overrides
                config.app_overrides = app_variation
            configs.append(config)
        return configs
