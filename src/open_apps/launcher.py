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

import os
import random
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.parse  # Add this import
import urllib.request
from pathlib import Path

# Standard library imports
from subprocess import PIPE
from time import sleep
from typing import Optional

import browsergym
import git
import hydra
import wandb
import yaml
from browsergym.experiments import ExpArgs, get_exp_result
from fasthtml.common import serve
from killport import kill_ports

# Third-party imports
from omegaconf import DictConfig, OmegaConf, open_dict

# Project-specific imports
from open_apps.apps.availability import (
    onlineshop_unavailable_reason,
    task_needs_onlineshop,
)
from open_apps.apps.start_page.main import initialize_routes_and_configure_task
from open_apps.preview import open_preview
from open_apps.tasks.add_tasks_to_browsergym import register_tasks_with_browsergym
from open_apps.tasks.tasks import Task
from open_apps.utils import merge_plus_keys

# Note: the "now" interpolation resolver (used as ${now:...} in the configs)
# is provided by Hydra's own setup at run/compose time, so we don't register
# one here. (A local registration previously lived here but was shadowed by
# Hydra's and never actually ran.)


def _absolute_path(value) -> Path:
    """Resolve a path from config the way the user meant it.

    Hydra ``chdir``s into the run directory before any of this executes, so a
    relative path typed on the command line has to be resolved against the
    directory the command was launched from, not the one we are in now.
    Outside a Hydra context (tests, direct instantiation) cwd is still right.
    """
    if hydra.core.hydra_config.HydraConfig.initialized():
        return Path(hydra.utils.to_absolute_path(str(value))).expanduser().resolve()
    return Path(str(value)).expanduser().resolve()


def _filename_safe(text: str) -> str:
    """Keep a recording's file name portable: no slashes, spaces or shell
    metacharacters from a task or model name (``Qwen/Qwen3-VL``)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")


def freeze_recording_dir(config: DictConfig) -> None:
    """Pin ``record_video_dir`` to an absolute path, in place.

    Call once, in the process the user launched, before any job config is
    derived from ``config``. It matters for two reasons:

    * ``record_video_dir`` defaults to ``${logs_dir}/recordings``, and a local
      sweep rewrites each job's ``logs_dir`` to ``<logs_dir>/<job>``. Left as an
      interpolation, every job would record into its own folder; frozen, a
      sweep's videos share one.
    * SLURM jobs are unpickled on a compute node with no Hydra context, in
      whatever directory Hydra had changed into. A relative path resolved
      there means nothing the user typed.

    A no-op when recording is off or the key is absent.
    """
    if not config.get("record_video") or "record_video_dir" not in config:
        return
    value = config.record_video_dir or Path(config.logs_dir) / "recordings"
    config.record_video_dir = str(_absolute_path(value))


class OpenAppsLauncher:
    def __init__(self, config: DictConfig):
        self.config = config
        self.web_app_host = "localhost"
        OmegaConf.resolve(self.config)

        # Merge any '+' prefixed keys with their base keys
        self.config = merge_plus_keys(self.config)
        random.seed(self.config.seed)

        self.web_app_port = self.pick_empty_port(
            start_port=5001 + self._job_port_offset()
        )
        print(f"Using port {self.web_app_port} for the web app")
        self.web_app_url = f"http://{self.web_app_host}:{self.web_app_port}"
        print("Web app hostname is: ", self.web_app_url)
        self.config.logs_dir = str(_absolute_path(self.config.logs_dir))
        # After logs_dir, which the default destination interpolates.
        freeze_recording_dir(self.config)

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
        # model_pretty_name is the display name every agent config sets for
        # exactly this purpose; model_name is the raw HF repo path, which drags
        # the org prefix (e.g. "ByteDance-Seed/") into the run name.
        agent_name = (
            self.config.agent.get("model_pretty_name")
            or self.config.agent.get("model_name")
            or self.config.agent.get("agent_name")
            or "agent"
        )
        run_name = agent_name
        task_name = self.config.get("task_name")
        if task_name:
            run_name = f"{run_name}-{task_name}"

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

    def _job_port_offset(self) -> int:
        """Offset port ranges per parallel job so siblings don't collide.

        Each job gets a 100-port window starting at ``5001 + job_id*100``.
        """
        job_id = int(self.config.get("job_id", 0) or 0)
        return job_id * 100

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

        # Open a window on the apps, sized like `config/device/`. Off whenever
        # something other than a person is driving: `headless=True` is passed
        # by launch_apps_via_shell (agent runs) and by save_screenshots.py,
        # both of which bring their own browser.
        if not self.config.get("headless", False):
            url = f"http://{self.web_app_host}:{self.web_app_port}/"
            open_preview(url, self.config.get("device"))

        serve(
            appname="launch",
            reload=False,
            port=self.web_app_port,
            host=self.web_app_host,
        )
        sleep(12)

    def launch_apps_via_shell(self):
        file_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        abs_config_file_path = self.config_path.resolve()
        config_dir_for_subprocess = abs_config_file_path.parent
        config_name_for_subprocess = abs_config_file_path.name

        venv_activate_script = Path(file_dir) / ".venv" / "bin" / "activate"
        command = (
            f"source '{venv_activate_script}' && "
            f"cd '{file_dir}' && "
            f"uv run launch.py --config-path '{config_dir_for_subprocess}' "
            f"--config-name '{config_name_for_subprocess}' use_wandb=False headless=True"
        )
        print("Launching web app with command: ", command)
        # Redirect the web app's output to a file rather than an unread PIPE.
        # An unread PIPE fills its OS buffer (~64KB) and blocks the web server
        # on write, wedging the whole run so the process never exits.
        webapp_log_path = Path(self.config.logs_dir) / "webapp.log"
        self._webapp_log_fh = open(webapp_log_path, "a")
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=self._webapp_log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            executable="/bin/bash",  # Use bash explicitly for 'source' command
        )
        sleep(30)
        return process

    def is_app_running(self) -> bool:
        """Checks the web app is up AND is the one launched by this job.

        Because parallel jobs on the same SLURM node share ``localhost``, a
        port-bind race can leave a sibling job's web app answering on this
        job's port. We guard against that by reading ``/environment_variables``
        (served by ``start_page/main.py``) and confirming the response
        mentions our ``databases_dir`` — a per-job unique path.
        """
        try:
            response = urllib.request.urlopen(
                self.web_app_url + "/environment_variables", timeout=10
            )
            if response.status != 200:
                return False
            body = response.read().decode("utf-8", errors="replace")
            expected = self.config.databases_dir
            if expected not in body:
                print(
                    f"Web app on {self.web_app_url} is serving a DIFFERENT job's "
                    f"config (expected databases_dir={expected!r}). "
                    "Another job's process must own this port."
                )
                return False
            print("Web app main page is running properly...")
            return True
        except Exception as e:
            print(f"Web app is not running on {self.web_app_url} as expected: {e}")
            return False

    def launch(self):
        self.launch_apps()
        print("experiment finished")


class AgentLauncher(OpenAppsLauncher):
    def __init__(self, config: DictConfig):
        super().__init__(config)

    def _success_summary(self, cum_reward: float, scores=None) -> dict:
        """The run-level ``success`` (and ``cum_reward``) for ``wandb.summary``.

        Ordinarily ``success`` is the reward: BrowserGym emits a terminal
        reward of 1.0 on task success, else 0 (accuracy = mean success).

        Ambiguity tasks (a task with ``expected``) are the exception. Their
        reward only sees the state half of success -- on a ``refuse`` task it
        is 1 whenever nothing changed, so an agent that idles to max_steps
        earns it -- while success is asking (or refusing) first, which only
        the alignment scorer sees. There ``success`` is the scorer's
        (``align/success``) and the raw reward goes to ``task_reward_success``;
        without scores (alignment off, or it failed) ``success`` is left unset
        rather than filled with the misleading reward.
        """
        raw = int(cum_reward >= 1.0)
        out: dict = {"cum_reward": cum_reward}
        if not getattr(getattr(self, "task", None), "expected", None):
            out["success"] = raw
            return out
        out["task_reward_success"] = raw
        aligned_success = getattr(scores, "success", None)
        if aligned_success is not None:
            out["success"] = int(aligned_success)
        return out

    def _log_agent_results_to_wandb(self, exp_record: dict, exp_result, scores=None):
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
            # Only log if key exists in exp_record (avoid KeyError on failed runs)
            if key in exp_record:
                wandb.log({key: exp_record[key]})
        # Single value per run for report aggregation (accuracy = mean
        # success); see _success_summary for ambiguity tasks.
        cum_reward = float(exp_record.get("cum_reward") or 0.0)
        for key, value in self._success_summary(cum_reward, scores).items():
            wandb.run.summary[key] = value
        steps_info = exp_result.steps_info

        def action_error(i: int) -> str:
            """BrowserGym's ``last_action_error`` for the action taken at step ``i``.

            An observation describes the state *after* the preceding action, so
            the error raised by ``steps_info[i].action`` lands in
            ``steps_info[i + 1].obs``, not in step ``i``'s own obs. Without
            this shift the column would blame each failure on the next action.
            The final step has no successor (the loop ended), so its action —
            if any — has no recorded outcome.
            """
            if i + 1 >= len(steps_info):
                return ""
            next_obs = steps_info[i + 1].obs
            return str(next_obs.get("last_action_error", "") if next_obs else "")

        actions_data = [
            [
                i,
                str(step_info.action),
                str(step_info.obs.get("open_pages_urls") if step_info.obs else None),
                str(
                    step_info.agent_info.get("think")
                    if step_info.agent_info
                    else None
                ),
                # Whether the environment actually accepted the action. Agents
                # routinely narrate success in `think` for a click that never
                # landed, so without this the table reads as a win either way.
                action_error(i),
            ]
            for i, step_info in enumerate(steps_info)
        ]

        actions_table = wandb.Table(
            data=actions_data,
            columns=[
                "step",
                "action",
                "open_pages_urls",
                "think",
                "last_action_error",
            ],
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
        sleep(20)  # seconds

    def setup_browsergym_task(self):
        # specifies goal and logic for reward
        task_name = self.config.task_name
        task_configs = self.config.tasks[task_name]
        task: Task = hydra.utils.instantiate(task_configs)
        # Kept for alignment scoring after the episode (see _record_alignment).
        self.task = task
        register_tasks_with_browsergym(tasks=[task])

        self.config.browsergym_env_args.task_name = task.task_id
        self.config.browsergym_env_args.task_kwargs.base_url = self.web_app_url
        if getattr(task, "expected", None) and self.config.get("task_termination") != "on_agent_stop":
            # Ambiguity tasks (config/tasks/ambiguity.yaml) are scored on the
            # agent's ask/refuse, so they always end on its report; the task
            # enforces this too, and setting it here keeps the recorded
            # identity (task_termination) truthful. task_kwargs interpolates it.
            with open_dict(self.config):
                self.config.task_termination = "on_agent_stop"
        # Adversarial events (config/apps/events/) ride along with the task,
        # which fires them from validate. Set here rather than in the env-args
        # yaml so every browsergym_env_args variant gets them; seeded from the
        # run seed so a sweep over seeds reproduces its event timing.
        events = self.config.get("apps", {}).get("events")
        if events:
            # open_dict: a composed config is struct, and these keys are not
            # in the env-args yaml.
            with open_dict(self.config.browsergym_env_args.task_kwargs) as task_kwargs:
                from open_apps.alignment.events import content_languages

                task_kwargs.events = events
                task_kwargs.events_seed = self.config.get("seed")
                # Each event speaks its target app's content language.
                task_kwargs.event_languages = content_languages(self.config.apps)
        # instantiate browsergym task
        return hydra.utils.instantiate(self.config.browsergym_env_args)

    def _recording_dir(self) -> Optional[Path]:
        """Where this episode's video goes, or ``None`` when not recording.

        Recording is on when ``browsergym_env_args.record_video`` is, which
        follows the top-level ``record_video`` switch. The destination was
        frozen to an absolute path by :func:`freeze_recording_dir` in the
        process that was launched; resolving it again here is a no-op then,
        and a fallback for a launcher constructed some other way.
        """
        env_args = self.config.get("browsergym_env_args") or {}
        if not env_args.get("record_video", False):
            return None
        value = self.config.get("record_video_dir") or Path(self.config.logs_dir) / "recordings"
        return _absolute_path(value)

    def _save_recording(self, exp_dir: Path, exp_record: dict) -> Optional[Path]:
        """Copy the episode's video out of the experiment dir into the recordings dir.

        BrowserGym writes it to ``<exp_dir>/task_video/<random>.webm``, three
        levels into a timestamped tree and named by a hash. The copy is named
        for what it shows -- when, which task, which agent, whether it passed,
        which job -- so a folder of recordings can be skimmed without opening
        run logs. The original stays where AgentLab's tooling expects it.
        Hard-linked when the two are on one filesystem, so a sweep does not
        store every video twice; copied otherwise (node-local disk to a shared
        one, say).

        The name is claimed with an exclusive create, not an exists() check:
        `conduct.sh` runs several episodes of the same task concurrently with
        no job id, and two finishing in the same second must not overwrite
        each other.

        Runs for failed episodes too: a recording of the failure is usually
        the reason anyone wanted the recording.
        """
        target_dir = self._recording_dir()
        if target_dir is None:
            return None
        videos = sorted((Path(exp_dir) / "task_video").glob("*.webm"))
        if not videos:
            print(f"record_video is on but no video was written under {exp_dir}")
            return None
        source = videos[-1]

        outcome = "pass" if float(exp_record.get("cum_reward") or 0.0) >= 1.0 else "fail"
        agent_cfg = self.config.get("agent") or {}
        agent = agent_cfg.get("model_pretty_name") or agent_cfg.get("model_name") or ""
        job = self.config.get("job_id")
        stem = "_".join(
            _filename_safe(str(part))
            for part in (
                time.strftime("%Y-%m-%d_%H-%M-%S"),
                self.config.task_name,
                agent,
                outcome,
                f"job{job}" if job is not None else "",
            )
            if part
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        for n in range(1000):
            target = target_dir / (f"{stem}.webm" if n == 0 else f"{stem}-{n}.webm")
            try:
                os.link(source, target)
            except FileExistsError:
                continue
            except OSError:
                # Different filesystems, or links not supported: copy into a
                # file we created exclusively, so the name is still ours alone.
                try:
                    with open(source, "rb") as src, open(target, "xb") as dst:
                        shutil.copyfileobj(src, dst)
                except FileExistsError:
                    continue
            print(f"Recording saved to {target}")
            return target
        print(f"Could not find a free name for the recording in {target_dir}")
        return None

    def launch_agent(self):
        """Launches the agent to perform the task in the OpenApps environment."""
        self.agent_args = hydra.utils.instantiate(self.config.agent)
        self.browser_gym_task = self.setup_browsergym_task()

        # Runs agent in BrowserGym environment on task
        exp_args = ExpArgs(
            env_args=self.browser_gym_task,
            agent_args=self.agent_args,
        )

        # running and logging results
        exp_args.prepare(self.config.logs_dir)
        self.agent_args.save_dir = str(exp_args.exp_dir)
        record_alignment = self._record_alignment_enabled()
        if record_alignment:
            self._attach_episode_dir(exp_args, irreversible=self._irreversible_map())
        exp_args.run()

        # loading and printing results
        exp_result = get_exp_result(exp_args.exp_dir)
        exp_record = exp_result.get_exp_record()

        print("Experiment Results")
        for key, val in exp_record.items():
            print(f"{key}: {val}")

        recording = self._save_recording(exp_args.exp_dir, exp_record)
        # exp_result is handed over so its step pickles are read once, not
        # once for the record and again for the W&B action table.
        alignment = (
            self._record_alignment(exp_args, exp_result=exp_result)
            if record_alignment
            else (None, None)
        )

        if self.config.use_wandb:
            if record_alignment:
                # Before the results logger: it ends with a sleep that lets
                # uploads (now including the episode artifact) finish.
                self._log_alignment_to_wandb(*alignment)
            self._log_agent_results_to_wandb(exp_record, exp_result, scores=alignment[1])
            if recording is not None:
                wandb.log({"episode_video": wandb.Video(str(recording), format="webm")})
                wandb.run.summary["video_path"] = str(recording)

    @staticmethod
    def _attach_episode_dir(exp_args: ExpArgs, irreversible: Optional[dict] = None) -> None:
        """Have ``OpenAppsTask`` persist the initial and final app state (and
        its per-step snapshots) into the experiment directory
        (``openapps_states.json``).

        Set after ``prepare()`` because that is when ``exp_dir`` exists.
        ``task_kwargs`` is a plain dict (the env args config converts), so a
        copy with more keys is all it takes. ``irreversible`` is the map the
        snapshots classify changes with -- the run's own, so a variation that
        overrides it is honoured mid-episode as well as at scoring time.
        """
        task_kwargs = dict(exp_args.env_args.task_kwargs or {})
        task_kwargs["episode_dir"] = str(exp_args.exp_dir)
        if irreversible is not None:
            task_kwargs["irreversible"] = irreversible
        exp_args.env_args.task_kwargs = task_kwargs

    def _irreversible_map(self) -> Optional[dict]:
        """The run's ``irreversible:`` maps, or None (the task and the scorer
        then fall back to config/apps/*/default.yaml). Never raises.

        Resolved once per launcher and reused: the task's per-step snapshots
        and the episode record must classify changes with the same map.
        """
        if not hasattr(self, "_irreversible_cache"):
            try:
                from open_apps.alignment.irreversible import map_from_apps_config

                self._irreversible_cache = map_from_apps_config(self.config.get("apps"))
            except Exception as exc:  # noqa: BLE001 -- alignment never fails a run
                print(f"Alignment: could not read irreversible maps ({type(exc).__name__}: {exc})")
                self._irreversible_cache = None
        return self._irreversible_cache

    def _record_alignment_enabled(self) -> bool:
        """Top-level ``record_alignment`` (config/config.yaml), default on.

        Off, a run is what it was before alignment scoring existed: the task
        is given no ``episode_dir`` (no state file, no per-step state
        hashes), and no ``episode.json``, ``align/*`` W&B keys or episode
        artifact are produced.
        """
        return bool(self.config.get("record_alignment", True))

    def _record_alignment(
        self, exp_args: ExpArgs, exp_result=None
    ) -> tuple[Optional[Path], Optional[object]]:
        """Write ``<exp_dir>/episode.json`` and score it.

        Best-effort by design: alignment scoring is an observer of the run,
        so any failure is printed and the run carries on exactly as before.
        Returns ``(record_path, scores)``, either of which may be None.
        """
        try:
            from open_apps.alignment.record import (
                build_episode_record,
                identity_from_config,
                save_record,
            )
            identity = identity_from_config(
                self.config, self.task, task_seed=exp_args.env_args.task_seed
            )
            record = build_episode_record(
                exp_args.exp_dir,
                identity=identity,
                irreversible=self._irreversible_map(),
                # The apps are still up (cleanup runs after this), so a
                # teardown that missed the final state can be backfilled --
                # reporting failed app probes, like teardown does.
                fallback_final_state=self._probe_final_state,
                exp_result=exp_result,
            )
            path = save_record(record, exp_args.exp_dir)
            print(f"Episode record saved to {path}")
        except Exception as exc:  # noqa: BLE001 -- see docstring
            print(f"Alignment: could not build episode record ({type(exc).__name__}: {exc})")
            return None, None
        try:
            from open_apps.alignment.score import score

            scores = score(record, self.task)
            print("Alignment scores:", scores.summary())
            return path, scores
        except Exception as exc:  # noqa: BLE001
            print(f"Alignment: could not score episode ({type(exc).__name__}: {exc})")
            return path, None

    def _probe_final_state(self) -> tuple[dict, list[str]]:
        """``(state, failed_apps)`` from the still-running apps."""
        from open_apps.state import get_current_state

        failed: list[str] = []
        return get_current_state(self.web_app_url, failed_apps=failed), failed

    @staticmethod
    def _log_alignment_to_wandb(record_path: Optional[Path], scores) -> None:
        """Scores to ``wandb.summary`` as ``align/<metric>``; the record as an
        ``episode_record`` artifact, so the leaderboard can import from W&B."""
        try:
            if scores is not None:
                for key, value in scores.summary().items():
                    if value is None:
                        continue
                    # ints, not bools: matches the existing `success` summary
                    # and averages cleanly in W&B reports.
                    wandb.run.summary[f"align/{key}"] = (
                        int(value) if isinstance(value, bool) else value
                    )
            if record_path is not None:
                artifact = wandb.Artifact(
                    name=f"episode-{wandb.run.id}",
                    type="episode_record",
                    metadata={"record_version": 1},
                )
                artifact.add_file(str(record_path))
                wandb.run.log_artifact(artifact)
        except Exception as exc:  # noqa: BLE001 -- never fail a run over logging
            print(f"Alignment: could not log to W&B ({type(exc).__name__}: {exc})")

    def cleanup(self, apps_process: subprocess.Popen):
        if self.config.use_wandb:
            wandb.finish()
        apps_process.terminate()
        try:
            apps_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print("Apps process is still running, stopping OpenApps...")
            apps_process.kill()
            apps_process.wait()
            time.sleep(4)
            kill_ports(ports=[self.web_app_port])
            print("OpenApps successfully stopped.")
        webapp_log_fh = getattr(self, "_webapp_log_fh", None)
        if webapp_log_fh is not None:
            webapp_log_fh.close()
            self._webapp_log_fh = None

    def wait_until_apps_start(self, apps_process, times_to_wait: int = 10):
        is_app_running = False
        for _ in range(times_to_wait):
            if self.is_app_running():
                is_app_running = True
                break
            print("Waiting for OpenApps to start...")
            sleep(10)
            # Do NOT call apps_process.communicate() here: it blocks until the
            # process exits, but the web server runs forever, so it would
            # deadlock. Just check the server didn't die during startup.
            if apps_process.poll() is not None:
                raise RuntimeError(
                    "Web app process exited before becoming ready; see "
                    f"{Path(self.config.logs_dir) / 'webapp.log'}"
                )
        if is_app_running:
            print("OpenApps is running, proceeding to run the agent.")
        else:
            raise RuntimeError(
                f"OpenApps failed to start on {self.web_app_url} within "
                f"{times_to_wait} attempts. Aborting to avoid scoring against "
                "a sibling job's web app."
            )

    def check_task_can_run(self):
        """Refuse a task that needs the shop when this run does not serve it.

        Otherwise the agent spends every step looking for an app whose routes
        were never registered, and the run reads as a model failure. Checked
        before the web app starts, so the mistake costs nothing.
        """
        task_name = self.config.get("task_name")
        task_cfg = self.config.tasks.get(task_name) if task_name else None
        if not task_needs_onlineshop(task_cfg):
            return
        reason = onlineshop_unavailable_reason(self.config.apps)
        if reason:
            raise ValueError(
                f"Task {task_name!r} uses the online shop, but this run does not "
                f"serve it: {reason}. Drop that override, or pick a task that "
                f"does not touch the shop."
            )

    def launch(self):
        """
        Launches open apps environment and orchestrates agent to perform the task.
        """
        self.check_task_can_run()
        apps_process = self.launch_apps_via_shell()
        self.wait_until_apps_start(apps_process)
        # TODO: check if agent model is available in case of VLLM or API
        try:
            self.launch_agent()
            self.cleanup(apps_process)
        except Exception as e:
            self.cleanup(apps_process)
            print("Error during agent launch: ", e)
            raise e


if __name__ == "__main__":
    repo = git.Repo(Path(__file__).parent.parent.parent.resolve())
    sha = repo.head.object.hexsha
    print("git hash", sha)
