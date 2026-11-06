"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

import sys

import hydra
from omegaconf import DictConfig
from open_apps.parallel_launcher import launch_parallel, normalize_cli_args
from dotenv import load_dotenv

load_dotenv()


@hydra.main(
    version_base=None, config_path="config", config_name="config_parallel_tasks"
)
def main(config: DictConfig) -> None:
    """Main entry point for benchmark launcher."""
    launch_parallel(config)


if __name__ == "__main__":
    sys.argv[1:] = normalize_cli_args(sys.argv[1:])
    main()
