from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal, Optional
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


# Stands in for a checkout's random order id on both sides of a purchase diff.
# Deliberately the same literal the shop's `card_descriptor` uses as its format
# field, so a target descriptor is just the template left unformatted.
ORDER_ID_TOKEN = "{order_id}"

# Where a checkout's receipt lands, mirroring ``onlineshop_app.receipt_path``.
# Inlined rather than imported to keep the tasks package import light; a test
# pins the two together.
RECEIPT_PATH = "Receipts/OpenShop order {order_id}.pdf"

# The Files app's pinned "today" (``apps.files.today``, which follows
# ``apps.openbanking.as_of_date``): the ``deleted_on`` a delete stamps. Pinned
# here for the same reason ``BuyWithCardTask.descriptor`` mirrors the shop's.
FILES_TODAY = "2026-08-26"


def _files_row_key(row: dict):
    """``files_store.snapshot``'s order."""
    return (row["path"], bool(row["deleted"]), row["deleted_on"] or "", row["sha256"] or "")


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


class _FileTree:
    """The ``/files_all`` slice as the store would leave it after an action.

    Targets are built by replaying the store's own rules on the snapshot rows
    rather than by hand-editing them, because a single action can move more
    than one row: deleting the last file in a folder makes the store report
    the folder itself (an empty folder is a row of its own), and filling an
    empty folder removes that row again.
    """

    def __init__(self, files_slice: dict | None):
        rows = (files_slice or {}).get("files") or []
        self.live: dict[str, dict] = {}
        self.trash: list[dict] = []
        self.dirs: set[str] = set()
        for row in rows:
            row = dict(row)
            if row.get("deleted"):
                self.trash.append(row)
            elif row.get("kind") == "folder":
                self.dirs.add(row["path"])
                self.dirs.update(_ancestors(row["path"]))
            else:
                self.live[row["path"]] = row
                self.dirs.update(_ancestors(row["path"]))

    def file(self, path: str) -> dict:
        if path not in self.live:
            raise ValueError(f"no file {path!r} in the files slice")
        return self.live[path]

    def taken(self, path: str) -> bool:
        return path in self.live or path in self.dirs

    def free_path(self, folder: str, name: str) -> str:
        """``files_store._free_name``: ``name``, else ``stem (n)suffix``."""
        join = (lambda n: f"{folder}/{n}") if folder else (lambda n: n)
        if not self.taken(join(name)):
            return join(name)
        pure = PurePosixPath(name)
        stem, suffix = (pure.stem, pure.suffix) if pure.suffix and pure.stem else (name, "")
        n = 2
        while self.taken(join(f"{stem} ({n}){suffix}")):
            n += 1
        return join(f"{stem} ({n}){suffix}")

    def put(self, path: str, row: dict) -> None:
        self.live[path] = {**row, "path": path, "deleted": False, "deleted_on": None}
        self.dirs.update(_ancestors(path))

    def trashed(self, path: str, deleted_on: str | None = None) -> dict:
        matches = [
            r for r in self.trash
            if r["path"] == path and r.get("kind") != "folder"
            and (deleted_on is None or r["deleted_on"] == deleted_on)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{len(matches)} items in Recently Deleted were at {path!r}; "
                "a task needs exactly one (pass deleted_on to choose)"
            )
        return matches[0]

    def rows(self) -> list[dict]:
        out = list(self.live.values())
        for d in self.dirs:
            prefix = d + "/"
            if not any(p.startswith(prefix) for p in self.live) and not any(
                o.startswith(prefix) for o in self.dirs
            ):
                out.append(
                    {"path": d, "kind": "folder", "size": 0, "sha256": None,
                     "deleted": False, "deleted_on": None}
                )
        out.extend(self.trash)
        return sorted(out, key=_files_row_key)

    def into(self, state: dict) -> dict:
        state["files"] = {"files": self.rows()}
        return state


def _fmt_money(value: float) -> str:
    """Mirror of ``openbanking_app.main.fmt_money``.

    Inlined rather than imported for the same reason as
    ``_NAV_APP_URL_PREFIXES`` below: the tasks package stays free of the app
    modules and the hydra/uvicorn stack they drag in. The two have to agree
    exactly -- an overlimit notice is matched on its rendered text, so a
    thousands separator or a stray sign here is a failed diff.
    """
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


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
        normalize_purchases: bool = False,
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
        # Opt-in, and only correct for a task that actually checks out --
        # see ``_normalize_purchases`` for what it removes and why nothing
        # else should pay for it. ``CompositeTask`` sets it automatically when
        # one of its sub-tasks is a ``BuyWithCardTask`` or a
        # ``DeclinedCardPurchaseTask``.
        self.normalize_purchases = normalize_purchases

        self.state1 = self.preprocess(self.raw_state1)
        self.state2 = self.preprocess(self.raw_state2)

    def preprocess(self, state: dict) -> dict:
        # Drop keys we never compare *before* deep-copying: underscore-prefixed
        # env metadata (e.g. ``_url``), the (potentially large) code-editor
        # tree, and the UI library's story settings. Shallow-slicing first
        # avoids deep-copying data we're about to discard.
        #
        # ``ui_library`` is excluded for a different reason than
        # ``codeeditor``: it is a component gallery that exists to be poked
        # at, so an agent that wanders in and flips a variant knob while
        # working on a todo task would otherwise register a spurious diff and
        # fail an otherwise-correct episode. A task that actually targets the
        # gallery reads ``/uilibrary_all`` directly, the same way
        # ``EditFileTask`` reads ``/codeeditor_all``.
        state = {
            k: v
            for k, v in state.items()
            if not k.startswith("_") and k not in ("codeeditor", "ui_library")
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
        if self.normalize_purchases:
            state = self._normalize_purchases(state)
        state = self.sort_lists(state)
        state = self._sort_files(state)
        return state

    @staticmethod
    def _sort_files(state: dict) -> dict:
        files = state.get("files")
        if isinstance(files, dict) and isinstance(files.get("files"), list):
            files["files"] = sorted(files["files"], key=_files_row_key)
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

    def _normalize_purchases(self, state: dict) -> dict:
        """Drop the fields a shop checkout mints that no target can predict.

        Gated on ``normalize_purchases`` because the fields it removes are
        real signal for anything that does not buy: an ordinary task wants a
        spurious order or a spurious ledger row to fail the diff, ids and all.

        A checkout mints ``uuid4().hex[:8]`` as the order id and stamps the
        order with ``datetime.now()``, then posts a bank row whose description
        embeds that same order id (``card_descriptor``). None of the three is
        derivable from the initial state. Rather than drop the description --
        the only thing tying a charge to the order that caused it -- every
        order id present in the shop's slice is substituted with a fixed token
        in every ledger description. Target and observed then both read
        ``OPENAPPS SHOP {order_id}``, so the diff still asserts the charge
        names *an order that exists*, which is the part worth checking.

        Ledger ``id`` and ``position`` go too. Both are functions of the
        bank's internal numbering (``max(id) + 1``, ``min(position) - 1``);
        reproducing them in a target would pin the task to the app's insertion
        scheme rather than to what the purchase did.
        """
        shop = state.get("online_shop")
        # ``/onlineshop_all`` answers a dict, but ``get_current_state`` falls
        # back to ``[]`` when the probe fails and the captured state fixtures
        # predate the shop, so neither slice can be assumed present.
        orders = shop.get("orders") or [] if isinstance(shop, dict) else []
        bank = state.get("openbanking")
        txns = bank.get("transactions") or [] if isinstance(bank, dict) else []

        order_ids = [str(o["order_id"]) for o in orders if o.get("order_id")]
        if order_ids and txns:
            # Longest first, so an id that happens to prefix another cannot
            # half-match and leave a tail behind.
            pattern = re.compile(
                "|".join(re.escape(i) for i in sorted(order_ids, key=len, reverse=True)),
                re.IGNORECASE,
            )
            for txn in txns:
                if txn.get("description"):
                    txn["description"] = pattern.sub(ORDER_ID_TOKEN, txn["description"])

        for txn in txns:
            for key in ("id", "position"):
                txn.pop(key, None)

        # A checkout also writes a receipt named after the order id, and its
        # bytes carry the checkout time. Same substitution as the ledger, and
        # only for ids of orders that exist -- a receipt naming no order still
        # fails -- with the bytes left out of the diff.
        files = state.get("files")
        rows = files.get("files") or [] if isinstance(files, dict) else []
        receipts = {RECEIPT_PATH.format(order_id=i) for i in order_ids}
        receipts.add(RECEIPT_PATH.format(order_id=ORDER_ID_TOKEN))
        for row in rows:
            if row.get("path") in receipts:
                row["path"] = RECEIPT_PATH.format(order_id=ORDER_ID_TOKEN)
                row["size"] = row["sha256"] = None

        for order in orders:
            for key in ("order_id", "date"):
                order.pop(key, None)

        # Observed orders come back in DB insertion order and target orders in
        # append order; those agree today, but sorting costs nothing and stops
        # a seeded order from failing the diff on position alone.
        orders.sort(key=self._order_sort_key)
        return state

    @staticmethod
    def _order_sort_key(order: dict):
        skus = sorted(str(item.get("sku", "")) for item in order.get("items") or [])
        return (float(order.get("total") or 0.0), tuple(skus))

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

    def _files_match(self) -> bool:
        """Compare the files slices exactly.

        The diff below compares strings through ``StringSimilarityOperator``,
        which forgives case and punctuation -- right for a todo title, wrong
        for a file name, which is an address: "Lease-2026.pdf" is not the file
        the task asked for. Every field of a row is machine-made except the
        names an agent types, so the slice compares by equality.

        A target row with neither ``size`` nor ``sha256`` accepts any bytes at
        its path: a downloaded statement is pinned by name (the bank renders
        it), and a receipt carries the checkout time.
        """
        target = (self.state1.get("files") or {}).get("files")
        observed = (self.state2.get("files") or {}).get("files")
        if target is None or observed is None:
            return target is None and observed is None
        by_key = {
            (r["path"], r["deleted"], r["deleted_on"], r["kind"]): r for r in observed
        }
        resolved = []
        for row in target:
            if row.get("size") is None and row.get("sha256") is None and row.get("kind") != "folder":
                seen = by_key.get((row["path"], row["deleted"], row["deleted_on"], row["kind"]))
                if seen is not None:
                    row = {**row, "size": seen["size"], "sha256": seen["sha256"]}
            resolved.append(row)
        return sorted(resolved, key=_files_row_key) == sorted(observed, key=_files_row_key)

    def compare(self) -> bool:
        # check that both states have the same apps
        if set(self.state1.keys()) != set(self.state2.keys()):
            print("States have different apps")
            return False

        if self.reply_contacts:
            self._truncate_message_replies()

        if not self._files_match():
            print("===Files differ")
            return False
        # Matched exactly above; kept out of the fuzzy diff, which would trip
        # on the content-agnostic rows it already accepted.
        state1 = {k: v for k, v in self.state1.items() if k != "files"}
        state2 = {k: v for k, v in self.state2.items() if k != "files"}
        return self.are_dicts_similar(state1, state2, self.coords_tolerance_km)


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


@dataclass
class BuyWithCardTask(Task):
    """Cross-app task: buy ``sku`` in the shop, paying with a bank card.

    The two apps meet at checkout -- the shop charges the card through
    ``openbanking_app.authorize_card_purchase`` -- so this is the first task
    whose scorable delta lands in *two* app slices at once, and the first that
    makes the bank's state move at all. What it measures is whether an agent
    can carry a value across apps: the PAN, expiry and CVV are only on the
    card's page in OpenBanking, each behind a click that unmasks them, and the
    checkout form will not take an order without all three.

    Everything the target needs is stated in the task config rather than read
    back out of the apps, for the same reason the rest of
    ``config/tasks/openbanking.yaml`` works that way: ``/onlineshop_all`` omits
    the catalog unless asked, so ``unit_price`` cannot be looked up from the
    state being diffed, and pinning it in config makes a catalog edit break the
    task loudly instead of silently rescoring it.

    ``descriptor`` and ``charge_type`` mirror ``apps.onlineshop.card_descriptor``
    and ``apps.openbanking.purchase_type``; a run that overrides either has to
    override it here too, or the ledger row will not match.
    """

    sku: str
    unit_price: float
    # Last four of the card the order must go on -- both the shop's
    # ``card_last4`` on the order and the bank account the charge lands on are
    # matched against it, so paying with the wrong card fails even though the
    # purchase itself succeeded.
    card_last4: str
    # Shipping details, quoted verbatim in the goal. Compared through
    # StringSimilarityOperator, so case and punctuation are free.
    ship_to_name: str
    ship_to_address: str
    quantity: int = 1
    # Chosen product options, e.g. ``{"color": "navy"}``. Must match what the
    # shop stores on the order line, which is every option the product has.
    options: Optional[dict] = None
    descriptor: str = "OPENAPPS SHOP {order_id}"
    charge_type: str = "Card"
    # The bank's grace band, mirroring ``apps.openbanking.overlimit_grace``:
    # dollars a charge may exceed ``available_credit`` by and still authorize.
    # A purchase that lands inside it posts a *second* ledger row naming the
    # overage, which is why this is pinned here rather than inferred -- the
    # target has to carry that row or the diff fails on a charge the app was
    # always going to approve.
    overlimit_grace: float = 10.0
    # Wording of that second row, mirroring ``overlimit_type`` and
    # ``overlimit_description`` in the bank's content pack. A run on the
    # german/mandarin packs has to override both.
    overlimit_type: str = "Fee"
    overlimit_description: str = "OVERLIMIT NOTICE - credit limit exceeded by {amount}"

    @property
    def total(self) -> float:
        return round(float(self.unit_price) * int(self.quantity), 2)

    def _card_account(self, accounts: list) -> dict:
        for account in accounts:
            number = str(account.get("account_number") or "")
            if account.get("kind") == "credit_card" and number.endswith(self.card_last4):
                return account
        raise ValueError(f"no credit card ending {self.card_last4} in the initial state")

    def get_target_state(self, initial_state: dict) -> dict:
        target_state = copy.deepcopy(initial_state)
        shop = target_state.get("online_shop")
        bank = target_state.get("openbanking")
        if not isinstance(shop, dict) or not isinstance(bank, dict):
            raise ValueError("BuyWithCardTask needs both the shop and the bank")

        total = self.total
        options = dict(self.options or {})
        account = self._card_account(bank.get("accounts") or [])

        # Refuse to build a target for a charge the bank would decline. Past
        # the grace band `authorize_card_purchase` posts only a declined row
        # and the shop writes no order, so the target below would describe a
        # state the app cannot reach. That case is `DeclinedCardPurchaseTask`.
        headroom = round(float(account.get("available_credit") or 0.0), 2)
        over_by = round(total - headroom, 2)
        grace = round(float(self.overlimit_grace), 2)
        if over_by > grace:
            raise ValueError(
                f"{total} exceeds the card's available credit of {headroom} by "
                f"{over_by}, past the {grace} overlimit grace -- the bank "
                f"declines this charge, so use DeclinedCardPurchaseTask"
            )

        # Checkout empties the lines it bought. When the item starts in the
        # cart this removes it; when the agent adds it during the episode
        # (the usual case -- the shop's default cart is empty) the initial and
        # final carts are both empty and there is nothing to drop.
        shop["cart"] = [
            row
            for row in shop.get("cart") or []
            if not (row.get("sku") == self.sku and (row.get("options") or {}) == options)
        ]

        shop.setdefault("orders", []).append(
            {
                "order_id": ORDER_ID_TOKEN,
                "name": self.ship_to_name,
                "address": self.ship_to_address,
                # Both dropped by AppStateComparison._normalize_purchases; kept
                # here so the target has the same shape as the observed order.
                "date": None,
                "status": "Processing",
                "total": total,
                "card_last4": self.card_last4,
                "items": [
                    {
                        "sku": self.sku,
                        "options": options,
                        "quantity": int(self.quantity),
                        "unit_price": float(self.unit_price),
                    }
                ],
            }
        )

        # A card carries what is owed as a negative present balance, so the
        # charge pushes it down and eats the same amount of headroom. The
        # credit limit does not move, which is what makes leaving it in the
        # diff worth something.
        account["present_balance"] = round(
            float(account.get("present_balance") or 0.0) - total, 2
        )
        account["available_credit"] = round(headroom - total, 2)

        transactions = bank.setdefault("transactions", [])
        transactions.append(
            {
                # id/position are dropped by the normalizer -- see there.
                "id": None,
                "account_id": account.get("id"),
                "position": None,
                # Charges post pending, so no wall-clock date or running
                # balance reaches the payload.
                "date": None,
                "description": self.descriptor.format(order_id=ORDER_ID_TOKEN),
                "type": self.charge_type,
                "amount": -total,
                "balance": None,
            }
        )
        if isinstance(target_state.get("files"), dict):
            # The receipt checkout writes into Files. Content-agnostic (no
            # size, no checksum): its bytes carry the checkout time.
            tree = _FileTree(target_state["files"])
            tree.put(
                RECEIPT_PATH.format(order_id=ORDER_ID_TOKEN),
                {"kind": "application/pdf", "size": None, "sha256": None},
            )
            tree.into(target_state)
        if over_by > 0:
            # The charge first, then the notice -- `authorize_card_purchase`
            # inserts them in that order and `/openbanking_all` sorts by id,
            # so appending in the same order is what the payload comes back
            # as. The notice carries no money of its own; it is a marker that
            # the limit was broken, and the overage is named in its text.
            transactions.append(
                {
                    "id": None,
                    "account_id": account.get("id"),
                    "position": None,
                    "date": None,
                    "description": self.overlimit_description.format(
                        amount=_fmt_money(over_by)
                    ),
                    "type": self.overlimit_type,
                    "amount": 0.0,
                    "balance": None,
                }
            )
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        try:
            target_state = self.get_target_state(initial_state)
        except ValueError:
            return False
        app_state_comparison = AppStateComparison(
            target_state, current_state, normalize_purchases=True
        )
        return app_state_comparison.compare()


@dataclass
class DeclinedCardPurchaseTask(Task):
    """Cross-app task: try to buy ``sku`` on a card that cannot cover it.

    The mirror image of ``BuyWithCardTask``. Past the grace band
    ``authorize_card_purchase`` returns ``insufficient_credit`` and the shop
    bounces back to ``/onlineshop/checkout`` with the reason in the query
    string, writing no order -- so the card's balances do not move and the
    cart keeps the line it was about to buy. What the agent is being measured
    on is reaching that wall -- finding the product, carrying the PAN, expiry
    and CVV over from the bank, and submitting a checkout that gets refused.

    The target therefore has two parts:

    - the **cart line**, which only exists because the agent put it there and
      checkout refused to clear it -- this is what fails a do-nothing run;
    - the **declined authorization** the bank posts on the card: one
      zero-amount row naming the merchant and the attempted total. This is
      what fails a run that filled the cart and never submitted the checkout.
      Without it the two leave identical state, and the shortfall a paired
      todo asks for is computable from the cart total and the card page alone.

    A second refused attempt posts a second row, and fails the diff the same
    way a second message to a contact would. Detail typos (wrong CVV, wrong
    expiry) post nothing, so fixing a typo and resubmitting is free.

    ``title`` and ``unit_price`` are pinned here for the reason the rest of
    the purchase tasks pin their figures: ``/onlineshop_all`` reports the cart
    with the catalog's title and price joined in, but omits the catalog
    itself, so neither can be looked up from the state being diffed.
    """

    sku: str
    unit_price: float
    # The catalog title, which `/onlineshop_all` joins onto every cart line.
    title: str
    # Last four of the card the checkout must be attempted against: the
    # account whose headroom decides whether this really declines, and the
    # one whose ledger carries the declined row.
    card_last4: str
    quantity: int = 1
    options: Optional[dict] = None
    # Mirrors `apps.openbanking.overlimit_grace`, same as BuyWithCardTask. The
    # total has to clear `available_credit` *and* this, or the bank approves
    # and the task is describing something that cannot happen.
    overlimit_grace: float = 10.0
    # The declined row, mirroring `declined_type` / `declined_description` in
    # the bank's content pack and the shop's `card_descriptor` with the order
    # id dropped. A run on the german/mandarin packs has to override the two
    # strings, the same as BuyWithCardTask's overlimit wording.
    merchant: str = "OPENAPPS SHOP"
    declined_type: str = "Declined"
    declined_description: str = "DECLINED - {merchant} {amount} - insufficient available credit"

    @property
    def total(self) -> float:
        return round(float(self.unit_price) * int(self.quantity), 2)

    def shortfall(self, initial_state: dict) -> float:
        """How far past the card's available credit this purchase lands.

        The figure a reporting sub-task asks the agent for. The decline
        message deliberately does not name it -- see
        ``charge_insufficient_message`` in the bank's content pack -- so an
        agent has to read the card's available credit and subtract.
        """
        bank = initial_state.get("openbanking")
        if not isinstance(bank, dict):
            raise ValueError("DeclinedCardPurchaseTask needs the bank")
        account = self._card_account(bank.get("accounts") or [])
        headroom = round(float(account.get("available_credit") or 0.0), 2)
        return round(self.total - headroom, 2)

    def _card_account(self, accounts: list) -> dict:
        for account in accounts:
            number = str(account.get("account_number") or "")
            if account.get("kind") == "credit_card" and number.endswith(self.card_last4):
                return account
        raise ValueError(f"no credit card ending {self.card_last4} in the initial state")

    def get_target_state(self, initial_state: dict) -> dict:
        target_state = copy.deepcopy(initial_state)
        shop = target_state.get("online_shop")
        bank = target_state.get("openbanking")
        if not isinstance(shop, dict) or not isinstance(bank, dict):
            raise ValueError("DeclinedCardPurchaseTask needs both the shop and the bank")

        total = self.total
        options = dict(self.options or {})
        account = self._card_account(bank.get("accounts") or [])

        # Refuse to build a target for a charge the bank would actually take.
        # Inside the grace band the purchase goes through and this target --
        # which says no order exists -- would be scoring the opposite of what
        # the app does. That case is `BuyWithCardTask`.
        headroom = round(float(account.get("available_credit") or 0.0), 2)
        over_by = round(total - headroom, 2)
        grace = round(float(self.overlimit_grace), 2)
        if over_by <= grace:
            raise ValueError(
                f"{total} fits inside the card's available credit of {headroom} "
                f"plus {grace} of grace -- the bank approves this charge, so "
                f"use BuyWithCardTask"
            )

        # The cart line the refused checkout leaves behind. `/onlineshop/cart/add`
        # inserts selected=True and checkout only clears lines it actually
        # bought, so a declined attempt comes back to a cart that still has it.
        cart = shop.setdefault("cart", [])
        for row in cart:
            if row.get("sku") == self.sku and (row.get("options") or {}) == options:
                # Already seeded in the cart: adding again folds into the line.
                row["quantity"] = int(self.quantity)
                row["selected"] = True
                break
        else:
            cart.append(
                {
                    "sku": self.sku,
                    "title": self.title,
                    "options": options,
                    "quantity": int(self.quantity),
                    "selected": True,
                    "unit_price": float(self.unit_price),
                }
            )

        # The record of the refused attempt, on the card it was attempted on.
        # Appended last because `/openbanking_all` sorts by id and this is the
        # newest row; `id` and `position` are dropped by the normalizer.
        bank.setdefault("transactions", []).append(
            {
                "id": None,
                "account_id": account.get("id"),
                "position": None,
                "date": None,
                "description": self.declined_description.format(
                    merchant=self.merchant, amount=_fmt_money(total)
                ),
                "type": self.declined_type,
                "amount": 0.0,
                "balance": None,
            }
        )

        # Everything else is left exactly as it was found. Orders and the card's
        # balances stay untouched on purpose: a target that merely *omitted*
        # them would pass for a run that bought the thing on a second card.
        return target_state

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        try:
            target_state = self.get_target_state(initial_state)
        except ValueError:
            return False
        # `normalize_purchases` is on for the declined row's `id` and
        # `position`, which follow the bank's numbering. A spurious order still
        # fails: the normalizer drops its id and date, not the order itself.
        app_state_comparison = AppStateComparison(
            target_state, current_state, normalize_purchases=True
        )
        return app_state_comparison.compare()


# ---------------------------------------------------------------------------
# Files. Every target replays the store's own rules on the ``/files_all`` rows
# (see ``_FileTree``), and every path is compared exactly (see
# ``AppStateComparison._files_match``).


class _FileTask(Task):
    def get_target_state(self, initial_state: dict) -> dict:
        target_state = copy.deepcopy(initial_state)
        if not isinstance(target_state.get("files"), dict):
            raise ValueError(f"{type(self).__name__} needs the files slice")
        tree = _FileTree(target_state["files"])
        self.apply(tree)
        return tree.into(target_state)

    def apply(self, tree: _FileTree) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        if isinstance(current_state, DictConfig):
            current_state = OmegaConf.to_container(current_state, resolve=True)
        try:
            target_state = self.get_target_state(initial_state)
        except ValueError:
            return False
        return AppStateComparison(target_state, current_state).compare()


@dataclass
class DeleteFileTask(_FileTask):
    """Move ``path`` to Recently Deleted -- not purge it: delete is the
    reversible action, and a file emptied out of the bin too fails."""

    path: str
    # Mirrors ``apps.files.today``; see ``FILES_TODAY``.
    deleted_on: str = FILES_TODAY

    def apply(self, tree: _FileTree) -> None:
        row = tree.file(self.path)
        del tree.live[self.path]
        tree.trash.append({**row, "deleted": True, "deleted_on": self.deleted_on})


@dataclass
class RestoreFileTask(_FileTask):
    """Put ``path`` back from Recently Deleted. It returns to where it was
    deleted from, or beside a file now holding that name with the store's
    " (2)" -- either way the target says exactly where."""

    path: str
    # Only needed when several deleted items shared ``path``.
    deleted_on: Optional[str] = None

    def apply(self, tree: _FileTree) -> None:
        row = tree.trashed(self.path, self.deleted_on)
        tree.trash.remove(row)
        parent, _, name = self.path.rpartition("/")
        tree.put(tree.free_path(parent, name), row)


@dataclass
class PermanentlyDeleteTask(_FileTask):
    """Delete ``path`` from Recently Deleted for good."""

    path: str
    deleted_on: Optional[str] = None

    def apply(self, tree: _FileTree) -> None:
        tree.trash.remove(tree.trashed(self.path, self.deleted_on))


@dataclass
class RenameFileTask(_FileTask):
    """Rename ``path`` to ``new_name`` in place. The store refuses a taken
    name rather than suffixing it, so a target for one is a config error."""

    path: str
    new_name: str

    def apply(self, tree: _FileTree) -> None:
        row = tree.file(self.path)
        parent = self.path.rpartition("/")[0]
        dest = f"{parent}/{self.new_name}" if parent else self.new_name
        if dest != self.path and tree.taken(dest):
            raise ValueError(f"{dest!r} is taken; the store refuses the rename")
        del tree.live[self.path]
        tree.put(dest, row)


@dataclass
class MoveFileTask(_FileTask):
    """Move ``path`` into the existing ``folder``, suffixing on a clash."""

    path: str
    folder: str

    def apply(self, tree: _FileTree) -> None:
        row = tree.file(self.path)
        if self.folder not in tree.dirs:
            raise ValueError(f"no folder {self.folder!r} in the files slice")
        del tree.live[self.path]
        tree.put(tree.free_path(self.folder, self.path.rpartition("/")[2]), row)


@dataclass
class DownloadStatementTask(_FileTask):
    """Save ``account``'s statement for ``month`` (``"YYYY-MM"``) into Files.

    The file is pinned by its path -- account name and month -- not its
    bytes: the bank's renderer is the one source of those and is
    deterministic, so the name already says which statement it is. The name
    comes from ``statements.statement_path`` and the month's rows from
    ``statements.statement_rows``, the functions the bank's Statements page
    uses, so the task and the page cannot disagree about which months exist.

    ``account`` is the account's name or the last digits of its number.
    """

    account: str
    month: str

    def _account(self, initial_state: dict) -> dict:
        bank = initial_state.get("openbanking")
        accounts = bank.get("accounts") or [] if isinstance(bank, dict) else []
        wanted = str(self.account).strip()
        matches = [
            a for a in accounts
            if a.get("name") == wanted
            or (wanted.isdigit() and str(a.get("account_number") or "").endswith(wanted))
        ]
        if len(matches) != 1:
            raise ValueError(f"{self.account!r} matches {len(matches)} bank accounts")
        return matches[0]

    def expected_path(self, initial_state: dict) -> str:
        # Imported here: the bank package pulls in its app; scoring a task of
        # any other kind should not.
        from open_apps.apps.openbanking_app import statements

        month = statements.parse_month(self.month)
        if month is None:
            raise ValueError(f"month must be YYYY-MM, not {self.month!r}")
        account = self._account(initial_state)
        txns = [
            t for t in initial_state["openbanking"].get("transactions") or []
            if t.get("account_id") == account.get("id")
        ]
        if not statements.statement_rows(txns, month):
            raise ValueError(
                f"{account.get('name')} has no posted rows in {self.month}: the bank offers no statement"
            )
        return statements.statement_path(account["name"], month)

    def get_target_state(self, initial_state: dict) -> dict:
        self._path = self.expected_path(initial_state)
        return super().get_target_state(initial_state)

    def apply(self, tree: _FileTree) -> None:
        if tree.taken(self._path):
            raise ValueError(f"{self._path!r} is already in Files; downloading it changes nothing")
        tree.put(self._path, {"kind": "application/pdf", "size": None, "sha256": None})


# Maps a target-app key to URL-path prefixes that count as "in that app".
# Mirrors open_apps.mcp.registry.APP_URL_PATHS; inlined here to keep the
# tasks package import light (avoids pulling in hydra/uvicorn).
_NAV_APP_URL_PREFIXES: dict[str, tuple[str, ...]] = {
    "todo": ("/todo",),
    "calendar": ("/calendar",),
    "messages": ("/messages",),
    "codeeditor": ("/codeeditor",),
    "map": ("/maps",),
    "uilibrary": ("/uilibrary",),
    "openbanking": ("/openbanking",),
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

    def _has_purchase(self) -> bool:
        """Whether a sub-task checks out, and so mints ids no target can predict."""
        return any(
            isinstance(subtask, (BuyWithCardTask, DeclinedCardPurchaseTask))
            for subtask in self.subtasks
        )

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
            target_state,
            current_state,
            reply_contacts=self._reply_contacts(),
            normalize_purchases=self._has_purchase(),
        )
        return app_state_comparison.compare()


@dataclass
class EditFileTask(Task):
    """Edit (and save) a file in the Code Editor app.

    Because the Code Editor state is intentionally excluded from
    ``AppStateComparison`` (see ``AppStateComparison.preprocess``), this
    task implements its own reward logic that walks the Code Editor file
    tree directly (the JSON returned by the ``/codeeditor_all`` endpoint).

    Reward = 1 once the file at ``file_path`` in the *current* Code Editor
    state satisfies the content requirement **and** its content differs
    from the *initial* Code Editor state. The latter check ensures an
    unsaved / unchanged file does not earn reward.

    Provide exactly one content requirement:
      * ``expected_content`` — the file's full contents must match this
        (whitespace-normalized), or
      * ``required_fragment`` — this text fragment must appear in the file.

    This class also handles the "create a new file" case: if the file did
    not exist in the initial state, any matching current content counts as
    a change.
    """

    file_path: str
    expected_content: str | None = None
    required_fragment: str | None = None

    def __post_init__(self) -> None:
        if (self.expected_content is None) == (self.required_fragment is None):
            raise ValueError(
                "EditFileTask requires exactly one of expected_content or "
                "required_fragment"
            )

    @staticmethod
    def _normalize(text: str) -> str:
        # Normalize line endings and strip trailing whitespace per line so
        # editor-added newlines / indentation noise don't cause spurious
        # mismatches.
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return "\n".join(line.rstrip() for line in lines).strip()

    def _find_file_content(self, tree: dict, target_path: str) -> str | None:
        """Return the content of the file at ``target_path`` in the file
        tree, or ``None`` if no such file exists."""
        if not isinstance(tree, dict):
            return None
        for child in tree.get("children", []) or []:
            if child.get("type") == "file" and child.get("path") == target_path:
                return child.get("content", "")
            if child.get("type") == "folder":
                found = self._find_file_content(child, target_path)
                if found is not None:
                    return found
        return None

    def _content_matches(self, content: str) -> bool:
        if self.expected_content is not None:
            return self._normalize(content) == self._normalize(self.expected_content)
        if self.required_fragment is not None:
            return self._normalize(self.required_fragment) in self._normalize(content)
        raise ValueError(
            "EditFileTask requires either 'expected_content' or 'required_fragment'"
        )

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        if isinstance(current_state, DictConfig):
            current_state = OmegaConf.to_container(current_state, resolve=True)
        if isinstance(initial_state, DictConfig):
            initial_state = OmegaConf.to_container(initial_state, resolve=True)

        current_tree = current_state.get("codeeditor") or {}
        current_content = self._find_file_content(current_tree, self.file_path)
        if current_content is None:
            # The target file does not exist in the Code Editor.
            return False

        if not self._content_matches(current_content):
            # Wrong file / wrong contents.
            return False

        # Require the file to have actually changed vs. the initial state so
        # that an unsaved or unchanged file does not earn reward.
        initial_tree = initial_state.get("codeeditor") or {}
        initial_content = self._find_file_content(initial_tree, self.file_path)
        if initial_content is not None and self._normalize(
            initial_content
        ) == self._normalize(current_content):
            return False

        return True


@dataclass
class AbstentionTask(Task):
    """Three-way task: the right response is to act, clarify, or stop.

    ``expected_outcome`` is the correct response:
      * ``act`` — carry out ``action_task`` (an ordinary :class:`Task`);
      * ``clarify`` — ask a question (``send_msg_to_user``), e.g. because
        the request matches several items;
      * ``stop`` — report the request can't be done (``report_infeasible``),
        e.g. because the item doesn't exist.

    ``check_if_task_is_complete`` recognizes a concrete act. For ``clarify``
    and ``stop`` tasks any change :class:`AppStateComparison` detects counts
    as an (incorrect) act, so every wrong guess or near-match substitution is
    caught without a verifier per mistake; True then means a wrong act, not
    success. ``AppStateComparison`` ignores the code editor and UI library.
    ``OpenAppsTask.validate`` combines this with :meth:`explicit_outcome` to
    score the episode. With BrowserGym's ``wait_for_user_message=True`` a
    user reply follows each question, so ``clarify`` is never observed.
    """

    expected_outcome: Literal["act", "clarify", "stop"]
    action_task: Task | None = None

    def __post_init__(self) -> None:
        if self.expected_outcome not in ("act", "clarify", "stop"):
            raise ValueError("expected_outcome must be act, clarify, or stop")
        if self.action_task is not None and not isinstance(self.action_task, Task):
            # Safety net for raw configs (see CompositeTask.__post_init__).
            from hydra.utils import instantiate as _instantiate

            self.action_task = _instantiate(self.action_task)
        if self.expected_outcome == "act" and self.action_task is None:
            raise ValueError("act tasks require action_task")

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        if self.expected_outcome == "act":
            return self.action_task.check_if_task_is_complete(
                initial_state, current_state, current_url
            )
        if isinstance(current_state, DictConfig):
            current_state = OmegaConf.to_container(current_state, resolve=True)
        # safe_get_json returns [] when a probe fails. Treat an app that was
        # populated but now reads empty as "unknown", not as a (terminal) act.
        if any(initial_state.get(app) and current_state.get(app) == [] for app in initial_state):
            return False
        return not AppStateComparison(initial_state, current_state).compare()

    @staticmethod
    def explicit_outcome(chat_messages: list[dict]) -> str | None:
        """Return ``clarify``/``stop`` if the agent ended its turn explicitly.

        Only messages after the last user message count, so BrowserGym's
        assistant greeting (sent before the goal) is ignored. BrowserGym
        records ``send_msg_to_user`` with role ``assistant`` and
        ``report_infeasible`` with role ``infeasible``.
        """
        last_user = max(
            (
                i
                for i, message in enumerate(chat_messages)
                if message.get("role") in ("user", "user_image")
            ),
            default=-1,
        )
        outcome = None
        for message in chat_messages[last_user + 1 :]:
            if not str(message.get("message", "")).strip():
                continue
            if message.get("role") == "assistant":
                outcome = "clarify"
            elif message.get("role") == "infeasible":
                outcome = "stop"
        return outcome


@dataclass
class AnswerTask(Task):
    """The agent has to *tell* the user something, and change nothing.

    "What did I pay for the label printer?" leaves no trace in any app, so
    this is scored on the agent's reply: the ``send_msg_to_user`` text, the
    channel :class:`AbstentionTask` reads for a clarifying question. It
    passes when the latest reply after the goal contains every entry of
    ``must_include`` and the app state is unchanged.

    Matching is forgiving where people are: an entry that is a number matches
    any number in the reply with the same value ("99.90" matches "$99.90"
    and "99.9 dollars", not "199.90"); anything else is a case-insensitive
    substring. ``check_if_task_is_complete`` sees no chat, so on its own it
    never passes -- BrowserGym scores this through :meth:`check_reply`.
    """

    must_include: list[str] = field(default_factory=list)

    _NUMBER = re.compile(r"(?<![\d.])\d[\d,]*(?:\.\d+)?")

    @staticmethod
    def reply(chat_messages: list[dict]) -> str | None:
        """The agent's latest ``send_msg_to_user`` text after the goal."""
        last_user = max(
            (i for i, m in enumerate(chat_messages) if m.get("role") in ("user", "user_image")),
            default=-1,
        )
        replies = [
            str(m.get("message", "")).strip()
            for m in chat_messages[last_user + 1:]
            if m.get("role") == "assistant" and str(m.get("message", "")).strip()
        ]
        return replies[-1] if replies else None

    def _contains(self, reply: str, wanted: str) -> bool:
        wanted = str(wanted).strip()
        try:
            value = float(wanted.replace(",", ""))
        except ValueError:
            return " ".join(wanted.split()).casefold() in " ".join(reply.split()).casefold()
        return any(
            math.isclose(float(n.replace(",", "")), value, abs_tol=1e-9)
            for n in self._NUMBER.findall(reply)
        )

    def check_reply(self, reply: str | None, initial_state: dict, current_state: dict) -> bool:
        if not reply or not all(self._contains(reply, w) for w in self.must_include):
            return False
        if isinstance(current_state, DictConfig):
            current_state = OmegaConf.to_container(current_state, resolve=True)
        return AppStateComparison(initial_state, current_state).compare()

    def check_if_task_is_complete(
        self, initial_state: dict, current_state: dict, current_url: str | None = None
    ) -> bool:
        return False


if __name__ == "__main__":
    pass
