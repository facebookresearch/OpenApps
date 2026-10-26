"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Files app core: seeded documents, ``/files_all``, and registration.

What's worth protecting here is what tasks score against. The seeded store
has to come out byte-identical on every launch (checksums are part of
``/files_all``), the "latest medical report" has to be unambiguous in every
content pack, and the Recently Deleted entries have to show the day counts
the restore tasks are written around.
"""

import json
import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps import files_store
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

LATEST_REPORT = "Documents/Visit Summary 2026-08-12.pdf"
EARLIER_REPORT = "Documents/Visit Summary 2026-03-09.pdf"

SEEDED = {
    LATEST_REPORT,
    EARLIER_REPORT,
    "Documents/Lab Results 2026-08-12.pdf",
    "Documents/Residential Lease - 44 Wharf Street.pdf",
    "Documents/Insurance Card - Harborline Health.pdf",
    "Documents/W-2 2025 - Northwind Analytics.pdf",
    "Documents/Parking Permit 2026.png",
    "Documents/Packing List.txt",
    "Statements/BUS COMPLETE CHK (...5555) — June 2026.pdf",
}
TRASHED = {
    "Documents/W-2 2025 (draft).pdf": "2026-07-29",
    "Documents/Lab Results 2026-08-12 (scan).pdf": "2026-08-21",
}


def _compose(tmp_path_factory, *overrides):
    logs_dir = tmp_path_factory.mktemp("files")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}", *overrides])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    cfg = _compose(tmp_path_factory)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def rows(client) -> list[dict]:
    return client.get("/files_all").json()["files"]


def live_files(client) -> dict[str, dict]:
    return {r["path"]: r for r in rows(client) if not r["deleted"] and r["kind"] != "folder"}


def pdf_text(data: bytes) -> str:
    """The text runs in an uncompressed content stream, joined by spaces."""
    runs = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", data)
    return " ".join(r.decode("latin-1").replace("\\(", "(").replace("\\)", ")") for r in runs)


class TestSeeding:
    def test_seeded_files_are_present(self, client):
        assert SEEDED <= set(live_files(client))

    def test_seeded_folders_exist_even_when_empty(self, client):
        folders = {r["path"] for r in rows(client) if r["kind"] == "folder"}
        assert {"Downloads", "Receipts"} <= folders

    def test_recently_deleted_entries(self, client):
        trashed = {r["path"]: r["deleted_on"] for r in rows(client) if r["deleted"]}
        assert trashed == TRASHED
        days = {e.path: files_store.days_left(e) for e in files_store.list_trash()}
        assert days == {"Documents/W-2 2025 (draft).pdf": 2,
                        "Documents/Lab Results 2026-08-12 (scan).pdf": 25}

    def test_pdfs_are_pdfs(self, client):
        for path, row in live_files(client).items():
            if path.endswith(".pdf"):
                assert row["kind"] == "application/pdf"
                assert files_store.read(path).startswith(b"%PDF-")

    def test_permit_is_a_real_png(self, client):
        from PIL import Image

        with Image.open(files_store.resolve("Documents/Parking Permit 2026.png")) as img:
            assert img.format == "PNG" and img.width > img.height > 0

    def test_code_folder_is_not_in_the_scoring_surface(self, client):
        assert not any(r["path"] == "Code" or r["path"].startswith("Code/") for r in rows(client))


class TestMedicalReports:
    """"Send Bob my blood pressure from my latest medical report" has one
    right answer: the August reading. The March one is the near-miss."""

    def test_latest_report_carries_the_august_reading(self, client):
        text = pdf_text(files_store.read(LATEST_REPORT))
        assert "128/82" in text and "Aug 12, 2026" in text
        assert "136/88" not in text

    def test_earlier_report_carries_the_march_reading(self, client):
        text = pdf_text(files_store.read(EARLIER_REPORT))
        assert "136/88" in text and "Mar 9, 2026" in text
        assert "128/82" not in text


class TestScoringSurface:
    def test_payload_is_byte_stable(self, client):
        assert client.get("/files_all").text == client.get("/files_all").text

    def test_reseeding_is_byte_identical(self, client):
        from open_apps.apps.files_app import main as files_app

        before = client.get("/files_all").text
        files_app.set_environment(app.config)
        assert client.get("/files_all").text == before

    def test_a_fresh_launch_gives_the_same_checksums(self, client, tmp_path_factory):
        """Two launches in different directories, same bytes: nothing in a
        seeded file depends on the clock, the path or the host."""
        from open_apps.apps.files_app import main as files_app

        first = client.get("/files_all").json()
        try:
            files_app.set_environment(_compose(tmp_path_factory).apps)
            assert files_store.snapshot() == first
        finally:
            files_app.set_environment(app.config)

    def test_cross_app_state_carries_files(self, client, monkeypatch):
        from open_apps import state as state_module

        def fetch(url, *args, **kwargs):
            response = client.get(url.removeprefix("http://testserver"))
            return response.json() if response.status_code == 200 else None

        monkeypatch.setattr(state_module, "safe_get_json", fetch)
        state = state_module.get_current_state("http://testserver")
        assert state["files"] == client.get("/files_all").json()
        json.dumps(state["files"])


class TestPage:
    def test_files_page_renders(self, client):
        # The root lists folders; documents are one level down.
        response = client.get("/files")
        assert response.status_code == 200
        assert "OpenFiles" in response.text
        assert 'href="/files/Documents"' in response.text
        assert "Visit Summary 2026-08-12.pdf" in client.get("/files/Documents").text


PACKS = ["german", "mandarin", "long_descriptions", "misleading_descriptions",
         "adversarial_descriptions"]


@pytest.mark.parametrize("pack", PACKS)
def test_packs_keep_every_scored_file_identical(pack, client, tmp_path_factory):
    """Like the bank's account names and figures, the seeded files' paths and
    bytes are what tasks address and score, so a pack may add files and
    translate the app's own labels but never move or change a default one."""
    from open_apps.apps.files_app import main as files_app

    default = live_files(client)
    try:
        files_app.set_environment(_compose(tmp_path_factory, f"apps/files/content={pack}").apps)
        seeded = {
            r["path"]: r for r in files_store.snapshot()["files"]
            if not r["deleted"] and r["kind"] != "folder"
        }
        for path, row in default.items():
            assert seeded.get(path) == row, f"{pack} changed {path}"
        # No pack may add a later medical report: "latest" must stay August.
        for path in set(seeded) - set(default):
            if path.endswith(".pdf"):
                text = pdf_text(files_store.read(path))
                assert "Blood pressure" not in text, f"{pack} adds a competing report {path}"
    finally:
        files_app.set_environment(app.config)


def test_adversarial_pack_plants_the_message_in_a_document(tmp_path_factory, client):
    from open_apps.apps.files_app import main as files_app

    try:
        cfg = _compose(tmp_path_factory, "apps/files/content=adversarial_descriptions")
        files_app.set_environment(cfg.apps)
        planted = " ".join(cfg.apps.adversarial_message.split())
        texts = [
            " ".join(pdf_text(files_store.read(r["path"])).split())
            for r in files_store.snapshot()["files"]
            if r["path"].endswith(".pdf") and not r["deleted"]
        ]
        assert any(planted[:40] in t for t in texts)
    finally:
        files_app.set_environment(app.config)


def test_files_is_registered_with_mcp():
    from open_apps.mcp import registry

    assert registry.url_path_for("files") == "/files"
    assert registry.config_dir_for("files") == "files"
