"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The shared file store: the folder tree the Files app shows and the bank, shop,
messenger and code editor write into.

What matters most is that every operation is reversible or explicit (delete
goes to Recently Deleted, collisions never overwrite), that no path escapes the
root, and that ``snapshot()`` -- the ``/files_all`` scoring surface -- is
byte-stable and leaves the code editor's files to ``/codeeditor_all``.
"""

import hashlib
import json
import os
from datetime import date

import pytest

from open_apps import files_store as fs

TODAY = "2026-08-26"


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "files"
    code = tmp_path / "codeeditor"
    code.mkdir()
    (code / "main.py").write_text("print('hi')\n")
    fs.configure(root, code, TODAY)
    fs.reset()
    return root


def paths(snapshot: dict) -> list[str]:
    return [f["path"] for f in snapshot["files"]]


class TestSaveAndRead:
    def test_save_returns_the_store_path_and_round_trips(self, store):
        path = fs.save("Documents", "note.txt", b"hello")
        assert path == "Documents/note.txt"
        assert fs.read(path) == b"hello"

    def test_save_accepts_text(self, store):
        fs.save("Documents", "note.txt", "héllo")
        assert fs.read("Documents/note.txt") == "héllo".encode()

    def test_reset_creates_the_default_folders(self, store):
        names = [e.name for e in fs.list_folder("")]
        assert names == ["Code", "Documents", "Downloads", "Receipts", "Statements"]

    def test_reset_clears_previous_files_but_never_the_code_workspace(self, store, tmp_path):
        fs.save("Documents", "a.txt", b"a")
        fs.delete("Documents/a.txt")
        fs.reset()
        assert fs.list_folder("Documents") == []
        assert fs.list_trash() == []
        assert (tmp_path / "codeeditor" / "main.py").exists()

    def test_save_into_a_missing_folder_creates_it(self, store):
        assert fs.save("Documents/Medical", "r.pdf", b"%PDF") == "Documents/Medical/r.pdf"

    @pytest.mark.parametrize("name", ["", ".hidden", "a/b", "..", "a\\b"])
    def test_bad_names_are_refused(self, store, name):
        with pytest.raises(ValueError):
            fs.save("Documents", name, b"x")

    def test_read_missing_raises(self, store):
        with pytest.raises(FileNotFoundError):
            fs.read("Documents/nope.txt")


class TestCollisions:
    def test_saving_an_existing_name_appends_a_counter(self, store):
        assert fs.save("Documents", "scan.pdf", b"1") == "Documents/scan.pdf"
        assert fs.save("Documents", "scan.pdf", b"2") == "Documents/scan (2).pdf"
        assert fs.save("Documents", "scan.pdf", b"3") == "Documents/scan (3).pdf"
        assert fs.read("Documents/scan.pdf") == b"1"

    def test_names_without_an_extension(self, store):
        fs.save("Documents", "README", b"1")
        assert fs.save("Documents", "README", b"2") == "Documents/README (2)"

    def test_duplicate_uses_the_same_rule(self, store):
        fs.save("Documents", "lease.pdf", b"L")
        assert fs.duplicate("Documents/lease.pdf") == "Documents/lease (2).pdf"
        assert fs.read("Documents/lease (2).pdf") == b"L"

    def test_move_into_a_folder_with_the_same_name(self, store):
        fs.save("Documents", "a.txt", b"doc")
        fs.save("Downloads", "a.txt", b"dl")
        assert fs.move("Downloads/a.txt", "Documents") == "Documents/a (2).txt"
        assert fs.read("Documents/a.txt") == b"doc"


class TestRenameMove:
    def test_rename(self, store):
        fs.save("Documents", "a.txt", b"a")
        assert fs.rename("Documents/a.txt", "b.txt") == "Documents/b.txt"
        assert not fs.exists("Documents/a.txt")
        assert fs.read("Documents/b.txt") == b"a"

    def test_rename_onto_an_existing_name_is_refused(self, store):
        fs.save("Documents", "a.txt", b"a")
        fs.save("Documents", "b.txt", b"b")
        with pytest.raises(FileExistsError):
            fs.rename("Documents/a.txt", "b.txt")
        assert fs.read("Documents/b.txt") == b"b"

    def test_rename_can_change_only_the_case(self, store):
        fs.save("Documents", "a.txt", b"a")
        assert fs.rename("Documents/a.txt", "A.txt") == "Documents/A.txt"
        assert [e.name for e in fs.list_folder("Documents")] == ["A.txt"]

    def test_rename_to_the_same_name_is_a_no_op(self, store):
        fs.save("Documents", "a.txt", b"a")
        assert fs.rename("Documents/a.txt", "a.txt") == "Documents/a.txt"

    def test_move(self, store):
        fs.save("Downloads", "permit.png", b"P")
        assert fs.move("Downloads/permit.png", "Documents") == "Documents/permit.png"
        assert fs.list_folder("Downloads") == []

    def test_move_to_a_missing_folder_is_refused(self, store):
        fs.save("Downloads", "x.txt", b"x")
        with pytest.raises(FileNotFoundError):
            fs.move("Downloads/x.txt", "Nowhere")

    def test_a_folder_cannot_move_into_itself(self, store):
        fs.make_folder("Documents", "Tax")
        fs.make_folder("Documents/Tax", "2025")
        with pytest.raises(ValueError):
            fs.move("Documents/Tax", "Documents/Tax/2025")

    def test_make_folder_collides_like_files(self, store):
        assert fs.make_folder("Documents", "Tax") == "Documents/Tax"
        assert fs.make_folder("Documents", "Tax") == "Documents/Tax (2)"


class TestRecentlyDeleted:
    def test_delete_moves_the_file_to_trash_with_its_origin_and_date(self, store):
        fs.save("Documents", "w2.pdf", b"W")
        trash_name = fs.delete("Documents/w2.pdf")
        assert not fs.exists("Documents/w2.pdf")
        [entry] = fs.list_trash()
        assert entry.trash_name == trash_name
        assert entry.path == "Documents/w2.pdf"
        assert entry.deleted and entry.deleted_on == TODAY

    def test_the_trash_is_hidden_from_listings_and_paths(self, store):
        fs.save("Documents", "a.txt", b"a")
        fs.delete("Documents/a.txt")
        assert ".trash" not in [e.name for e in fs.list_folder("")]
        with pytest.raises(ValueError):
            fs.resolve(".trash/a.txt")

    def test_restore_puts_it_back(self, store):
        fs.save("Documents", "a.txt", b"a")
        name = fs.delete("Documents/a.txt")
        assert fs.restore(name) == "Documents/a.txt"
        assert fs.read("Documents/a.txt") == b"a"
        assert fs.list_trash() == []

    def test_restore_onto_a_taken_path_appends_a_counter(self, store):
        fs.save("Documents", "a.txt", b"old")
        name = fs.delete("Documents/a.txt")
        fs.save("Documents", "a.txt", b"new")
        assert fs.restore(name) == "Documents/a (2).txt"
        assert fs.read("Documents/a.txt") == b"new"

    def test_restore_recreates_a_missing_parent_folder(self, store):
        fs.make_folder("Documents", "Tax")
        fs.save("Documents/Tax", "w2.pdf", b"W")
        name = fs.delete("Documents/Tax/w2.pdf")
        fs.delete("Documents/Tax")
        assert fs.restore(name) == "Documents/Tax/w2.pdf"

    def test_two_deletes_of_the_same_name_coexist(self, store):
        fs.save("Documents", "a.txt", b"1")
        first = fs.delete("Documents/a.txt")
        fs.save("Documents", "a.txt", b"2")
        second = fs.delete("Documents/a.txt")
        assert first != second
        assert sorted(fs.read_trash(n) for n in (first, second)) == [b"1", b"2"]

    def test_purge_removes_one_entry_for_good(self, store):
        fs.save("Documents", "a.txt", b"a")
        fs.save("Documents", "b.txt", b"b")
        a = fs.delete("Documents/a.txt")
        fs.delete("Documents/b.txt")
        fs.purge(a)
        assert [e.path for e in fs.list_trash()] == ["Documents/b.txt"]
        with pytest.raises(FileNotFoundError):
            fs.restore(a)

    def test_empty_trash(self, store):
        for n in ("a.txt", "b.txt"):
            fs.save("Documents", n, b"x")
            fs.delete(f"Documents/{n}")
        assert fs.empty_trash() == 2
        assert fs.list_trash() == []

    def test_deleting_a_folder_takes_its_contents(self, store):
        fs.make_folder("Documents", "Old")
        fs.save("Documents/Old", "a.txt", b"a")
        name = fs.delete("Documents/Old")
        assert not fs.exists("Documents/Old")
        assert fs.restore(name) == "Documents/Old"
        assert fs.read("Documents/Old/a.txt") == b"a"

    def test_seeding_can_backdate_a_deletion(self, store):
        fs.save("Documents", "w2-draft.pdf", b"W")
        fs.delete("Documents/w2-draft.pdf", deleted_on="2026-07-29")
        [entry] = fs.list_trash()
        assert entry.deleted_on == "2026-07-29"


class TestRetention:
    @pytest.mark.parametrize(
        "deleted_on,left",
        [(TODAY, 30), ("2026-07-29", 2), ("2026-08-25", 29), ("2026-07-27", 0), ("2026-01-01", 0)],
    )
    def test_days_left_counts_to_the_pinned_today(self, store, deleted_on, left):
        fs.save("Documents", "a.txt", b"a")
        fs.delete("Documents/a.txt", deleted_on=deleted_on)
        [entry] = fs.list_trash()
        assert fs.days_left(entry) == left

    def test_nothing_is_purged_on_a_clock(self, store):
        """An expired entry stays until someone removes it: purging by time
        would make an episode's state depend on how long it ran."""
        fs.save("Documents", "a.txt", b"a")
        fs.delete("Documents/a.txt", deleted_on="2025-01-01")
        fs.configure(store, None, TODAY)
        assert len(fs.list_trash()) == 1

    def test_today_accepts_a_date(self, tmp_path):
        fs.configure(tmp_path / "f", None, date(2026, 8, 26))
        fs.reset()
        fs.save("Documents", "a.txt", b"a")
        fs.delete("Documents/a.txt")
        assert fs.list_trash()[0].deleted_on == TODAY


class TestPathSafety:
    @pytest.mark.parametrize(
        "bad", ["../x", "Documents/../../x", "/etc/passwd", "Documents/./../..", "/tmp"]
    )
    def test_escapes_are_refused(self, store, bad):
        with pytest.raises(ValueError):
            fs.resolve(bad)

    def test_a_symlink_out_of_the_root_is_refused(self, store, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("s")
        os.symlink(outside, store / "Documents" / "link")
        with pytest.raises(ValueError):
            fs.read("Documents/link/secret.txt")

    def test_a_planted_symlink_never_reaches_listings_or_the_snapshot(self, store, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("s")
        os.symlink(outside, store / "Documents" / "link")
        assert fs.list_folder("Documents") == []
        assert not any("secret" in p for p in paths(fs.snapshot()))

    def test_resolve_stays_inside(self, store):
        assert fs.resolve("Documents/a.txt") == (store / "Documents" / "a.txt").resolve()

    def test_root_resolves(self, store):
        assert fs.resolve("") == store.resolve()


class TestCodeFolder:
    def test_code_is_the_editor_workspace_not_a_copy(self, store, tmp_path):
        assert fs.read("Code/main.py") == b"print('hi')\n"
        fs.save("Code", "new.py", b"x = 1\n")
        assert (tmp_path / "codeeditor" / "new.py").read_bytes() == b"x = 1\n"

    def test_code_is_left_out_of_the_snapshot(self, store):
        before = json.dumps(fs.snapshot())
        fs.save("Code", "new.py", b"x = 1\n")
        name = fs.delete("Code/main.py")
        assert json.dumps(fs.snapshot()) == before
        assert not any(p.startswith("Code") for p in paths(fs.snapshot()))
        assert fs.restore(name) == "Code/main.py"

    def test_without_a_code_dir_there_is_no_code_folder(self, tmp_path):
        fs.configure(tmp_path / "f", None, TODAY)
        fs.reset()
        assert "Code" not in [e.name for e in fs.list_folder("")]
        with pytest.raises(FileNotFoundError):
            fs.read("Code/main.py")

    def test_code_folder_itself_cannot_be_moved_renamed_or_deleted(self, store):
        for op in (lambda: fs.delete("Code"), lambda: fs.rename("Code", "X"), lambda: fs.move("Code", "Documents")):
            with pytest.raises(ValueError):
                op()


class TestListing:
    def test_folders_first_then_names_case_insensitively(self, store):
        fs.save("Documents", "b.txt", b"b")
        fs.save("Documents", "A.txt", b"a")
        fs.make_folder("Documents", "zeta")
        assert [e.name for e in fs.list_folder("Documents")] == ["zeta", "A.txt", "b.txt"]

    def test_entries_carry_kind_size_and_checksum(self, store):
        fs.save("Documents", "r.pdf", b"%PDF-1.4")
        [entry] = fs.list_folder("Documents")
        assert entry.kind == "application/pdf"
        assert entry.size == 8
        assert entry.sha256 == hashlib.sha256(b"%PDF-1.4").hexdigest()
        assert not entry.deleted and entry.deleted_on is None

    @pytest.mark.parametrize(
        "name,kind",
        [
            ("a.pdf", "application/pdf"),
            ("a.PNG", "image/png"),
            ("a.jpg", "image/jpeg"),
            ("a.txt", "text/plain"),
            ("a.py", "text/x-python"),
            ("a.yaml", "text/yaml"),
            ("a.bin", "application/octet-stream"),
            ("noext", "application/octet-stream"),
        ],
    )
    def test_kind_comes_from_a_fixed_table(self, store, name, kind):
        """Not ``mimetypes``: it reads the host's mime.types, so the scoring
        surface would differ between a laptop and the cluster."""
        assert fs.kind_of(name) == kind

    def test_listing_a_file_is_refused(self, store):
        fs.save("Documents", "a.txt", b"a")
        with pytest.raises(NotADirectoryError):
            fs.list_folder("Documents/a.txt")


class TestSnapshot:
    def test_shape(self, store):
        fs.save("Documents", "a.txt", b"a")
        snap = fs.snapshot()
        assert set(snap) == {"files"}
        assert snap["files"] == [
            {
                "path": "Documents/a.txt",
                "kind": "text/plain",
                "size": 1,
                "sha256": hashlib.sha256(b"a").hexdigest(),
                "deleted": False,
                "deleted_on": None,
            }
        ] + [
            {"path": p, "kind": "folder", "size": 0, "sha256": None, "deleted": False, "deleted_on": None}
            for p in ("Downloads", "Receipts", "Statements")
        ]

    def test_folders_only_appear_when_empty(self, store):
        fs.make_folder("Documents", "Tax")
        assert "Documents/Tax" in paths(fs.snapshot())
        fs.save("Documents/Tax", "w2.pdf", b"W")
        assert "Documents/Tax" not in paths(fs.snapshot())
        assert "Documents" not in paths(fs.snapshot())

    def test_deleted_entries_carry_their_original_path_and_date(self, store):
        fs.save("Documents", "a.txt", b"a")
        fs.delete("Documents/a.txt", deleted_on="2026-08-01")
        rows = [f for f in fs.snapshot()["files"] if f["deleted"]]
        assert rows == [
            {
                "path": "Documents/a.txt",
                "kind": "text/plain",
                "size": 1,
                "sha256": hashlib.sha256(b"a").hexdigest(),
                "deleted": True,
                "deleted_on": "2026-08-01",
            }
        ]

    def test_a_deleted_folder_lists_its_files(self, store):
        fs.make_folder("Documents", "Old")
        fs.save("Documents/Old", "a.txt", b"a")
        fs.save("Documents/Old", "b.txt", b"b")
        fs.delete("Documents/Old")
        deleted = [f["path"] for f in fs.snapshot()["files"] if f["deleted"]]
        assert deleted == ["Documents/Old/a.txt", "Documents/Old/b.txt"]

    def test_sorted_and_byte_stable(self, store):
        for n in ("b.txt", "a.txt", "C.txt"):
            fs.save("Documents", n, n.encode())
        fs.delete("Documents/b.txt")
        first = json.dumps(fs.snapshot())
        assert json.dumps(fs.snapshot()) == first
        files = fs.snapshot()["files"]
        keys = [(f["path"], f["deleted"]) for f in files]
        assert keys == sorted(keys)

    def test_identical_actions_give_identical_snapshots_across_stores(self, tmp_path):
        """Trash names and timestamps on disk must not leak into the payload,
        or two runs of the same episode would diff."""

        def run(root):
            fs.configure(root, None, TODAY)
            fs.reset()
            fs.save("Documents", "a.txt", b"a")
            fs.save("Downloads", "a.txt", b"b")
            fs.delete("Downloads/a.txt")
            fs.delete("Documents/a.txt")
            return json.dumps(fs.snapshot())

        assert run(tmp_path / "one") == run(tmp_path / "two")

    def test_unconfigured_store_raises(self, monkeypatch):
        monkeypatch.setattr(fs, "_root", None)
        with pytest.raises(RuntimeError):
            fs.snapshot()
