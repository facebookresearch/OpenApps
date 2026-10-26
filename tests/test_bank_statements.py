"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Monthly bank statements: built from the ledger, viewed inline, saved to Files.

A statement is scored by where it lands (``Statements/<account> — <Month
YYYY>.pdf``) and trusted for what it says, so the figures have to agree with
the ledger the account page shows -- opening plus credits plus debits is the
closing balance, and the closing balance is the last posted balance of the
month. Downloading must not touch the bank's own state.
"""

import re
from pathlib import Path
from urllib.parse import quote

import pytest
from hydra import compose, initialize
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import files_store
from open_apps.apps.openbanking_app import main as bank
from open_apps.apps.openbanking_app import statements
from open_apps.apps.start_page.main import app, initialize_routes_and_configure_task

CHECKING, SAVINGS, CARD = 0, 1, 2
PACKS = ["default", "german", "mandarin", "long_descriptions", "misleading_descriptions",
         "adversarial_descriptions"]


def _compose(tmp_path_factory, *overrides):
    logs_dir = tmp_path_factory.mktemp("statements")
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(config_name="config", overrides=[f"logs_dir={logs_dir}", *overrides])
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    cfg = _compose(tmp_path_factory)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


def pdf_text(data: bytes) -> str:
    runs = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", data)
    return " ".join(r.decode("latin-1").replace("\\(", "(").replace("\\)", ")") for r in runs)


class TestPostingDates:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Aug 25, 2026", (2026, 8, 25)),
            ("Jul 01, 2026", (2026, 7, 1)),
            ("25. Aug. 2026", (2026, 8, 25)),
            ("15. Juli 2026", (2026, 7, 15)),
            ("18. Juni 2026", (2026, 6, 18)),
            ("3. März 2026", (2026, 3, 3)),
            ("2026年8月25日", (2026, 8, 25)),
            ("2026年7月1日", (2026, 7, 1)),
        ],
    )
    def test_every_pack_spelling_parses(self, text, expected):
        assert statements.parse_posting_date(text).timetuple()[:3] == expected

    @pytest.mark.parametrize("text", [None, "", "pending", "Smarch 3, 2026"])
    def test_unparseable_is_none(self, text):
        assert statements.parse_posting_date(text) is None


class TestMonths:
    def test_only_months_with_posted_rows_are_offered(self, client):
        months = {a.id: bank.statement_months(a) for a in bank.account_rows()}
        assert months[CHECKING] == [(2026, 8)]
        assert months[SAVINGS] == [(2026, 8), (2026, 7), (2026, 6)]
        assert months[CARD] == [(2026, 8), (2026, 7)]

    def test_pending_rows_are_left_out(self, client):
        rows = bank.statement_rows(bank.get_account(CHECKING), (2026, 8))
        assert all(r.date is not None for r in rows)
        assert len(rows) == 6  # the seventh, the real-time transfer, is pending

    def test_rows_run_oldest_first(self, client):
        rows = bank.statement_rows(bank.get_account(SAVINGS), (2026, 7))
        assert [r.description for r in rows] == ["INTEREST PAYMENT", "TRANSFER FROM CHK ...5555"]

    @pytest.mark.parametrize(
        "month,label",
        [((2026, 7), "July 2026"), ((2026, 12), "December 2026"), ((2027, 1), "January 2027")],
    )
    def test_month_label_is_english(self, month, label):
        assert statements.month_label(month) == label

    def test_month_keys_round_trip(self):
        assert statements.month_key((2026, 7)) == "2026-07"
        assert statements.parse_month("2026-07") == (2026, 7)
        for bad in ("2026-13", "July", "2026-7-1", ""):
            assert statements.parse_month(bad) is None


@pytest.mark.parametrize("pack", PACKS)
def test_totals_equal_ledger_sums_for_every_account_and_month(pack, tmp_path_factory):
    """Opening + credits + debits = closing, the closing balance is the last
    posted balance of the month, and each month opens where the one before it
    closed -- in every content pack, including the appended noise accounts."""
    cfg = OmegaConf.to_container(
        _compose(tmp_path_factory, f"apps/openbanking/content={pack}").apps.openbanking.accounts,
        resolve=True,
    )
    for account in cfg:
        txns = [dict(t, position=i) for i, t in enumerate(account["transactions"])]
        previous_close = None
        for month in reversed(statements.statement_months(txns)):
            st = statements.build_statement(txns, month)
            assert st.rows, f"{account['name']} {month}: offered with no rows"
            assert round(st.opening + st.credits + st.debits, 2) == st.closing
            assert st.closing == round(float(st.rows[-1]["balance"]), 2)
            assert st.credits == round(sum(r["amount"] for r in st.rows if r["amount"] >= 0), 2)
            assert st.debits == round(sum(r["amount"] for r in st.rows if r["amount"] < 0), 2)
            if previous_close is not None:
                assert st.opening == previous_close, f"{account['name']} {month} opens off its prior close"
            previous_close = st.closing


class TestNames:
    def test_statement_path(self):
        assert (
            statements.statement_path("BUS SELECT SAVINGS (...8891)", (2026, 7))
            == "Statements/BUS SELECT SAVINGS (...8891) — July 2026.pdf"
        )

    @pytest.mark.parametrize("name", ["A/B CHK", "..hidden", "back\\slash"])
    def test_names_the_store_would_refuse_are_made_safe(self, name):
        filename = statements.statement_name(name, (2026, 8))
        assert "/" not in filename and "\\" not in filename and not filename.startswith(".")


class TestPages:
    def test_account_page_links_its_statements(self, client):
        body = client.get(f"/openbanking/accounts/{SAVINGS}").text
        assert f'href="/openbanking/accounts/{SAVINGS}/statements"' in body

    def test_statements_page_lists_each_month(self, client):
        body = client.get(f"/openbanking/accounts/{SAVINGS}/statements").text
        for label in ("August 2026", "July 2026", "June 2026"):
            assert label in body
        assert f"/openbanking/accounts/{SAVINGS}/statements/2026-07/view" in body
        assert f"/openbanking/accounts/{SAVINGS}/statements/2026-07/download" in body

    def test_view_opens_in_a_new_tab(self, client):
        body = client.get(f"/openbanking/accounts/{SAVINGS}/statements").text
        view = re.search(r'<a[^>]*statements/2026-07/view[^>]*>', body).group(0)
        assert 'target="_blank"' in view

    def test_unknown_account_or_month_404s(self, client):
        assert client.get("/openbanking/accounts/99/statements/2026-07/view").status_code == 404
        assert client.get(f"/openbanking/accounts/{CHECKING}/statements/2026-07/view").status_code == 404
        assert client.get(f"/openbanking/accounts/{SAVINGS}/statements/nonsense/view").status_code == 404


class TestView:
    def test_serves_an_inline_pdf(self, client):
        response = client.get(f"/openbanking/accounts/{SAVINGS}/statements/2026-07/view")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].startswith("inline")
        assert response.content.startswith(b"%PDF-")

    def test_figures_match_the_ledger(self, client):
        text = pdf_text(client.get(f"/openbanking/accounts/{SAVINGS}/statements/2026-07/view").content)
        assert "$18,776.25" in text  # opening: June's close
        assert "$22,292.13" in text  # closing: the Jul 15 posted balance
        assert "Jul 1, 2026 - Jul 31, 2026" in text

    def test_the_current_month_runs_to_the_banks_today(self, client):
        text = pdf_text(client.get(f"/openbanking/accounts/{CHECKING}/statements/2026-08/view").content)
        assert "Aug 1, 2026 - Aug 26, 2026" in text

    def test_card_statement_carries_the_cycle_figures(self, client):
        text = pdf_text(client.get(f"/openbanking/accounts/{CARD}/statements/2026-08/view").content)
        for figure in ("$872.19", "$35.00", "Sep 15, 2026"):
            assert figure in text
        july = pdf_text(client.get(f"/openbanking/accounts/{CARD}/statements/2026-07/view").content)
        assert "Sep 15, 2026" not in july  # the Aug 21 cycle isn't July's

    def test_same_statement_same_bytes(self, client):
        url = f"/openbanking/accounts/{CARD}/statements/2026-08/view"
        assert client.get(url).content == client.get(url).content


class TestDownload:
    PATH = "Statements/BUS SELECT SAVINGS (...8891) — July 2026.pdf"

    def test_saves_to_files_and_offers_to_open_it(self, client):
        before = client.get("/openbanking_all").text
        response = client.post(f"/openbanking/accounts/{SAVINGS}/statements/2026-07/download")
        assert response.status_code == 200
        assert files_store.exists(self.PATH)
        assert files_store.read(self.PATH) == client.get(
            f"/openbanking/accounts/{SAVINGS}/statements/2026-07/view"
        ).content
        assert f'href="/files/view/{quote(self.PATH)}"' in response.text
        assert client.get("/openbanking_all").text == before

    def test_a_download_is_dated_by_the_worlds_today(self, client):
        import datetime

        client.post(f"/openbanking/accounts/{SAVINGS}/statements/2026-07/download")
        stamp = files_store.resolve(self.PATH).stat().st_mtime
        assert datetime.date.fromtimestamp(stamp).isoformat() == str(app.config.files.today)

    def test_downloading_again_keeps_one_copy(self, client):
        for _ in range(2):
            client.post(f"/openbanking/accounts/{SAVINGS}/statements/2026-07/download")
        paths = [r["path"] for r in client.get("/files_all").json()["files"]]
        assert paths.count(self.PATH) == 1
        assert not any("July 2026 (2)" in p for p in paths)

    def test_a_different_file_in_the_way_is_not_overwritten(self, client):
        path = "Statements/BUS COMPLETE CHK (...5555) — August 2026.pdf"
        files_store.save("Statements", path.split("/", 1)[1], b"not a statement")
        try:
            client.post(f"/openbanking/accounts/{CHECKING}/statements/2026-08/download")
            assert files_store.read(path) == b"not a statement"
            assert files_store.exists("Statements/BUS COMPLETE CHK (...5555) — August 2026 (2).pdf")
        finally:
            for p in (path, "Statements/BUS COMPLETE CHK (...5555) — August 2026 (2).pdf"):
                if files_store.exists(p):
                    files_store.purge(files_store.delete(p))

    def test_unknown_month_writes_nothing(self, client):
        before = client.get("/files_all").text
        assert client.post(f"/openbanking/accounts/{CHECKING}/statements/2026-07/download").status_code == 404
        assert client.get("/files_all").text == before


LABELS = ["statements_label", "statements_heading", "view_label", "download_label",
          "saved_to_files_label", "already_in_files_label", "open_in_files_label",
          "no_statements_label"]


@pytest.mark.parametrize("pack", ["default", "german", "mandarin"])
def test_every_translated_pack_labels_statements(pack, tmp_path_factory):
    bank_cfg = _compose(tmp_path_factory, f"apps/openbanking/content={pack}").apps.openbanking
    english = _compose(tmp_path_factory).apps.openbanking
    for key in LABELS:
        assert bank_cfg.get(key), f"{pack} has no {key}"
        if pack != "default":
            assert bank_cfg[key] != english[key], f"{pack} leaves {key} in English"
