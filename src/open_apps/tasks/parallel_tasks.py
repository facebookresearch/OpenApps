"""Defines logic for running multiple tasks in parallel."""

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import List, Sequence, Union
from omegaconf import DictConfig, OmegaConf, open_dict
from itertools import product
import hydra

# Sentinel value for task_names that expands to every task in the tasks config.
ALL_TASKS = "all"

#: Paths every job inherits from the parent rather than recomputing them.
#: The parallel launcher resolves logs_dir once and creates a per-run folder;
#: create_job_configs then assigns job-specific logs and databases directories.
#: freeze_recording_dir pins the common recordings path before fan-out.
#: Other inherited interpolations stay unresolved until their job uses them.
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
    """Runs each task per app variation, for each task file, agent and repeat.

    Args:
        repeats: Runs per job, to measure an agent's run-to-run variance. The
            seed is not varied: a different seed can change the environment's
            own randomness, and a repeat is meant to measure the agent's.
    """

    def __init__(
        self,
        app_variations: list[list[str]],
        task_names: Union[str, list[str]],
        task_configs: Sequence[str] | None = None,
        agent_configs: Sequence[str] | None = None,
        repeats: int = 1,
    ) -> None:
        self.app_variations = app_variations
        self.task_names = task_names
        self.task_configs = list(task_configs) if task_configs is not None else None
        self.agent_configs = list(agent_configs) if agent_configs is not None else None
        if self.task_configs == [] or self.agent_configs == []:
            raise ValueError("Task and agent config selections must not be empty")
        if int(repeats) < 1:
            raise ValueError(f"repeats must be at least 1, got {repeats!r}")
        self.repeats = int(repeats)

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
        if self.task_configs is not None or self.agent_configs is not None:
            return self._repeated(
                self._create_matrix_configs(default_config, cli_overrides, config_name)
            )
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
        return self._repeated(configs)

    def _create_matrix_configs(
        self,
        default_config: DictConfig,
        cli_overrides: Sequence[str],
        config_name: str,
    ) -> list[DictConfig]:
        raw = OmegaConf.to_container(default_config, resolve=False)
        inherited = {key: raw[key] for key in INHERITED_KEYS if key in raw}
        configs = []
        found_tasks = set()
        for task_config, agent_config, app_variation in product(
            self.task_configs or [None], self.agent_configs or [None], self.app_variations
        ):
            overrides = [*cli_overrides, *app_variation]
            if task_config is not None:
                overrides.append(f"tasks={task_config}")
            if agent_config is not None:
                overrides.append(f"agent={agent_config}")
            parent = hydra.compose(config_name=config_name, overrides=overrides)
            task_names = self._resolve_task_names(parent)
            for task_name in task_names:
                if task_name not in parent.tasks:
                    continue
                found_tasks.add(task_name)
                config = deepcopy(parent)
                with open_dict(config):
                    for key, value in inherited.items():
                        config[key] = value
                    config.task_name = task_name
                    config.app_overrides = app_variation
                    config.task_config_name = task_config
                    config.agent_config_name = agent_config
                configs.append(config)
        if self.task_names != ALL_TASKS:
            missing_tasks = set(self.task_names) - found_tasks
            if missing_tasks:
                raise ValueError(f"Unknown task names: {sorted(missing_tasks)}")
        return configs

    def _repeated(self, configs: list[DictConfig]) -> list[DictConfig]:
        """Each config ``repeats`` times, side by side, numbered by ``repeat``.

        ``repeat`` stays None for a single run, so a run's name only grows a
        ``-r<N>`` suffix when there is something to tell apart.
        """
        out = []
        for config in configs:
            for repeat in range(self.repeats):
                copy = deepcopy(config) if repeat else config
                with open_dict(copy):
                    copy.repeat = repeat if self.repeats > 1 else None
                out.append(copy)
        return out
