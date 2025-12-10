"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

import hydra
from omegaconf import DictConfig
from pathlib import Path
from omegaconf import OmegaConf
import submitit
import os


def run_task(config: DictConfig) -> None:
    from open_apps.apps.start_page.main import app  # need to import apps to serve
    from open_apps.launcher import OpenAppsLauncher

    launcher = OpenAppsLauncher(config)
    launcher.launch()


def create_sweep_configs(default_config: DictConfig) -> list[DictConfig]:
    """Creates configs by instantiating each set of app configs + task"""

    # check if sweep cababilties are str or list
    if isinstance(default_config.sweep_capabilities, str):
        default_config.sweep_capabilities = [default_config.sweep_capabilities]

    sweep_configs = []
    for capability in default_config.sweep_capabilities:
        capability_path = Path(
            hydra.utils.get_original_cwd(), f"./config/capability/{capability}.yaml"
        )
        if not capability_path.exists():
            raise ValueError(f"Capability config not found at {capability_path}")
        capability_instance = hydra.utils.instantiate(OmegaConf.load(capability_path))
        config = default_config.copy()
        capability_sweep_configs = capability_instance.create_experiment_configs(config)
        # add capability name for tracking in each individual experiment
        for sweep_config in capability_sweep_configs:
            sweep_config.capability_name = capability
        sweep_configs.extend(capability_sweep_configs)
    return sweep_configs


@hydra.main(version_base=None, config_path="config", config_name="config")
def main(config: DictConfig) -> None:
    """Main entry point for benchmark launcher"""
    # print("sweep configs num is", len(sweep_configs))
    sweep_configs = create_sweep_configs(config)
    num_jobs = len(sweep_configs)

    if not config.launch_jobs:
        print(
            f"Not launching sweep, but there are {len(sweep_configs)} capabilities specified in config"
        )
        return

    # get parent dir of logs_dir
    sweep_root_logs_dir = Path(config.logs_dir).parent
    sweep_dir = os.path.join(sweep_root_logs_dir, "sweep")
    print("Logging sweep to ", sweep_dir)

    executor = submitit.AutoExecutor(folder=sweep_dir)
    executor.update_parameters(**config.slurm_sweep_launcher)
    jobs = []
    with executor.batch():
        for i, job_config in enumerate(sweep_configs):
            job_config.job_id = i
            job_config.num_jobs = num_jobs
            job = executor.submit(run_task, job_config)
            jobs.append(job)

    print(f"Submitting {len(jobs)} jobs to the cluster.")
    print("First Job ID:", jobs[0].job_id)


if __name__ == "__main__":
    main()
