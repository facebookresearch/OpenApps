"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Orchestrates the configs to launch the web envirnoment,
task, and agent.
"""

# Standard library imports
from subprocess import PIPE
import subprocess
from pathlib import Path
import shutil
import os
import socket
import signal

# Third-party imports
from omegaconf import DictConfig, OmegaConf
from fasthtml.common import serve
import wandb
import yaml
import git
from typing import Optional
from time import sleep
import hydra
import urllib.request
import time
import urllib.parse  # Add this import
from open_apps.utils import merge_plus_keys

# Project-specific imports
from open_apps.apps.start_page.main import (
    initialize_routes_and_configure_task,
)
import socket
import getpass
from killport import kill_ports
import random

try:
    # Register the custom 'now' resolver
    OmegaConf.register_resolver(
        "now",
        lambda format_str="%Y-%m-%d_%H-%M-%S": datetime.now().strftime(format_str),
    )
except AssertionError:
    # resolver already registered, ignore
    pass


class OpenAppsLauncher:
    def __init__(self, config: DictConfig):
        self.config = config
        self.web_app_host = "localhost"
        OmegaConf.resolve(self.config)

        # Merge any '+' prefixed keys with their base keys
        self.config = merge_plus_keys(self.config)
        random.seed(self.config.seed)

        self.web_app_port = self.pick_empty_port()
        print(f"Using port {self.web_app_port} for the web app")
        self.web_app_url = f"http://{self.web_app_host}:{self.web_app_port}"
        print("Web app hostname is: ", self.web_app_url)
        current_logs_dir_value = self.config.logs_dir

        if hydra.core.hydra_config.HydraConfig.initialized():
            # In a Hydra context, to_absolute_path resolves relative to Hydra's original working directory.
            absolute_logs_dir = str(
                Path(hydra.utils.to_absolute_path(current_logs_dir_value)).resolve()
            )
        else:
            # Outside a Hydra context (e.g., direct instantiation for tests), resolve relative to the current working directory.
            absolute_logs_dir = str(Path(current_logs_dir_value).resolve())

        self.config.logs_dir = absolute_logs_dir

        if self.config.get("apps"):
            for app_name, app_config in self.config.apps.items():
                if (
                    app_config
                    and isinstance(app_config, DictConfig)
                    and app_config.get("database_path")
                ):
                    # The app_config.database_path after initial OmegaConf.resolve might be
                    # something like "experiment_logs/DATE/databases/calendar.db".
                    # We take the filename/last component and join it with the absolute databases_dir.
                    db_file_or_dir_name = Path(app_config.database_path).name
                    app_config.database_path = str(
                        Path(self.config.databases_dir) / db_file_or_dir_name
                    )

        self.config_path = Path(self.config.logs_dir) / "config.yaml"
        self.save_config()
        if config.use_wandb:
            self.wandb_logger = self.setup_wandb()
            self.wandb_logger.log({"web_app_url": self.web_app_url})

    def setup_wandb(self):
        config_dict = yaml.safe_load(OmegaConf.to_yaml(self.config, resolve=True))
        print("logging to: ", config_dict["logs_dir"])
        git_hash = self.get_git_hash()
        config_dict["git_hash"] = git_hash
        job_logs_dir = os.getcwd()
        config_dict["job_logs_dir"] = job_logs_dir
        # increase timeout per wandb folks' suggestion
        # to avoid FAIR cluster network issues
        os.environ["WANDB_INIT_TIMEOUT"] = "60"
        run_name = f"openapps"
        logger = wandb.init(
            **self.config.wandb,
            name=run_name,
            config=config_dict,
            settings={"start_method": "fork"},
        )
        return logger

    def get_git_hash(self) -> Optional[str]:
        try:
            repo = git.Repo(Path(__file__).parent.parent.parent.resolve())
            sha = repo.head.object.hexsha
            print("git hash", sha)
            return sha
        except Exception as e:
            print(e)
            print("not able to find git hash")

    def save_config(self):
        # replaces in place
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        # check if the config file already exists
        if os.path.exists(self.config_path):
            return
        with open(self.config_path, mode="w") as fp:
            OmegaConf.save(config=self.config, f=fp)
        print("configs saved to ", self.config_path)

    def pick_empty_port(self, start_port=5001) -> int:
        """
        Checks if a port in the specified range is available; if not, finds and returns another available port.

        Args:
            port_range (tuple): A range of ports to prioritize (start, end).

        Returns:
            int: An available port.
        """
        port_range = list(range(start_port, start_port + 1000))
        port_range = self._remove_unsafe_ports(port_range)

        for port in port_range:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("", port))  # try binding to the port
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    print(f"Port {port} is available.")
                    return port
                except OSError:
                    print(f"Port {port} is not available.")

        # If no port in the range is available, bind to any available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))  # Bind to any available port
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            available_port = s.getsockname()[1]
            print(
                f"No ports in range {port_range} are available. Using port {available_port}."
            )
            return available_port

    def _remove_unsafe_ports(self, port_range: list[int]) -> list[int]:
        """Removes ports considered unsafe by Chrome."""
        for unsafe_port in [5060, 5061]:
            if unsafe_port in port_range:
                port_range.remove(unsafe_port)
                port_range.append(port_range[-1] + 1)
        return port_range

    def launch_apps(self):
        print("Browser Gym will start at this localhost: ", self.web_app_host)

        initialize_routes_and_configure_task(self.config.apps)

        serve(
            appname="launch",
            reload=False,
            port=self.web_app_port,
            host=self.web_app_host,
        )
        sleep(10)

    def launch_apps_via_shell(self):
        file_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        abs_config_file_path = self.config_path.resolve()
        config_dir_for_subprocess = abs_config_file_path.parent
        config_name_for_subprocess = abs_config_file_path.name

        venv_activate_script = Path(file_dir) / ".venv" / "bin" / "activate"
        command = (
            f"source {venv_activate_script} && "
            f"cd {file_dir} && "
            f"uv run launch.py --config-path {config_dir_for_subprocess} "
            f"--config-name {config_name_for_subprocess} use_wandb=False task.task_kwargs.base_url={self.web_app_url}"
        )
        # TODO: check on this task base_url
        if self.config.apps.onlineshop.enable:
            command += " apps.onlineshop.enable=True"
        print("Launching web app with command: ", command)
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            executable="/bin/bash",
            start_new_session=True,
        )
        sleep(30)
        return process

    def is_app_running(self) -> bool:
        """Checks if the web app is running by sending a request to its URL."""
        try:
            response = urllib.request.urlopen(self.web_app_url, timeout=10)
            if response.status == 200:
                print("Web app main page is running properly...")
            return response.status == 200
        except Exception as e:
            print(f"Web app is not running: {e}")
            return False

    def launch(self):
        self.launch_apps()
        print("experiment finished")


class AgentLauncher(OpenAppsLauncher):
    def __init__(self, config: DictConfig):
        super().__init__(config)

    def _log_agent_results_to_wandb(self, exp_record: dict, exp_result):
        keys_to_save = [
            "n_steps",
            "cum_reward",
            "stats.cum_steps",
            "stats.cum_step_elapsed",
            "stats.max_step_elapsed",
            "stats.cum_agent_elapsed",
            "stats.max_agent_elapsed",
            "terminated",
        ]
        for key in keys_to_save:
            wandb.log({key: exp_record[key]})
        actions_data = [
            [
                i,
                str(step_info.action),
                str(step_info.obs["open_pages_urls"]),
                str(step_info.agent_info.get("think")),
            ]
            for i, step_info in enumerate(exp_result.steps_info)
        ]

        actions_table = wandb.Table(
            data=actions_data, columns=["step", "action", "open_pages_urls", "think"]
        )
        wandb.log({"actions": actions_table})
        print("logging screenshots to wandb")
        for i, screenshot in enumerate(exp_result.screenshots):
            wandb.log(
                {
                    "screenshot": wandb.Image(
                        screenshot,
                        caption=f"Step: {i}",
                    )
                }
            )
        # give some time for the screenshots to be uploaded
        time.sleep(20)  # seconds

    def launch_agent(self):
        """Launches the agent to perform the task in the OpenApps environment."""
        # TODO: check logic
        self.agent_args = hydra.utils.instantiate(self.config.agent)
        self.env_args = hydra.utils.instantiate(self.config.task)

        # Runs agent in BrowserGym environment on task
        exp_args = ExpArgs(
            env_args=self.env_args,
            agent_args=self.agent_args,
        )

        # running and logging results
        exp_args.prepare(self.config.logs_dir)
        exp_args.run()

        # loading and printing results
        exp_result = get_exp_result(exp_args.exp_dir)
        exp_record = exp_result.get_exp_record()

        print("Experiment Results")
        for key, val in exp_record.items():
            print(f"{key}: {val}")

        if self.config.use_wandb:
            self._log_agent_results_to_wandb(exp_record, exp_result)

    def cleanup(self, apps_process: subprocess.Popen):
        if self.config.use_wandb:
            wandb.finish()
        apps_process.terminate()
        time.sleep(4)
        apps_still_running = apps_process.poll() is None
        if apps_still_running:
            apps_process.kill()
        kill_ports(ports=[self.web_app_port])
        time.sleep(4)

    def launch(self):
        """
        Launches open apps environment and orchestrates agent to perform the task.
        """
        apps_process = self.launch_apps_via_shell()
        # TODO: confirm apps are running
        # TODO: check if agent model is available in case of VLLM or API
        self.launch_agent()
        self.cleanup(apps_process)


if __name__ == "__main__":
    repo = git.Repo(Path(__file__).parent.parent.parent.resolve())
    sha = repo.head.object.hexsha
    print("git hash", sha)
