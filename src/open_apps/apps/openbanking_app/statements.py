"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Monthly statements, computed from the ledger and nothing else.

Pure functions over transaction rows -- the bank's ``Transaction`` objects or
the dicts ``/openbanking_all`` returns -- so the Statements page, the PDF, and
a task that needs a statement's expected file name all read the same months
and the same rows. A figure on a statement is the ledger's figure by
construction: opening balance is the first row's posted balance minus its
amount, closing balance is the last row's posted balance, and the bank's
seeded ledgers are self-consistent (``tests/test_bank_statements.py`` checks
every pack).

Pending rows (no date yet) belong on no statement. That also keeps a shop
checkout -- which posts pending rows -- from changing any statement.

Posting dates are stored as each content pack prints them ("Aug 25, 2026",
"25. Aug. 2026", "2026年8月25日"), so :func:`parse_posting_date` reads all
three. The statement itself, and its file name, are always English: file
names are what tasks address, and the PDF fonts only cover Western European
text anyway (see ``open_apps.pdf_writer``).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

STATEMENTS_FOLDER = "Statements"

# Spelled out rather than taken from ``calendar.month_name``, which follows the
# process locale: a statement's name must not change with the host's LANG.
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")
MONTH_ABBR = tuple(name[:3] for name in MONTH_NAMES)
_EN_MONTHS = {abbr.lower(): i for i, abbr in enumerate(MONTH_ABBR, start=1)}
_DE_MONTHS = {
    "jan": 1, "januar": 1, "feb": 2, "februar": 2, "märz": 3, "mär": 3, "mrz": 3,
    "apr": 4, "april": 4, "mai": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10,
    "oktober": 10, "nov": 11, "november": 11, "dez": 12, "dezember": 12,
}
_EN = re.compile(r"^([A-Za-z]{3})[a-z]*\.? (\d{1,2}), (\d{4})$")
_DE = re.compile(r"^(\d{1,2})\. ?([A-Za-zä]+)\.? (\d{4})$")
_ZH = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")
_MONTH_KEY = re.compile(r"^(\d{4})-(\d{2})$")


def parse_posting_date(text: Optional[str]) -> Optional[date]:
    """The date a ledger row posted on, in any pack's spelling, or ``None``."""
    if not text:
        return None
    text = str(text).strip()
    try:
        if m := _EN.match(text):
            month = _EN_MONTHS.get(m.group(1).lower())
            return date(int(m.group(3)), month, int(m.group(2))) if month else None
        if m := _DE.match(text):
            month = _DE_MONTHS.get(m.group(2).lower())
            return date(int(m.group(3)), month, int(m.group(1))) if month else None
        if m := _ZH.match(text):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:  # "Feb 30, 2026"
        return None
    return None


def month_key(month: tuple[int, int]) -> str:
    """``(2026, 7)`` -> ``"2026-07"``, the form URLs carry."""
    return f"{month[0]:04d}-{month[1]:02d}"


def parse_month(key: str) -> Optional[tuple[int, int]]:
    m = _MONTH_KEY.match(str(key or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    return int(m.group(1)), int(m.group(2))


def month_label(month: tuple[int, int]) -> str:
    """``(2026, 7)`` -> ``"July 2026"``."""
    return f"{MONTH_NAMES[month[1] - 1]} {month[0]}"


def statement_name(account_name: str, month: tuple[int, int]) -> str:
    """``<account name> — <Month YYYY>.pdf``, made safe for the file store.

    A slash or backslash would read as a folder and a leading dot as hidden,
    both of which the store refuses; noise accounts can be named anything.
    """
    safe = re.sub(r"[/\\]", "-", str(account_name)).lstrip(".").strip() or "Account"
    return f"{safe} — {month_label(month)}.pdf"


def statement_path(account_name: str, month: tuple[int, int]) -> str:
    return f"{STATEMENTS_FOLDER}/{statement_name(account_name, month)}"


def _get(row: Any, field: str):
    return row[field] if isinstance(row, dict) else getattr(row, field)


def _posted(transactions) -> list[tuple[date, Any]]:
    """Dated rows, oldest first. Ledger ``position`` 0 is the newest row, so
    within a day the higher position posted first."""
    rows = []
    for row in transactions:
        posted = parse_posting_date(_get(row, "date"))
        if posted is not None:
            rows.append((posted, row))
    return sorted(rows, key=lambda pair: (pair[0], -int(_get(pair[1], "position"))))


def statement_months(transactions) -> list[tuple[int, int]]:
    """Every month with at least one posted row, newest first."""
    return sorted({(d.year, d.month) for d, _ in _posted(transactions)}, reverse=True)


def statement_rows(transactions, month: tuple[int, int]) -> list:
    """The rows posted in ``month``, oldest first."""
    return [row for d, row in _posted(transactions) if (d.year, d.month) == tuple(month)]


@dataclass(frozen=True)
class Statement:
    month: tuple[int, int]
    rows: list
    opening: float
    credits: float
    debits: float  # negative, as the ledger signs it
    closing: float


def build_statement(transactions, month: tuple[int, int]) -> Statement:
    rows = statement_rows(transactions, month)
    if not rows:
        raise ValueError(f"no posted rows in {month_label(month)}")
    amounts = [float(_get(r, "amount")) for r in rows]
    first = rows[0]
    return Statement(
        month=tuple(month),
        rows=rows,
        opening=round(float(_get(first, "balance")) - float(_get(first, "amount")), 2),
        credits=round(sum(a for a in amounts if a >= 0), 2),
        debits=round(sum(a for a in amounts if a < 0), 2),
        closing=round(float(_get(rows[-1], "balance")), 2),
    )


def period_label(month: tuple[int, int], today: date) -> str:
    """``Jul 1, 2026 - Jul 31, 2026``; the bank's current month stops at
    ``today``, the way an online statement-to-date does."""
    year, mon = month
    last = calendar.monthrange(year, mon)[1]
    if (today.year, today.month) == (year, mon):
        last = today.day
    abbr = MONTH_ABBR[mon - 1]
    return f"{abbr} 1, {year} - {abbr} {last}, {year}"


def document_spec(account: Any, statement: Statement, *, bank: str, today: date,
                  card_summary: Optional[list[tuple[str, str]]] = None) -> dict:
    """The ``bank_statement`` spec ``files_app.documents`` renders -- the same
    shape the seeded June statement is written in."""
    return {
        "kind": "bank_statement",
        "bank": bank,
        "account": _get(account, "name"),
        "holder": _get(account, "holder"),
        "period": period_label(statement.month, today),
        "opening_balance": statement.opening,
        "rows": [
            [f"{MONTH_ABBR[d.month - 1]} {d.day:02d}", str(_get(r, "description")),
             float(_get(r, "amount"))]
            for d, r in ((parse_posting_date(_get(r, "date")), r) for r in statement.rows)
        ],
        **({"card_summary": [list(p) for p in card_summary]} if card_summary else {}),
    }
