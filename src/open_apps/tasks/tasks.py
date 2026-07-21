from dataclasses import dataclass, field
from typing import Optional
from abc import ABC, abstractmethod
import hashlib
import math
import re
from deepdiff import DeepDiff
from deepdiff.operator import BaseOperator
from datetime import datetime
import copy
from deepdiff.helper import COLORED_COMPACT_VIEW
from omegaconf.dictconfig import DictConfig
from omegaconf import OmegaConf


class StringSimilarityOperator(BaseOperator):
    """
    Operator is used in DeepDiff to compare strings.
    Ignores case, special characters, and extra spaces when comparing strings.
    """

    @staticmethod
    def normalize_string(s: str) -> str:
        # Convert to lowercase
        s = s.lower()
        # Replace special characters and multiple spaces with single space
        s = re.sub(r"[^\w\s]", "", s)
        # Remove extra whitespace
        s = re.sub(r"\s+", " ", s)
        # Strip leading/trailing whitespace
        return s.strip()

    def give_up_diffing(self, level, diff_instance):
        if isinstance(level.t1, str) and isinstance(level.t2, str):
            # Compare strings case-insensitively
            if self.normalize_string(level.t1) == self.normalize_string(level.t2):
                return True  # Strings are equal, stop diffing
        return False


# Match the whole coords list (not individual axis entries), so we can compute
# a joint (lat, lon) distance instead of diffing each axis independently.
# Path shape: root['map'][0]['coords'].
MAP_COORDS_REGEX = r"root\['map'\]\[\d+\]\['coords'\]$"

# Latitude/longitude → distance conversion.
#   1° latitude  ≈ 111 km (≈ 69 mi) everywhere — Earth's meridian / 360.
#   1° longitude ≈ 111 km × cos(latitude), shrinking toward the poles:
#       equator (0°) → ~111 km / 69 mi
#       40°N (NYC)   →  ~85 km / 53 mi
#       60°N (Oslo)  →  ~55 km / 34 mi
#   So a 10 km (≈ 6.2 mi) tolerance ≈ 0.09° of latitude, or ~0.11° of
#   longitude at 40°N. Coords arrive here as int(degrees * 10) from
#   _normalize_map_locations, so we divide by 10 to recover degrees before
#   computing the distance.
_KM_PER_DEGREE_LAT = 111.0


class CoordsApproxEqualOperator(BaseOperator):
    """Treats two map coordinates as equal when their euclidean distance
    on the ground is within ``tolerance_km``.

    Uses the equirectangular (flat-earth) approximation, which is accurate
    to well under a percent at the ~10 km scale we care about:

        dlat_km = (lat1 - lat2) * 111
        dlon_km = (lon1 - lon2) * 111 * cos(mean_lat)
        distance_km = sqrt(dlat_km² + dlon_km²)

    This absorbs the small drift between where the agent clicked on the
    map and the exact ground-truth pin, without letting per-axis slack
    stack into a much larger diagonal error.

    Note that this is unprecise due to long not being lienarly proportional to distance.
    """

    def __init__(self, tolerance_km: float = 10.0):
        super().__init__(regex_paths=[MAP_COORDS_REGEX])
        self.tolerance_km = tolerance_km
    
    def give_up_diffing(self, level, diff_instance) -> bool:
        try:
            lat1, lon1 = level.t1[0] / 10.0, level.t1[1] / 10.0
            lat2, lon2 = level.t2[0] / 10.0, level.t2[1] / 10.0
        except (TypeError, IndexError, ValueError):
            return False
        mean_lat_rad = math.radians((lat1 + lat2) / 2)
        dlat_km = (lat1 - lat2) * _KM_PER_DEGREE_LAT
        dlon_km = (lon1 - lon2) * _KM_PER_DEGREE_LAT * math.cos(mean_lat_rad)
        distance_km = math.sqrt(dlat_km ** 2 + dlon_km ** 2)
        return distance_km <= self.tolerance_km


class AppStateComparison:
    """
    Compare two app states for similarity.

    Args:
        state1: First app state to compare
        state2: Second app state to compare
    """

    def __init__(
        self,
        state1: dict,
        state2: dict,
        reply_contacts: dict[str, int] | None = None,
        coords_tolerance_km: float = 10.0,
    ):
        self.raw_state1 = state1
        self.raw_state2 = state2
        # Messenger contacts whose auto-reply should be ignored, mapped to the
        # number of messages the task sent them (i.e. how many trailing
        # auto-replies to tolerate). ``state1`` is the target (ground truth)
        # and ``state2`` the observed/current state. The app appends one reply
        # after each sent message (random text for anyone but Alice/Bob), so a
        # message task couldn't be checked deterministically otherwise. Only
        # the named contacts are relaxed, and only by the exact number of
        # sends — so a spurious message (to any contact, or an extra one to a
        # targeted contact) still fails the comparison. Empty/None => compare
        # every conversation exactly.
        self.reply_contacts = dict(reply_contacts or {})
        self.coords_tolerance_km = coords_tolerance_km

        self.state1 = self.preprocess(self.raw_state1)
        self.state2 = self.preprocess(self.raw_state2)

    def preprocess(self, state: dict) -> dict:
        # Drop keys we never compare *before* deep-copying: underscore-prefixed
        # env metadata (e.g. ``_url``) and the (potentially large) code-editor
        # tree. Shallow-slicing first avoids deep-copying data we're about to
        # discard.
        state = {
            k: v
            for k, v in state.items()
            if not k.startswith("_") and k != "codeeditor"
        }
        # Deep copy so normalization never mutates the caller's state. The
        # helpers below rewrite nested lists/dicts (dropping ids, flattening
        # messenger tuples, truncating replies), which a shallow copy would
        # leak back into the observed/target dicts passed in.
        state = copy.deepcopy(state)
        state = self._normalize_calendar_invitees(state)
        state = self._remove_id_key(state)
        state = self._normalize_todo_done_field(state)
        state = self._remove_timestamp_from_messenger(state)
        state = self._normalize_map_locations(state)
        state = self.sort_lists(state)
        return state

    def _normalize_todo_done_field(self, state: dict) -> dict:
        for todo in state["todo"]:
            done_value = todo.get("done")
            if (
                done_value is None
                or done_value is False
                or done_value == 0
                or done_value == "0"
            ):
                todo["done"] = False
            elif done_value is True or done_value == 1 or done_value == "1":
                todo["done"] = True
        return state

    def _normalize_calendar_invitees(self, state: dict) -> dict:
        """
        Canonicalize each calendar event's ``invitees`` to a sorted list.
        """
        for event in state["calendar"]:
            if "invitees" not in event:
                continue
            value = event["invitees"]
            if OmegaConf.is_config(value):  # convert hydra config to python format
                value = OmegaConf.to_container(value, resolve=True)
            if isinstance(value, str):  # convert comma separted strings into a list
                names = [part.strip() for part in value.split(",") if part.strip()]
            elif isinstance(value, (list, tuple)):  # normalize list/tuple of strings
                names = [str(part).strip() for part in value if str(part).strip()]
            elif value is None:
                names = []
            else:  # unexpected scalar
                names = [str(value).strip()] if str(value).strip() else []
            event["invitees"] = sorted(names, key=StringSimilarityOperator.normalize_string)
        return state

    def _remove_id_key(self, state: dict) -> dict:
        """Removes id keys from todo and calendar, as ID is
        internal to how entries are stored in the database
        """
        for todo in state["todo"]:
            if "id" in todo:
                del todo["id"]

        for event in state["calendar"]:
            # remove empty values, id, and fields no task compares on
            keys_to_delete = []
            for k, v in event.items():
                if k in ("id", "recurring") or v is None or v == "" or v == []:
                    keys_to_delete.append(k)
            for k in keys_to_delete:
                del event[k]
        return state

    def _remove_timestamp_from_messenger(self, state: dict) -> dict:
        for i, contact in enumerate(state["messenger"]):
            old_message_contents = contact["messages"]
            new_message_contents = [m[0] for m in old_message_contents]
            state["messenger"][i]["messages"] = new_message_contents
        return state

    def _normalize_map_locations(self, state: dict) -> dict:
        # Reduce each stored name to its primary (first comma-separated)
        # component, since the map app persists the full OSM address string
        # (e.g. "Bockelwitz, Leisnig, ..., Deutschland") while tasks may target
        # just the primary place name (e.g. "Bockelwitz").
        normalized_places = []
        for place in state["map"]:
            name = place["name"].split(",", 1)[0].strip()
            new_place = {
                "name": name,
                "coords": [int(place["coords"][0] * 10), int(place["coords"][1] * 10)],
            }
            normalized_places.append(new_place)
        state["map"] = normalized_places
        return state

    def sort_lists(self, state: dict) -> dict:
        """To ensure comparisons don't fail
        due to different list orders, we sort.
        """
        # field by which to sort
        app_and_field = [
            ("map", "name"),
            ("todo", "title"),
            ("calendar", "title"),
            ("messenger", "user"),
        ]
        for app, field in app_and_field:
            state[app] = sorted(
                state[app],
                key=lambda p, f=field: StringSimilarityOperator.normalize_string(p[f]),
            )
        return state

    @staticmethod
    def are_dicts_similar(
        dict1: dict,
        dict2: dict,
        coords_tolerance_km: float = 10.0,
    ) -> bool:
        """
        Compare two dictionaries for similarity, using a custom string comparison function.

        Args:
            dict1: First dictionary to compare
            dict2: Second dictionary to compare
            coords_tolerance_km: Distance (km) within which map coordinates
                are treated as equal. Default 10 km. Tasks pin a specific
                city block might want 1–2 km; tasks pinning a country might
                want 50–100 km.
        """
        diff = DeepDiff(
            dict1,
            dict2,
            custom_operators=[
                StringSimilarityOperator(types=[str]),
                CoordsApproxEqualOperator(tolerance_km=coords_tolerance_km),
            ],
            ignore_string_type_changes=True,
            ignore_numeric_type_changes=True,
            ignore_nan_inequality=True,
            ignore_encoding_errors=True,
            view=COLORED_COMPACT_VIEW,
        )
        if diff == {}:
            return True
        print(f"===Differences found: {diff}")
        return False

    def _truncate_message_replies(self) -> None:
        """Drop each targeted contact's trailing auto-replies before diffing.

        Runs after ``preprocess`` (so each contact's ``messages`` is already a
        flat list of message texts). For every contact named in
        ``reply_contacts`` (mapped to the number of messages the task sent
        them), if the observed conversation is longer than the target by no
        more than that many messages, truncate the observed conversation to
        the target's length. Content matching of the remaining prefix is left
        to the fuzzy diff.

        This ignores the one auto-reply the app appends per sent message,
        while still failing on:
          * a spurious message to a contact the task never targeted (that
            contact isn't in ``reply_contacts``, so it's compared exactly);
          * an extra message to a targeted contact (observed exceeds the
            tolerated count, so no truncation happens and the diff fails);
          * a missing send (observed is shorter than target).
        """
        target_by_user = {c["user"]: c for c in self.state1["messenger"]}
        for contact in self.state2["messenger"]:
            user = contact["user"]
            allowed = self.reply_contacts.get(user)
            if allowed is None or user not in target_by_user:
                continue
            n_target = len(target_by_user[user]["messages"])
            extra = len(contact["messages"]) - n_target
            if 0 <= extra <= allowed:
                contact["messages"] = contact["messages"][:n_target]

    def compare(self) -> bool:
        # check that both states have the same apps
        if set(self.state1.keys()) != set(self.state2.keys()):
            print("States have different apps")
            return False

        if self.reply_contacts:
            self._truncate_message_replies()

        return self.are_dicts_similar(
            self.state1, self.state2, self.coords_tolerance_km
        )


@dataclass
class Task(ABC):
    goal: str
    # Optional descriptor for how the goal is phrased (e.g. the user-goal
    # variation style). Keyword-only so subclasses can keep declaring
    # required positional fields without tripping dataclass field ordering.
    goal_style: Optional[str] = field(default=None, kw_only=True)

    @abstractmethod
    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        # Implement your logic to check if the event has been added successfully
        # commpare initial state and target state
        pass

    @property
    def task_id(self) -> str:
        goal_string = self.goal.encode("utf-8")
        return hashlib.sha256(goal_string).hexdigest()


@dataclass
class AddEventTask(Task):
    """
    Task to add an event to the calendar.
    """

    title: str
    date: str
    description: str | None
    location: str | None
    url: str | None
    invitees: list[str]

    @property
    def event(self) -> dict:
        return {
            "title": self.title,
            "date": self.date,
            "description": self.description if self.description else "",
            "location": self.location if self.location else "",
            "url": self.url if self.url else "",
            "invitees": self.invitees,
        }

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        assert target_state["calendar"], "calendar must be populated"
        if target_state["calendar"][-1] != self.event:
            target_state["calendar"].append(self.event)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        if isinstance(current_state, DictConfig):
            current_state = OmegaConf.to_container(current_state, resolve=True)
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class RemoveEventTask(Task):
    """
    Task to remove an event from the calendar.
    """

    title: str
    date: str

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        idx_to_remove = None
        for i, event in enumerate(target_state["calendar"]):
            if event["title"] == self.title and event["date"] == self.date:
                idx_to_remove = i
        # remove the event to be deleted
        if idx_to_remove is not None:
            target_state["calendar"].pop(idx_to_remove)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class AddToDoTask(Task):
    """
    Task to add a todo to the todo app.
    """

    todo_name: str
    is_done: bool

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        target_state["todo"].append({"title": self.todo_name, "done": self.is_done})
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class MarkToDoDoneTask(Task):
    """
    Mark todo item as done
    """

    todo_name: str

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        target_idx = None
        for i, todo_item in enumerate(target_state["todo"]):
            if self.todo_name == todo_item["title"]:
                new_todo_item = {"title": self.todo_name, "done": 1}
                target_idx = i
        if target_idx is None:
            raise ValueError(f"Todo item {self.todo_name} not found")
        target_state["todo"][target_idx] = new_todo_item
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        try:
            target_state = self.get_target_state(initial_state)
        except ValueError:
            return False
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class SendMessageTask(Task):
    to: str
    message: str
    # Retained for backwards compatibility and goal phrasing. Replies are
    # ignored by default when checking completion (the app's auto-reply is
    # random for anyone other than Alice/Bob), so this is not part of the
    # target state. Pass ``ignore_message_replies=False`` to
    # ``AppStateComparison`` to compare replies.
    expected_reply: str | None = None

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        now = datetime.now()
        # Format the datetime object into the desired string format
        formatted_time_string = now.strftime("%b %d, %I:%M%p")
        contact_idx = None
        for i, contact in enumerate(target_state["messenger"]):
            if contact["user"] == self.to:
                contact_idx = i
        if contact_idx is None:
            raise ValueError(f"Contact {self.to} not found in messenger app")
        messages = target_state["messenger"][contact_idx]["messages"]
        # Only the sent message is part of the target; the app's reply is
        # ignored by AppStateComparison (see ``ignore_message_replies``).
        messages.append([self.message, False, self.to, formatted_time_string])
        target_state["messenger"][contact_idx]["messages"] = messages
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        # Ignore the single auto-reply this contact appends to our one message.
        app_state_comparison = AppStateComparison(
            target_state, current_state, reply_contacts={self.to: 1}
        )
        return app_state_comparison.compare()


@dataclass
class SavePlaceTask(Task):
    """Save a place to the map at ``(latitude, longitude)``.

    ``tolerance_km`` optionally overrides the coordinate match radius used
    for reward scoring. Omit for the 10 km default. Example YAML:

        save_eiffel_tower_to_my_favorite_places:
          _target_: open_apps.tasks.tasks.SavePlaceTask
          goal: Save the Eiffel Tower to my favorite places
          name: Eiffel Tower
          latitude: 48.8584
          longitude: 2.2945
          tolerance_km: 1.0  # city-landmark precision

        save_france_to_my_favorite_places:
          ...
          tolerance_km: 100.0  # country-level pin
    """

    name: str
    latitude: float
    longitude: float
    tolerance_km: float | None = None

    def get_target_state(self, initial_state: dict) -> dict:
        """Define the target state for the task.

        Args:
            initial_state (dict): The initial state of all apps.
        """
        target_state = copy.deepcopy(initial_state)
        assert target_state["map"], "map must be populated"
        new_place = {"name": self.name, "coords": [self.latitude, self.longitude]}
        if target_state["map"][-1] != new_place:
            target_state["map"].append(new_place)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        kwargs = (
            {"coords_tolerance_km": self.tolerance_km}
            if self.tolerance_km is not None
            else {}
        )
        app_state_comparison = AppStateComparison(target_state, current_state, **kwargs)
        return app_state_comparison.compare()


@dataclass
class DeleteToDoTask(Task):
    """Click-only task: delete a todo item via the per-row remove button."""

    todo_name: str

    def get_target_state(self, initial_state: dict) -> dict:
        target_state = copy.deepcopy(initial_state)
        idx_to_remove = None
        for i, item in enumerate(target_state["todo"]):
            if item["title"] == self.todo_name:
                idx_to_remove = i
        if idx_to_remove is not None:
            target_state["todo"].pop(idx_to_remove)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


@dataclass
class RemoveLandmarkTask(Task):
    """Click-only task: remove a saved landmark via the per-row delete button."""

    name: str

    def get_target_state(self, initial_state: dict) -> dict:
        target_state = copy.deepcopy(initial_state)
        idx_to_remove = None
        for i, place in enumerate(target_state["map"]):
            if place["name"] == self.name:
                idx_to_remove = i
        if idx_to_remove is not None:
            target_state["map"].pop(idx_to_remove)
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        target_state = self.get_target_state(initial_state)
        app_state_comparison = AppStateComparison(target_state, current_state)
        return app_state_comparison.compare()


# Maps a target-app key to URL-path prefixes that count as "in that app".
# Mirrors open_apps.mcp.registry.APP_URL_PATHS; inlined here to keep the
# tasks package import light (avoids pulling in hydra/uvicorn).
_NAV_APP_URL_PREFIXES: dict[str, tuple[str, ...]] = {
    "todo": ("/todo",),
    "calendar": ("/calendar",),
    "messages": ("/messages",),
    "codeeditor": ("/codeeditor",),
    "map": ("/maps",),
}


@dataclass
class NavigateToAppTask(Task):
    """Click-only task: starting in ``source_app``, end up in ``target_app``.

    Reward = 1 once the page URL's path lives under the target app's URL
    prefix. Relies on the env injecting ``current_state['_url']`` before
    invoking this task's check.
    """

    source_app: str
    target_app: str

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        url = current_state.get("_url", "") if isinstance(current_state, dict) else ""
        if not url:
            return False
        try:
            from urllib.parse import urlparse

            path = urlparse(url).path or "/"
        except Exception:
            return False
        prefixes = _NAV_APP_URL_PREFIXES.get(self.target_app, (f"/{self.target_app}",))
        return any(
            path == p or path.startswith(p + "/") or path.rstrip("/") == p
            for p in prefixes
        )


@dataclass
class CompositeTask(Task):
    """A meta-task made of several sub-tasks that must *all* be satisfied.

    Longer-horizon goals ("add X to my calendar, also add it to my todo list,
    and message a friend about it") are expressed as an ordered list of
    ordinary :class:`Task` instances. Completion checking reuses the existing
    per-task logic: the combined target state is produced by threading each
    sub-task's ``get_target_state`` (``initial -> sub1 -> sub2 -> ...``), and
    the result is diffed against the observed state with the shared
    :class:`AppStateComparison`.

    Sub-tasks are instantiated by Hydra's recursive ``instantiate`` from the
    ``subtasks`` list in the task config, so no bespoke wiring is needed.

    Note: the naive alternative — calling each sub-task's
    ``check_if_task_is_complete`` and AND-ing the results — does *not* work,
    because each sub-task expects a state carrying only its own change and
    would flag the sibling sub-tasks' changes as spurious differences.
    """

    subtasks: list[Task]

    def __post_init__(self) -> None:
        """Instantiate any sub-tasks still in raw-config form.

        The task configs use ``_convert_: all`` so Hydra recursively
        instantiates the nested ``_target_`` sub-tasks into :class:`Task`
        objects. This is a safety net for the direct-construction path (e.g.
        building a ``CompositeTask`` from raw ``DictConfig``/``dict`` sub-task
        configs in a test or script): any sub-task that isn't already a
        ``Task`` is instantiated here. ``hydra`` is imported lazily to keep
        the ``tasks`` package import light (see ``add_tasks_to_browsergym``).
        """
        resolved: list[Task] = []
        for subtask in self.subtasks:
            if isinstance(subtask, Task):
                resolved.append(subtask)
                continue
            from hydra.utils import instantiate as _instantiate

            resolved.append(_instantiate(subtask))
        self.subtasks = resolved

    def _reply_contacts(self) -> dict[str, int]:
        """Count sent messages per contact, to tolerate their auto-replies."""
        counts: dict[str, int] = {}
        for subtask in self.subtasks:
            if isinstance(subtask, SendMessageTask):
                counts[subtask.to] = counts.get(subtask.to, 0) + 1
        return counts

    def get_target_state(self, initial_state: dict) -> dict:
        """Apply every sub-task's change in order to build the combined target.

        Each sub-task's ``get_target_state`` deep-copies the state it receives,
        so ``initial_state`` is never mutated.
        """
        state = initial_state
        for subtask in self.subtasks:
            state = subtask.get_target_state(state)
        return state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        if isinstance(current_state, DictConfig):
            current_state = OmegaConf.to_container(current_state, resolve=True)
        try:
            target_state = self.get_target_state(initial_state)
        except ValueError:
            # A sub-task referenced an item absent from the initial state
            # (e.g. marking a todo done that was never there). The composite
            # task cannot be satisfied against this state.
            return False
        app_state_comparison = AppStateComparison(
            target_state, current_state, reply_contacts=self._reply_contacts()
        )
        return app_state_comparison.compare()


if __name__ == "__main__":
    pass
