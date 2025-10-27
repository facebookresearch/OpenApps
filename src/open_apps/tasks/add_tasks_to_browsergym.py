import os
from typing import Optional, Tuple
import requests

import playwright.sync_api
from playwright.sync_api import sync_playwright
import wandb
from browsergym.core.task import AbstractBrowserTask
from open_apps.tasks.tasks import Task
import gymnasium as gym
from browsergym.core.env import BrowserEnv


def register_tasks_with_browsergym(
    tasks: list[Task], base_url: Optional[str] = None
) -> None:
    """create a list of all tasks to be registered with browsergym"""
    # TODO: call this function somewhere before we start browser gym
    for task in enumerate(tasks):
        register_task(
            task.get_task_id(variation=i),
            task,
            task_kwargs={"variation": i},
            nondeterministic=task.nondeterministic,
        )
        # browser_gym_task = create_browsergym_task_from_openapps_task(
        #     task, base_url=base_url
        # )
        # gym.register(
        #     id=f"browsergym/{task.task_id}",
        #     entry_point=lambda *env_args, **env_kwargs: BrowserEnv(
        #         browser_gym_task, *env_args, **env_kwargs
        #     ),
        #     # OpenApps is deterministic
        #     nondeterministic=False,
        # )


def create_browsergym_task_from_openapps_task(
    task: Task,
) -> OpenAppsTask:
    """Create a BrowserGym task from an OpenApps task.

    Args:
        openapps_task (Task): The OpenApps task to be converted.
    """
    # add_meeting_with_dennis_task
    task
    return


class OpenAppsTask(AbstractBrowserTask):
    """
    Abstract class for OpenApps.
    """

    # gym metadata (default value, can be overloaded per task)
    nondeterministic: bool = False

    @classmethod
    def get_task_id(cls, task_id: str | None = None) -> str:
        if task_id is None:
            raise ValueError("task_id must be provided.")
        return task_id

    def _get_goal(self):
        return f"{self.goal}"

    def __init__(
        self,
        seed: int,
        goal: str,
        task_id: str,
        base_url: Optional[str] = None,
        url_extension: Optional[str] = "",
        episode_max_time: int = 1000000,
        remove_human_display: bool = True,
        screen_resolution: Tuple[int, int] = (1024, 640),
    ) -> None:
        """
        Args:
            seed: random seed.
            base_url: str (optional), the base Miniwob URL where the task's HTML file is to be found. If not provided, the MINIWOB_URL environment variable will be used.
            url_extension: str (optional), the URL extension to be appended to the base URL. Default: "". Determines the start page the agetn will be presented with.
            episode_max_time: int, episode max time in milliseconds. Default: 1000000 ms.
            remove_human_display: bool, whether or not to remove the human display (goal, time left, last reward etc.) from the DOM. Default: True.

        """
        super().__init__(seed)

        self.goal = goal
        self.task_id = task_id

        # task properties, will be used to set up the browsergym environment
        self.viewport = {"width": screen_resolution[0], "height": screen_resolution[1]}
        self.slow_mo = 100  # ms
        self.timeout = 5000  # ms

        assert episode_max_time > 0

        # if not provided, try to get Miniwob URL from environment variable
        if base_url is None:
            if "OPENAPPS_URL" in os.environ:
                base_url = os.environ["OPENAPPS_URL"]
            else:
                raise ValueError(
                    f"Please provide a base URL (or setup one using the environment variable OPENAPPS_URL)."
                )

        self.base_url = base_url
        self.url = base_url + url_extension
        self.episode_max_time = episode_max_time
        self.remove_human_display = remove_human_display

        self.goal_category = ""  # optional string: set in task init, to categorize the goal (prompt), e.g "typos, foreign language, etc."

    def _get_info(self):
        info = {}  # e.g. episodeID, reward, ect
        return info

    def setup(self, page: playwright.sync_api.Page) -> tuple[str, dict]:
        if wandb.run is not None:
            wandb.summary["goal"] = self._get_goal()
            wandb.summary["url"] = self.url
            wandb.summary["base_task_id"] = self.sub_task_id
            wandb.summary["task_class_name"] = self.__class__.__name__
            wandb.summary["goal_category"] = self.goal_category
        self.page = page
        self.page.goto(self.url)
        return self._get_goal(), self._get_info()

    def validate(
        self, page: playwright.sync_api.Page, chat_messages: list[str]
    ) -> Tuple[float, bool, str, dict]:

        info = self._get_info()
        reward = self.reward()
        if reward >= 1.0:
            done = True
        else:
            done = False

        msg = ""
        return reward, done, msg, info

    def reward(self) -> float:
        """Return 1.0 if the item was marked as done, else 0.0."""
        return 1.0 if self.check_if_task_completed(self.reward_criterion) else 0.0

    def teardown(self) -> None:
        pass

    def cheat(self, page: playwright.sync_api.Page, chat_messages: list[str]) -> None:
        pass
