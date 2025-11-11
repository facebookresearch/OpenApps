"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Launches agent in OpenApps to perform a given single task.
"""
# Third-party imports
import hydra
from omegaconf import DictConfig

# Project-specific imports
from open_apps.apps.start_page.main import app  # need to import apps to serve
from open_apps.launcher import AgentLauncher


@hydra.main(version_base=None, config_path="config", config_name="config")
def main(config: DictConfig):
    launcher = AgentLauncher(config)
    launcher.launch()


if __name__ == "__main__":
    main()
