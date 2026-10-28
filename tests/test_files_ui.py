"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The Files app's pages: browser, viewers, Recently Deleted, and the actions.

What's protected here is what an agent depends on:
- every action is a real link or form post, so the window chrome survives;
- the PDF viewer is served from the vendored pdf.js, never a CDN;
- the raw route hands PDFs to a browser inline;
- destructive actions only happen after an explicit confirm;
- both layouts expose the same landmarks, and a phone gets one pane.
"""

import re
from pathlib import Path
from urllib.parse import quote

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps import files_store
from open_apps.apps.files_app import main as files_app
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

REPORT = "Documents/Visit Summary 2026-08-12.pdf"
LEASE = "Documents/Residential Lease - 44 Wharf Street.pdf"
PERMIT = "Documents/Parking Permit 2026.png"
PACKING = "Documents/Packing List.txt"
DRAFT = "Documents/W-2 2025 (draft).pdf"

LANDMARKS = ("files-sidebar", "files-toolbar", "files-content", "files-preview", "files-dialog")


def url(prefix: str, path: str) -> str:
    return f"{prefix}/{quote(path)}"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs_dir = tmp_path_factory.mktemp("files_ui")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}"])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


@pytest.fixture
def fresh(client):
    """Re-seed the store around a test that changes it."""
    files_app.set_environment(app.config)
    yield client
    files_app.set_environment(app.config)


def paths() -> set[str]:
    return {r["path"] for r in files_store.snapshot()["files"] if not r["deleted"]}


def trashed() -> set[str]:
    return {r["path"] for r in files_store.snapshot()["files"] if r["deleted"]}


def trash_name(path: str) -> str:
    return next(e.trash_name for e in files_store.list_trash() if e.path == path)


def ids(html: str) -> set[str]:
    return set(re.findall(r'\sid="([^"]+)"', html))


class TestBrowser:
    def test_root_lists_the_folders(self, client):
        html = client.get("/files").text
        for folder in ("Documents", "Downloads", "Receipts", "Statements", "Code"):
            assert f'href="/files/{folder}"' in html
        assert 'href="/files/recently-deleted"' in html

    def test_folder_rows_are_real_links(self, client):
        html = client.get("/files/Documents").text
        assert LANDMARKS[0] in ids(html) and "files-content" in ids(html)
        for path in (REPORT, LEASE, PERMIT, PACKING):
            name = path.split("/")[-1]
            assert f'href="/files/Documents?selected={quote(name)}"' in html
            assert name in html

    def test_breadcrumb_names_the_folder(self, client):
        html = client.get("/files/Statements").text
        crumb = re.search(r'<nav[^>]*id="files-breadcrumb".*?</nav>', html, re.S).group(0)
        assert 'href="/files"' in crumb and "Statements" in crumb

    def test_selection_fills_the_preview(self, client):
        html = client.get(f"/files/Documents?selected={quote('Visit Summary 2026-08-12.pdf')}").text
        preview = re.search(r'<aside[^>]*id="files-preview".*?</aside>', html, re.S).group(0)
        assert "Visit Summary 2026-08-12.pdf" in preview
        assert "PDF document" in preview
        assert re.search(r"\d+ pages?", preview)
        assert f'href="{url("/files/view", REPORT)}"' in preview

    def test_unknown_folder_is_a_404(self, client):
        assert client.get("/files/Nowhere").status_code == 404

    def test_sort_by_size_orders_rows(self, client):
        html = client.get("/files/Documents?sort=size").text
        sizes = [int(s) for s in re.findall(r'data-size="(\d+)"', html)]
        assert sizes and sizes == sorted(sizes, reverse=True)

    def test_search_finds_files_in_any_folder(self, client):
        html = client.get("/files/search?q=lease").text
        assert "Residential Lease - 44 Wharf Street.pdf" in html
        assert "Visit Summary" not in html

    def test_search_results_open_the_file(self, client):
        """The search window has no preview pane to select into, and its files
        live in other folders: a select link pointed at a name the root doesn't
        have, so a single click went nowhere."""
        html = client.get("/files/search?q=lease").text
        link = re.search(r'<a href="([^"]*)" class="fs-name"', html).group(1)
        assert link == url("/files/view", LEASE)

    def test_grid_view_toggle(self, client):
        html = client.get("/files/Documents?view=grid").text
        assert 'class="fs-grid"' in html
        assert 'aria-pressed="true"' in html


class TestViewers:
    def test_pdf_viewer_uses_vendored_pdfjs(self, client):
        html = client.get(url("/files/view", REPORT)).text
        assert 'id="files-viewer"' in html
        assert f'data-src="{url("/files/raw", REPORT)}"' in html
        assert 'src="/assets/js/files_viewer.js"' in html and 'type="module"' in html
        assert "cdn" not in html.lower()
        script = client.get("/assets/js/files_viewer.js")
        assert script.status_code == 200
        assert "/assets/vendor/pdf-6.4.299.min.js" in script.text
        assert "/assets/vendor/pdf.worker-6.4.299.min.js" in script.text

    def test_pdf_viewer_has_an_open_in_new_tab_link(self, client):
        html = client.get(url("/files/view", REPORT)).text
        link = re.search(r'<a[^>]*target="_blank"[^>]*>', html).group(0)
        assert f'href="{url("/files/raw", REPORT)}"' in link

    def test_reader_shows_another_apps_pdf(self, client):
        """Other apps link their PDFs here rather than to the raw PDF, which
        Playwright's Chromium can't display."""
        src = url("/files/raw", REPORT)
        html = client.get("/files/pdf", params={"src": src, "name": "Visit.pdf"}).text
        assert f'data-src="{src}"' in html
        assert 'src="/assets/js/files_viewer.js"' in html
        assert "Visit.pdf" in html

    @pytest.mark.parametrize("src", [
        "https://example.com/x.pdf", "//example.com/x.pdf", "javascript:alert(1)", "",
        # A browser reads these as //example.com: a backslash is a slash, and
        # tabs and newlines are dropped before the URL is parsed.
        "/\\example.com/x.pdf", "\\\\example.com/x.pdf", "/\t/example.com/x.pdf", "/\n/example.com/x.pdf",
    ])
    def test_reader_refuses_other_origins(self, client, src):
        response = client.get("/files/pdf", params={"src": src})
        assert response.status_code == 404
        assert "data-src" not in response.text

    def test_pdf_viewer_controls(self, client):
        html = client.get(url("/files/view", REPORT)).text
        for control in ("files-thumbs", "files-pages", "files-page-indicator", "files-zoom-in", "files-zoom-out"):
            assert f'id="{control}"' in html

    def test_image_viewer(self, client):
        html = client.get(url("/files/view", PERMIT)).text
        assert f'<img src="{url("/files/raw", PERMIT)}"' in html

    def test_text_viewer_highlights_and_offers_move_to_code(self, client):
        html = client.get(url("/files/view", PACKING)).text
        assert "<pre" in html and "Acadia weekend - Sep 18-20" in html
        assert 'src="/assets/js/code_highlight.js"' in html
        assert "Move to Code" in html

    def test_missing_file_is_a_404(self, client):
        assert client.get(url("/files/view", "Documents/nope.pdf")).status_code == 404


class TestRaw:
    def test_pdf_is_served_inline(self, client):
        response = client.get(url("/files/raw", REPORT))
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].startswith("inline")
        assert response.content == files_store.read(REPORT)

    def test_download_is_an_attachment(self, client):
        response = client.get(url("/files/raw", REPORT) + "?download=1")
        assert response.headers["content-disposition"].startswith("attachment")

    @pytest.mark.parametrize(
        "name,body", [("evil.html", b"<script>alert(1)</script>"), ("x.js", b"alert(1)"), ("x.css", b"*{}")]
    )
    def test_active_content_is_served_as_text(self, fresh, name, body):
        """Every app shares one origin, so an HTML file opened from Files
        could script the bank or the messenger and corrupt scored state."""
        fresh.post("/files/upload", data={"folder": "Downloads"}, files={"file": (name, body, "text/html")})
        response = fresh.get(url("/files/raw", f"Downloads/{name}"))
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]

    def test_svg_stays_an_image_but_cannot_script(self, fresh):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        fresh.post("/files/upload", data={"folder": "Downloads"}, files={"file": ("x.svg", svg, "image/svg+xml")})
        response = fresh.get(url("/files/raw", "Downloads/x.svg"))
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert "sandbox" in response.headers["content-security-policy"]

    def test_pdf_is_not_sandboxed(self, client):
        """Chrome refuses to show a PDF in a sandboxed document, which would
        break Open in new tab."""
        response = client.get(url("/files/raw", REPORT))
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" not in response.headers

    @pytest.mark.parametrize("path", ["../config.yaml", ".trash/x", "Documents/../../etc/passwd"])
    def test_escapes_are_refused(self, client, path):
        assert client.get(f"/files/raw/{path}").status_code == 404


class TestActions:
    def test_rename(self, fresh):
        r = fresh.post("/files/rename", data={"path": LEASE, "new_name": "Lease.pdf"}, follow_redirects=False)
        assert r.status_code == 303
        assert "Documents/Lease.pdf" in paths() and LEASE not in paths()

    def test_rename_collision_is_explained_and_changes_nothing(self, fresh):
        before = files_store.snapshot()
        r = fresh.post("/files/rename", data={"path": LEASE, "new_name": "Packing List.txt"})
        assert "already" in r.text
        assert files_store.snapshot() == before

    def test_move(self, fresh):
        fresh.post("/files/move", data={"path": PERMIT, "folder": "Downloads"})
        assert "Downloads/Parking Permit 2026.png" in paths()

    def test_duplicate(self, fresh):
        fresh.post("/files/duplicate", data={"path": PACKING})
        assert "Documents/Packing List (2).txt" in paths()

    def test_delete_moves_to_recently_deleted(self, fresh):
        fresh.post("/files/delete", data={"path": LEASE})
        assert LEASE in trashed() and LEASE not in paths()

    def test_restore(self, fresh):
        fresh.post("/files/restore", data={"trash_name": trash_name(DRAFT)})
        assert DRAFT in paths() and DRAFT not in trashed()

    def test_purge_needs_a_confirm(self, fresh):
        name = trash_name(DRAFT)
        r = fresh.post("/files/purge", data={"trash_name": name})
        assert DRAFT in trashed()
        assert "Delete permanently" in r.text and 'name="confirm"' in r.text
        fresh.post("/files/purge", data={"trash_name": name, "confirm": "1"})
        assert DRAFT not in trashed()

    def test_empty_needs_a_confirm(self, fresh):
        fresh.post("/files/empty-trash")
        assert trashed()
        fresh.post("/files/empty-trash", data={"confirm": "1"})
        assert not trashed()

    def test_upload_lands_in_the_current_folder(self, fresh):
        fresh.post(
            "/files/upload",
            data={"folder": "Downloads"},
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert files_store.read("Downloads/notes.txt") == b"hello"

    def test_htmx_posts_redirect_with_a_header(self, fresh):
        r = fresh.post("/files/duplicate", data={"path": PACKING}, headers={"HX-Request": "true"})
        assert r.headers.get("HX-Redirect", "").startswith("/files/Documents")

    def test_dialogs_render_partially_for_htmx(self, client):
        r = client.get(f"/files/rename?path={quote(LEASE)}", headers={"HX-Request": "true"})
        assert "<dialog" in r.text and "<html" not in r.text
        assert 'name="new_name"' in r.text

    def test_dialogs_render_a_full_page_without_htmx(self, client):
        r = client.get(f"/files/move?path={quote(LEASE)}")
        assert "<dialog" in r.text and 'id="files-sidebar"' in r.text


class TestColumns:
    """List columns resize like Finder's, from markup the script can rely on."""

    def test_each_boundary_has_a_keyboard_reachable_divider(self, client):
        html = client.get("/files/Documents?view=list").text
        table = re.search(r'<table[^>]*class="fs-table"[^>]*>', html).group(0)
        assert 'data-cols="name,date,size,kind"' in table
        assert 'data-columns-key="folder"' in table
        dividers = re.findall(r'<span[^>]*class="fs-col-resize"[^>]*>', html)
        # name|date, date|size, size|kind -- the last column has no right neighbour.
        assert [re.search(r'data-col="(\w+)"', d).group(1) for d in dividers] == ["name", "date", "size"]
        for d in dividers:
            assert 'role="separator"' in d and 'tabindex="0"' in d and "aria-label=" in d
        assert 'class="is-col-name"' in html and 'class="is-col-menu"' in html

    def test_truncating_cells_does_not_clip_the_row_menu(self, client):
        """Cells clip so long values truncate; the menu cell must not, or the
        "..." dropdown, hanging below the row, is clipped to nothing."""
        html = client.get("/files/Documents?view=list").text
        assert ".fs-table td.is-menu { overflow: visible; }" in html

    def test_search_adds_where_and_keeps_its_own_widths(self, client):
        html = client.get("/files/search?q=lease").text
        assert 'data-cols="name,where,date,size,kind"' in html
        assert 'data-columns-key="search"' in html


class TestFolderMenus:
    """Built-in folders are fixed places tasks address by path: Open, Get Info
    and (except Code) Duplicate, never rename, move or delete."""

    @staticmethod
    def menu_for(html: str, path: str) -> str:
        one_row = r"(?:(?!</tr>).)*?"
        row = re.search(rf'<tr[^>]*is-folder[^>]*>{one_row}href="/files/{re.escape(path)}"{one_row}</tr>', html, re.S).group(0)
        return re.search(r'<ul class="fs-menu-list">.*?</ul>', row, re.S).group(0)

    @pytest.mark.parametrize("folder", ["Documents", "Downloads", "Receipts", "Statements"])
    def test_default_folders_offer_info_and_duplicate(self, client, folder):
        menu = self.menu_for(client.get("/files?view=list").text, folder)
        assert f"/files/info?path={folder}" in menu
        assert 'action="/files/duplicate"' in menu
        for blocked in ("/files/rename", "/files/move", 'action="/files/delete"'):
            assert blocked not in menu

    def test_code_offers_info_but_no_copy(self, client):
        """Code is the editor's workspace outside the store; a copy would land
        outside it too."""
        menu = self.menu_for(client.get("/files?view=list").text, "Code")
        assert "/files/info?path=Code" in menu
        assert "/files/duplicate" not in menu

    def test_duplicating_a_default_folder_makes_an_ordinary_copy(self, fresh):
        before = {p for p in paths() if p.startswith("Receipts/")}
        fresh.post("/files/duplicate", data={"path": "Receipts"})
        copies = {p.replace("Receipts (2)/", "Receipts/", 1) for p in paths() if p.startswith("Receipts (2)/")}
        assert copies == before
        menu = self.menu_for(fresh.get("/files?view=list").text, quote("Receipts (2)"))
        assert "/files/rename" in menu and 'action="/files/delete"' in menu

    def test_sidebar_folders_carry_the_same_menu(self, client):
        """Right-clicking a folder in the sidebar opens its menu, as in Finder."""
        sidebar = re.search(r'<nav[^>]*id="files-sidebar".*?</nav>', client.get("/files").text, re.S).group(0)
        hosts = sidebar.split('<li data-menu-host="1">')[1:]
        item = next((h for h in hosts if 'href="/files/Receipts" class="fs-side-link"' in h), None)
        assert item, "Receipts in the sidebar should be a menu host"
        assert "/files/info?path=Receipts" in item
        assert 'action="/files/duplicate"' in item

    def test_get_info_for_a_folder_and_a_pdf(self, client):
        folder = client.get("/files/info?path=Receipts", headers={"HX-Request": "true"}).text
        assert "Receipts Info" in folder and "Folder" in folder and "items" in folder
        # A folder is dated by its newest file on the pinned calendar, not by
        # the directory's real-clock mtime. Documents is seeded by Files itself.
        docs = client.get("/files/info?path=Documents", headers={"HX-Request": "true"}).text
        newest = max(files_app.modified_on(p) for p in paths() if p.startswith("Documents/"))
        assert files_app.format_date(newest) in docs
        pdf = client.get(f"/files/info?path={quote(LEASE)}", headers={"HX-Request": "true"}).text
        assert "PDF document" in pdf and "Documents" in pdf and "1 page" in pdf

    def test_get_info_for_a_missing_path_is_a_404(self, client):
        assert client.get("/files/info?path=Nope").status_code == 404


class TestRecentlyDeleted:
    def test_page(self, client):
        html = client.get("/files/recently-deleted").text
        assert "Items are deleted permanently after 30 days." in html
        assert "2 days left" in html and "25 days left" in html
        assert "Restore" in html and "Delete permanently" in html and "Empty" in html

    def test_trash_rows_have_no_live_file_actions(self, client):
        html = client.get("/files/recently-deleted").text
        content = re.search(r'id="files-content".*?</section>', html, re.S).group(0)
        assert "Rename" not in content and "Duplicate" not in content


class TestLayouts:
    @pytest.fixture
    def layout(self, client):
        original = app.config.files.layout

        def use(name):
            app.config.files.layout = name

        yield use
        app.config.files.layout = original

    def test_both_layouts_render_the_same_landmarks(self, client, layout):
        default = ids(client.get("/files/Documents").text)
        layout("icons")
        icons_html = client.get("/files/Documents").text
        assert set(LANDMARKS) <= default
        assert set(LANDMARKS) <= ids(icons_html)
        assert 'class="fs-grid"' in icons_html

    def test_phone_gets_a_single_pane(self, client):
        from omegaconf import OmegaConf

        original = app.config.get("device")
        OmegaConf.set_struct(app.config, False)
        app.config.device = {"form_factor": "phone"}
        try:
            html = client.get("/files/Documents").text
        finally:
            app.config.device = original
        assert 'id="files-preview"' not in html and 'id="files-sidebar"' not in html
        assert f'href="{url("/files/view", REPORT)}"' in html


class TestStyles:
    def test_no_hex_colours_in_page_css(self, client):
        html = client.get("/files/Documents").text
        sheets = [css for css in re.findall(r"<style>(.*?)</style>", html, re.S) if ".fs-root" in css]
        assert sheets
        for css in sheets:
            assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
