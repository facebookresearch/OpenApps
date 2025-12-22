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


@hydra.main(
    version_base=None, config_path="config", config_name="config_parallel_tasks"
)
def main(config: DictConfig) -> None:
    """Main entry point for benchmark launcher"""
    # print("sweep configs num is", len(sweep_configs))

    parallel_configs: list[DictConfig] = hydra.utils.instantiate(
        config.parallel_tasks
    ).create_configs(default_config=config)
    num_jobs = len(parallel_configs)

    # get parent dir of logs_dir
    sweep_root_logs_dir = Path(config.logs_dir).parent
    sweep_dir = os.path.join(sweep_root_logs_dir, "sweep")
    print("Logging sweep to ", sweep_dir)

    executor = submitit.AutoExecutor(folder=sweep_dir)
    executor.update_parameters(**config.slurm_sweep_launcher)
    jobs = []
    with executor.batch():
        for i, job_config in enumerate(parallel_configs):
            job_config.job_id = i
            job_config.num_jobs = num_jobs
            job = executor.submit(run_task, job_config)
            jobs.append(job)

    print(f"Submitting {len(jobs)} parallel agent tasks the cluster.")
    print("First Job ID:", jobs[0].job_id)


if __name__ == "__main__":
    main()
