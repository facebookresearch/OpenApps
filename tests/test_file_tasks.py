"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

File tasks, scored against real episodes.

Each task in ``config/tasks/files.yaml`` is played through the actual routes
with a ``TestClient`` -- the same posts the Files UI and the bank make -- and
the resulting cross-app state is scored. A correct episode has to pass; the
near-misses an agent would plausibly make (the wrong file, one extra delete,
the wrong month, the March reading instead of August's) have to fail.

Also here: the files slice in the comparison itself (paths compare exactly,
receipts follow purchases), and a guard that every task already shipped still
builds a target that leaves the files slice alone.
"""

import contextlib
import io
from pathlib import Path
from urllib.parse import quote

import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
    reset_all_apps,
)
from open_apps.tasks.tasks import (
    ORDER_ID_TOKEN,
    AppStateComparison,
    DeleteFileTask,
    DownloadStatementTask,
    MoveFileTask,
    RenameFileTask,
    RestoreFileTask,
)

_TASKS = OmegaConf.load(config_dir() / "tasks" / "files.yaml")
SAVINGS = 1  # BUS SELECT SAVINGS (...8891): the account with July rows
JULY_SAVINGS = "Statements/BUS SELECT SAVINGS (...8891) — July 2026.pdf"


@pytest.fixture(scope="module")
def apps_cfg(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("file_tasks")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return cfg.apps


@pytest.fixture(scope="module")
def client(apps_cfg):
    return TestClient(app)


@pytest.fixture
def fresh(client, apps_cfg, monkeypatch):
    """Reset every app, and return a reader for the cross-app state."""
    from open_apps import state as state_module

    reset_all_apps(apps_cfg)

    def fetch(url, *args, **kwargs):
        response = client.get(url.removeprefix("http://testserver"))
        return response.json() if response.status_code == 200 else None

    monkeypatch.setattr(state_module, "safe_get_json", fetch)
    return lambda: state_module.get_current_state("http://testserver")


def _task(key):
    return instantiate(_TASKS[key])


def _passes(task, initial, current):
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


def _trash_name(client, path):
    """The trash name the Recently Deleted page posts for ``path``."""
    from open_apps import files_store

    return next(e.trash_name for e in files_store.list_trash() if e.path == path)


def _delete(client, path):
    assert client.post("/files/delete", data={"path": path}, follow_redirects=False).status_code in (200, 303)


def _restore(client, path):
    data = {"trash_name": _trash_name(client, path)}
    assert client.post("/files/restore", data=data, follow_redirects=False).status_code in (200, 303)


def _purge(client, path):
    data = {"trash_name": _trash_name(client, path), "confirm": "1"}
    assert client.post("/files/purge", data=data, follow_redirects=False).status_code in (200, 303)


def _rename(client, path, new_name):
    data = {"path": path, "new_name": new_name}
    assert client.post("/files/rename", data=data, follow_redirects=False).status_code in (200, 303)


def _move(client, path, folder):
    data = {"path": path, "folder": folder}
    assert client.post("/files/move", data=data, follow_redirects=False).status_code in (200, 303)


def _download_statement(client, account_id, month):
    response = client.post(f"/openbanking/accounts/{account_id}/statements/{month}/download")
    assert response.status_code in (200, 303), response.text[:200]


class TestTaskSet:
    def test_every_task_has_three_phrasings(self):
        bases = [k for k in _TASKS if "__" not in k]
        assert len(bases) >= 8
        for base in bases:
            styles = {_TASKS[k].goal_style for k in _TASKS if k.startswith(base + "__")}
            assert styles == {"casual", "formal", "unrelated_context"}, base

    def test_phrasings_keep_the_scored_fields(self):
        for key in _TASKS:
            if "__" not in key:
                continue
            base = dict(_TASKS[key.split("__")[0]])
            variant = dict(_TASKS[key])
            for field in ("goal", "goal_style"):
                base.pop(field, None)
                variant.pop(field, None)
            assert variant == base, key

    def test_not_in_the_canonical_inventory(self):
        """Like openbanking.yaml: all_tasks is the paper's inventory."""
        assert "files" not in str(OmegaConf.load(config_dir() / "tasks" / "all_tasks.yaml"))

    def test_composes_as_a_task_group(self, tmp_path):
        with initialize(version_base=None, config_path="../config/"):
            cfg = compose(config_name="config", overrides=["tasks=files", f"logs_dir={tmp_path}"])
        assert set(cfg.tasks) == set(_TASKS)


class TestDeleteFile:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("delete_packing_list"), fresh()
        _delete(client, "Documents/Packing List.txt")
        assert _passes(task, initial, fresh())

    def test_wrong_file_fails(self, client, fresh):
        task, initial = _task("delete_packing_list"), fresh()
        _delete(client, "Documents/Parking Permit 2026.png")
        assert not _passes(task, initial, fresh())

    def test_an_extra_delete_fails(self, client, fresh):
        task, initial = _task("delete_packing_list"), fresh()
        _delete(client, "Documents/Packing List.txt")
        _delete(client, "Documents/Parking Permit 2026.png")
        assert not _passes(task, initial, fresh())

    def test_deleting_and_emptying_the_trash_fails(self, client, fresh):
        """Delete means Recently Deleted, where the file can still be restored."""
        task, initial = _task("delete_packing_list"), fresh()
        _delete(client, "Documents/Packing List.txt")
        _purge(client, "Documents/Packing List.txt")
        assert not _passes(task, initial, fresh())

    def test_the_last_file_in_a_folder_leaves_the_folder(self, client, fresh):
        task = DeleteFileTask(goal="g", path="Statements/BUS COMPLETE CHK (...5555) — June 2026.pdf")
        initial = fresh()
        _delete(client, task.path)
        assert _passes(task, initial, fresh())

    def test_unknown_file_raises(self, client, fresh):
        with pytest.raises(ValueError, match="Nope.txt"):
            DeleteFileTask(goal="g", path="Documents/Nope.txt").get_target_state(fresh())


class TestRestoreFile:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("restore_w2_draft"), fresh()
        _restore(client, "Documents/W-2 2025 (draft).pdf")
        assert _passes(task, initial, fresh())

    def test_restoring_the_other_file_fails(self, client, fresh):
        task, initial = _task("restore_w2_draft"), fresh()
        _restore(client, "Documents/Lab Results 2026-08-12 (scan).pdf")
        assert not _passes(task, initial, fresh())

    def test_restoring_both_fails(self, client, fresh):
        task, initial = _task("restore_w2_draft"), fresh()
        _restore(client, "Documents/W-2 2025 (draft).pdf")
        _restore(client, "Documents/Lab Results 2026-08-12 (scan).pdf")
        assert not _passes(task, initial, fresh())

    def test_restore_onto_a_taken_name_gets_a_suffix(self, client, fresh):
        """The store restores beside a file now at the original path."""
        from open_apps import files_store

        files_store.save("Documents", "W-2 2025 (draft).pdf", b"a newer draft")
        task, initial = RestoreFileTask(goal="g", path="Documents/W-2 2025 (draft).pdf"), fresh()
        _restore(client, "Documents/W-2 2025 (draft).pdf")
        after = fresh()
        assert any(r["path"] == "Documents/W-2 2025 (draft) (2).pdf" for r in after["files"]["files"])
        assert _passes(task, initial, after)

    def test_a_file_that_is_not_in_the_trash_raises(self, client, fresh):
        with pytest.raises(ValueError, match="Recently Deleted"):
            RestoreFileTask(goal="g", path="Documents/Packing List.txt").get_target_state(fresh())


class TestRenameFile:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("rename_lease"), fresh()
        _rename(client, "Documents/Residential Lease - 44 Wharf Street.pdf", "Lease 2026.pdf")
        assert _passes(task, initial, fresh())

    @pytest.mark.parametrize("name", ["lease 2026.pdf", "Lease-2026.pdf", "Lease 2026", "Lease 2026 .pdf"])
    def test_a_near_miss_name_fails(self, client, fresh, name):
        """File names are addresses: the fuzzy compare used for titles
        (case and punctuation free) must not reach them."""
        task, initial = _task("rename_lease"), fresh()
        _rename(client, "Documents/Residential Lease - 44 Wharf Street.pdf", name)
        assert not _passes(task, initial, fresh())

    def test_renaming_the_wrong_file_fails(self, client, fresh):
        task, initial = _task("rename_lease"), fresh()
        _rename(client, "Documents/Insurance Card - Harborline Health.pdf", "Lease 2026.pdf")
        assert not _passes(task, initial, fresh())

    def test_renaming_onto_a_taken_name_raises(self, client, fresh):
        task = RenameFileTask(goal="g", path="Documents/Packing List.txt",
                              new_name="Parking Permit 2026.png")
        with pytest.raises(ValueError, match="taken"):
            task.get_target_state(fresh())


class TestMoveFile:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("move_insurance_card_to_downloads"), fresh()
        _move(client, "Documents/Insurance Card - Harborline Health.pdf", "Downloads")
        assert _passes(task, initial, fresh())

    def test_wrong_folder_fails(self, client, fresh):
        task, initial = _task("move_insurance_card_to_downloads"), fresh()
        _move(client, "Documents/Insurance Card - Harborline Health.pdf", "Statements")
        assert not _passes(task, initial, fresh())

    def test_a_missing_folder_raises(self, client, fresh):
        task = MoveFileTask(goal="g", path="Documents/Packing List.txt", folder="Nowhere")
        with pytest.raises(ValueError, match="Nowhere"):
            task.get_target_state(fresh())


class TestPermanentlyDelete:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("purge_duplicate_lab_scan"), fresh()
        _purge(client, "Documents/Lab Results 2026-08-12 (scan).pdf")
        assert _passes(task, initial, fresh())

    def test_purging_the_w2_draft_too_fails(self, client, fresh):
        task, initial = _task("purge_duplicate_lab_scan"), fresh()
        _purge(client, "Documents/Lab Results 2026-08-12 (scan).pdf")
        _purge(client, "Documents/W-2 2025 (draft).pdf")
        assert not _passes(task, initial, fresh())

    def test_restoring_instead_fails(self, client, fresh):
        task, initial = _task("purge_duplicate_lab_scan"), fresh()
        _restore(client, "Documents/Lab Results 2026-08-12 (scan).pdf")
        assert not _passes(task, initial, fresh())


class TestDownloadStatement:
    def test_correct_episode(self, client, fresh):
        task, initial = _task("download_july_savings_statement"), fresh()
        _download_statement(client, SAVINGS, "2026-07")
        after = fresh()
        assert any(r["path"] == JULY_SAVINGS for r in after["files"]["files"])
        assert _passes(task, initial, after)

    def test_wrong_month_fails(self, client, fresh):
        task, initial = _task("download_july_savings_statement"), fresh()
        _download_statement(client, SAVINGS, "2026-06")
        assert not _passes(task, initial, fresh())

    def test_wrong_account_fails(self, client, fresh):
        task, initial = _task("download_july_savings_statement"), fresh()
        _download_statement(client, 2, "2026-07")  # the card also has July
        assert not _passes(task, initial, fresh())

    def test_downloading_twice_keeps_one_copy_and_passes(self, client, fresh):
        task, initial = _task("download_july_savings_statement"), fresh()
        _download_statement(client, SAVINGS, "2026-07")
        _download_statement(client, SAVINGS, "2026-07")
        assert _passes(task, initial, fresh())

    def test_a_tampered_file_at_the_path_still_names_the_statement(self, client, fresh):
        """The target pins the statement's path, not its bytes: the bank's
        renderer is the one source of those, and it is deterministic."""
        task = _task("download_july_savings_statement")
        assert task.expected_path(fresh()) == JULY_SAVINGS

    def test_a_month_with_no_rows_raises(self, client, fresh):
        task = DownloadStatementTask(goal="g", account="BUS COMPLETE CHK (...5555)", month="2026-07")
        with pytest.raises(ValueError, match="no posted rows"):
            task.get_target_state(fresh())

    def test_account_by_last_four(self, client, fresh):
        task = DownloadStatementTask(goal="g", account="8891", month="2026-07")
        assert task.expected_path(fresh()) == JULY_SAVINGS


class TestComposite:
    def test_download_then_move(self, client, fresh):
        task, initial = _task("file_july_savings_statement_in_documents"), fresh()
        _download_statement(client, SAVINGS, "2026-07")
        _move(client, JULY_SAVINGS, "Documents")
        assert _passes(task, initial, fresh())

    def test_download_only_fails(self, client, fresh):
        task, initial = _task("file_july_savings_statement_in_documents"), fresh()
        _download_statement(client, SAVINGS, "2026-07")
        assert not _passes(task, initial, fresh())


def _chat(*replies):
    return [
        {"role": "assistant", "message": "Hi! How can I help you?"},
        {"role": "user", "message": "the goal"},
        *({"role": "assistant", "message": r} for r in replies),
    ]


class TestAnswer:
    @pytest.mark.parametrize(
        "reply",
        ["You paid $99.90 for it.", "99.90", "It was 99.9 dollars", "USD 99.90, on the card ending 2043"],
    )
    def test_right_answer_passes(self, client, fresh, reply):
        task, initial = _task("label_printer_price"), fresh()
        assert task.check_reply(task.reply(_chat(reply)), initial, fresh())

    @pytest.mark.parametrize("reply", ["$877.80", "about a hundred dollars", "$199.90", "9.90"])
    def test_wrong_answer_fails(self, client, fresh, reply):
        task, initial = _task("label_printer_price"), fresh()
        assert not task.check_reply(task.reply(_chat(reply)), initial, fresh())

    def test_no_reply_fails(self, client, fresh):
        task, initial = _task("label_printer_price"), fresh()
        assert task.reply(_chat()) is None
        assert not task.check_if_task_is_complete(initial, fresh())

    def test_only_the_latest_reply_after_the_goal_counts(self, client, fresh):
        task = _task("label_printer_price")
        assert task.reply(_chat("Let me check...", "It was $99.90.")) == "It was $99.90."
        greeting_only = [{"role": "assistant", "message": "$99.90"}, {"role": "user", "message": "goal"}]
        assert task.reply(greeting_only) is None

    def test_a_right_answer_with_a_state_change_fails(self, client, fresh):
        task, initial = _task("label_printer_price"), fresh()
        _delete(client, "Documents/Packing List.txt")
        assert not task.check_reply("$99.90", initial, fresh())

    def test_the_answer_is_in_the_seed(self, client, fresh):
        """The price lives on the seeded order and its receipt."""
        shop = fresh()["online_shop"]
        order = next(o for o in shop["orders"] if o["order_id"] == "a41f07c2")
        assert order["total"] == pytest.approx(99.9)
        assert client.get(f"/files/raw/{quote('Receipts/OpenShop order a41f07c2.pdf')}").status_code == 200

    def test_browsergym_validates_answers_from_the_chat(self, client, fresh):
        from open_apps.tasks.add_tasks_to_browsergym import OpenAppsTask

        bg = OpenAppsTask.__new__(OpenAppsTask)
        bg.task = _task("label_printer_price")
        bg.initial_state = fresh()
        bg.url = "http://testserver"
        bg.page = type("P", (), {"url": "http://testserver/files"})()
        reward, done, _, info = bg.validate(None, _chat("It cost $99.90."))
        assert (reward, done) == (1.0, True)
        reward, done, _, _ = bg.validate(None, _chat())
        assert (reward, done) == (0.0, False)
        reward, done, _, _ = bg.validate(None, _chat("$877.80"))
        assert (reward, done) == (0.0, True)


class TestMirrors:
    """Values the tasks package inlines to stay light, pinned to their owners."""

    def test_receipt_path_matches_the_shop(self):
        from open_apps.apps.onlineshop_app.main import receipt_path
        from open_apps.tasks.tasks import RECEIPT_PATH

        assert RECEIPT_PATH.format(order_id="a41f07c2") == receipt_path("a41f07c2")

    def test_files_today_matches_the_config(self, apps_cfg):
        from open_apps.tasks.tasks import FILES_TODAY

        assert str(apps_cfg.files.today) == FILES_TODAY


class TestFilesInComparison:
    def _state(self, rows):
        return {"todo": [], "calendar": [], "map": [], "messenger": [], "files": {"files": rows}}

    def _row(self, path, sha="a" * 64, deleted_on=None):
        return {"path": path, "kind": "text/plain", "size": 1, "sha256": sha,
                "deleted": deleted_on is not None, "deleted_on": deleted_on}

    def _same(self, a, b, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return AppStateComparison(self._state(a), self._state(b), **kw).compare()

    def test_row_order_does_not_matter(self):
        rows = [self._row("Documents/a.txt"), self._row("Documents/b.txt")]
        assert self._same(rows, rows[::-1])

    def test_paths_compare_exactly(self):
        assert not self._same([self._row("Documents/A.txt")], [self._row("Documents/a.txt")])
        assert not self._same([self._row("Documents/a b.txt")], [self._row("Documents/a-b.txt")])

    def test_content_compares_exactly(self):
        assert not self._same([self._row("Documents/a.txt")], [self._row("Documents/a.txt", "b" * 64)])

    def test_a_state_without_files_compares_as_before(self):
        state = {"todo": [], "calendar": [], "map": [], "messenger": []}
        assert AppStateComparison(state, dict(state)).compare()

    def test_receipts_follow_their_order_under_purchase_normalisation(self):
        target = self._state([{"path": f"Receipts/OpenShop order {ORDER_ID_TOKEN}.pdf", "kind": "application/pdf",
                               "size": None, "sha256": None, "deleted": False, "deleted_on": None}])
        target["online_shop"] = {"cart": [], "orders": [{"order_id": ORDER_ID_TOKEN, "total": 1.0, "items": []}]}
        observed = self._state([{"path": "Receipts/OpenShop order 1a2b3c4d.pdf", "kind": "application/pdf",
                                 "size": 3070, "sha256": "c" * 64, "deleted": False, "deleted_on": None}])
        observed["online_shop"] = {"cart": [], "orders": [{"order_id": "1a2b3c4d", "total": 1.0, "items": []}]}
        with contextlib.redirect_stdout(io.StringIO()):
            assert AppStateComparison(target, observed, normalize_purchases=True).compare()
            # A receipt for an order that doesn't exist is not normalised away.
            observed["files"]["files"][0]["path"] = "Receipts/OpenShop order deadbeef.pdf"
            assert not AppStateComparison(target, observed, normalize_purchases=True).compare()


class TestPurchasesWriteReceipts:
    def _buy(self, client, sku):
        """Check out ``sku`` alone. The default cart already holds it next to
        a second line, so that line is deselected for the checkout and
        selected again after -- leaving the cart as the target expects."""
        from open_apps.apps.onlineshop_app import main as shop

        others = [r.id for r in shop.cart_items() if r.sku != sku and r.selected]
        for item_id in others:
            client.post(f"/onlineshop/cart/toggle/{item_id}", follow_redirects=False)
        form = {"name": "Dana Reyes", "address": "44 Wharf Street Portland ME 04101",
                "card_number": "9024007155992043", "card_expiry": "09/29", "card_cvv": "418",
                "card_name": "Cardinal Freight LLC"}
        response = client.post("/onlineshop/checkout", data=form, follow_redirects=False)
        for item_id in others:
            client.post(f"/onlineshop/cart/toggle/{item_id}", follow_redirects=False)
        return response

    def _shipped(self, key):
        return instantiate(OmegaConf.load(config_dir() / "tasks" / "openbanking.yaml")[key])

    def test_a_checkout_passes_with_its_receipt(self, client, fresh):
        task, initial = self._shipped("buy_the_console_table_with_the_business_card"), fresh()
        assert self._buy(client, task.sku).headers["location"] == "/onlineshop/orders"
        after = fresh()
        assert len([r for r in after["files"]["files"] if r["path"].startswith("Receipts/")]) == 3
        assert _passes(task, initial, after)

    def test_a_checkout_whose_receipt_was_deleted_fails(self, client, fresh):
        from open_apps import files_store

        task, initial = self._shipped("buy_the_console_table_with_the_business_card"), fresh()
        self._buy(client, task.sku)
        new = next(r["path"] for r in fresh()["files"]["files"]
                   if r["path"].startswith("Receipts/") and "a41f07c2" not in r["path"] and "7d93e5b0" not in r["path"])
        files_store.delete(new)
        assert not _passes(task, initial, fresh())

    def test_the_target_expects_one_new_receipt(self, client, fresh):
        task = self._shipped("buy_the_console_table_with_the_business_card")
        initial = fresh()
        target = task.get_target_state(initial)
        before = {r["path"] for r in initial["files"]["files"]}
        added = [r for r in target["files"]["files"] if r["path"] not in before]
        assert [r["path"] for r in added] == [f"Receipts/OpenShop order {ORDER_ID_TOKEN}.pdf"]

    def test_a_declined_checkout_expects_no_receipt(self, client, fresh):
        task = self._shipped("attempt_the_copier_far_over_the_card_limit")
        initial = fresh()
        assert task.get_target_state(initial)["files"] == initial["files"]


def _shipped_tasks():
    out = []
    for path in sorted((config_dir() / "tasks").glob("*.yaml")):
        if path.stem == "files":
            continue
        cfg = OmegaConf.load(path)
        for key in cfg:
            out.append(pytest.param(path.stem, key, id=f"{path.stem}:{key}"))
    return out


@pytest.fixture(scope="module")
def seeded_state(client, apps_cfg):
    """One capture of the default seed, shared by the guard below (a reset
    per shipped task would cost minutes)."""
    from open_apps import state as state_module

    reset_all_apps(apps_cfg)

    def fetch(url, *args, **kwargs):
        response = client.get(url.removeprefix("http://testserver"))
        return response.json() if response.status_code == 200 else None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(state_module, "safe_get_json", fetch)
        return state_module.get_current_state("http://testserver")


@pytest.mark.parametrize("group,key", _shipped_tasks())
def test_every_shipped_task_leaves_files_alone(seeded_state, group, key):
    """Files is in every live state now. A task that isn't about files must
    build a target with the files slice untouched -- beyond the one receipt a
    checkout writes -- and that target must pass its own comparison."""
    task = instantiate(OmegaConf.load(config_dir() / "tasks" / f"{group}.yaml")[key])
    if not hasattr(task, "get_target_state"):
        pytest.skip("scored without a state target")
    initial = seeded_state
    target = task.get_target_state(initial)
    before = {r["path"] for r in initial["files"]["files"]}
    after = {r["path"] for r in target["files"]["files"]}
    assert after - before <= {f"Receipts/OpenShop order {ORDER_ID_TOKEN}.pdf"}
    assert before <= after
    if after == before:
        assert target["files"] == initial["files"]
    with contextlib.redirect_stdout(io.StringIO()):
        assert AppStateComparison(target, target, normalize_purchases=after != before).compare()
