"""Public OpenApps task API.

Exposes:
  - ``Task`` and concrete task subclasses (re-exported from
    :mod:`open_apps.tasks.tasks`).
  - :func:`load_task` / :func:`list_task_keys` to instantiate or
    enumerate tasks declared in ``config/tasks/all_tasks.yaml``.
"""

from __future__ import annotations

from hydra.utils import instantiate
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.tasks.tasks import (
    AddEventTask,
    AddToDoTask,
    DeleteToDoTask,
    MarkToDoDoneTask,
    NavigateToAppTask,
    RemoveEventTask,
    RemoveLandmarkTask,
    SavePlaceTask,
    SendMessageTask,
    Task,
)


__all__ = [
    "AddEventTask",
    "AddToDoTask",
    "DeleteToDoTask",
    "MarkToDoDoneTask",
    "NavigateToAppTask",
    "RemoveEventTask",
    "RemoveLandmarkTask",
    "SavePlaceTask",
    "SendMessageTask",
    "Task",
    "list_task_keys",
    "load_task",
]


# Maps a task class name to the OpenApps app the task starts in
# (i.e. which app the env should serve as its landing page).
# NavigateToAppTask is handled separately because its starting app is
# encoded per-instance in ``source_app``.
_TASK_CLASS_TO_APP: dict[str, str] = {
    "AddEventTask": "calendar",
    "RemoveEventTask": "calendar",
    "AddToDoTask": "todo",
    "MarkToDoDoneTask": "todo",
    "DeleteToDoTask": "todo",
    "SendMessageTask": "messages",
    "SavePlaceTask": "map",
    "RemoveLandmarkTask": "map",
}


def _tasks_yaml_path():
    return config_dir() / "tasks" / "all_tasks.yaml"


def _load_tasks_cfg():
    """Load the task set as a single flat ``{task_key: config}`` map.

    ``all_tasks.yaml`` uses a Hydra-style ``defaults`` list to compose
    sibling files (e.g. ``original_tasks`` + ``user_goal_variations``).
    Plain ``OmegaConf.load`` does not resolve that list, so we replicate
    Hydra's merge here. A plain flat file (no ``defaults`` key) is still
    loaded as-is for backward compatibility.
    """
    path = _tasks_yaml_path()
    if not path.is_file():
        return OmegaConf.create({})
    cfg = OmegaConf.load(path)
    includes = cfg.pop("defaults", None)
    if includes is None:
        return cfg
    merged = OmegaConf.create({})
    for name in includes:
        if name == "_self_":
            merged = OmegaConf.merge(merged, cfg)
            continue
        sub = path.parent / f"{name}.yaml"
        merged = OmegaConf.merge(merged, OmegaConf.load(sub) or {})
    return merged


def list_task_keys(app: str | None = None) -> list[str]:
    """List task keys from ``all_tasks.yaml``, optionally filtered by env app.

    Args:
        app: If given, only return tasks that start in this app
            (i.e. the env constructed with ``app_name=app`` should be
            able to run them).
    """
    path = _tasks_yaml_path()
    if not path.is_file():
        return []
    cfg = _load_tasks_cfg()
    keys: list[str] = []
    for k, v in cfg.items():
        if app is None:
            keys.append(k)
            continue
        cls = v.get("_target_", "").rsplit(".", 1)[-1]
        if cls == "NavigateToAppTask":
            if v.get("source_app") == app:
                keys.append(k)
            continue
        if _TASK_CLASS_TO_APP.get(cls) == app:
            keys.append(k)
    return keys


def load_task(key: str, app: str | None = None) -> Task:
    """Hydra-instantiate the task identified by ``key`` from ``all_tasks.yaml``.

    Args:
        key: Task key into ``all_tasks.yaml``.
        app: If given, validates the task starts in this app and
            raises ``ValueError`` otherwise.
    """
    path = _tasks_yaml_path()
    if not path.is_file():
        raise FileNotFoundError(f"Tasks yaml not found at {path}")

    cfg = _load_tasks_cfg()
    if key not in cfg:
        raise ValueError(
            f"Unknown task key {key!r}. Available: {list(cfg.keys())}"
        )

    task_cfg = cfg[key]
    target = task_cfg.get("_target_", "")
    cls = target.rsplit(".", 1)[-1]

    if app is not None:
        if cls == "NavigateToAppTask":
            source_app = task_cfg.get("source_app")
            if source_app is not None and source_app != app:
                raise ValueError(
                    f"Task {key!r} (NavigateToAppTask) starts in app "
                    f"{source_app!r}, not {app!r}"
                )
        else:
            expected = _TASK_CLASS_TO_APP.get(cls)
            if expected is not None and expected != app:
                raise ValueError(
                    f"Task {key!r} ({cls}) targets app {expected!r}, "
                    f"not {app!r}"
                )
    return instantiate(task_cfg)
