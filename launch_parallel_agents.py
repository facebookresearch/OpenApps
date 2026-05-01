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
from open_apps.launcher import AgentLauncher


def run_task(config: DictConfig) -> None:
    from open_apps.apps.start_page.main import app  # need to import apps to serve

    launcher = AgentLauncher(config)
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

    cluster = config.get("cluster")

    if cluster == "local":
        run_locally(config, parallel_configs, num_jobs)
    elif cluster == "slurm":
        run_via_slurm(config, parallel_configs, num_jobs, sweep_dir)
    else:
        raise ValueError(f"cluster= {cluster} not supported for parallel agents")


def run_via_slurm(config, parallel_configs, num_jobs, sweep_dir):
    cluster = config.get("cluster")
    executor = submitit.AutoExecutor(folder=sweep_dir, cluster=cluster)
    if hasattr(config, "slurm_sweep_launcher"):
        executor.update_parameters(**config.slurm_sweep_launcher)
    jobs = []
    with executor.batch():
        for i, job_config in enumerate(parallel_configs):
            job_config.job_id = i
            job_config.num_jobs = num_jobs
            job = executor.submit(run_task, job_config)
            jobs.append(job)

    print(f"Submitting {len(jobs)} parallel agent tasks via cluster={cluster}.")
    print("First Job ID:", jobs[0].job_id)


def run_locally(config, parallel_configs, num_jobs):
    # Run each job sequentially in the parent process so output streams
    # directly to the terminal instead of being captured by submitit
    # subprocess log files.
    base_logs_dir = str(config.logs_dir)
    for i, job_config in enumerate(parallel_configs):
        job_config.job_id = i
        job_config.num_jobs = num_jobs
        job_config.logs_dir = f"{base_logs_dir}/{i}"
        job_config.databases_dir = f"{job_config.logs_dir}/databases"
        print(f"\n=== Running local job {i + 1}/{num_jobs} (job_id={i}) ===")
        run_task(job_config)
    print(f"\nFinished {num_jobs} local jobs.")


if __name__ == "__main__":
    main()
