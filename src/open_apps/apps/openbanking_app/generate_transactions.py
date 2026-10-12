"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Entry point: ``python -m open_apps.apps.openbanking_app.generate_transactions``
/ ``openbanking-gen-txns``.

Generates extra ledger rows for one seeded OpenBanking account and either
prints them or splices them into the content files that carry that account.

Why a generator rather than hand-written YAML: the ledger has to stay
*arithmetically coherent* to be worth reading. Each row's ``balance`` is the
running balance after that posting, so a row appended to the end has to chain
off the oldest existing one -- ``balance[i+1] = balance[i] - amount[i]`` -- and
its date has to fall on or before the oldest existing date. Getting that wrong
by hand is easy and invisible: the page still renders, and only an agent asked
to reconcile the account notices.

Three properties the app depends on are preserved by construction:

* **Determinism.** ``--seed`` fixes the whole draw, so re-running with the same
  arguments produces byte-identical YAML. The app seeds its tables from config
  at startup and ``/openbanking_all`` must be byte-stable (see
  ``openbanking_app/main.py``), so a generator that drifted between runs would
  make the config itself a source of nondeterminism. This is also why no date
  here ever defaults to *today*: see ``generate``.
* **Variant parity.** ``german.yaml`` and ``mandarin.yaml`` restate the seeded
  accounts in full rather than inheriting them, and every amount and balance
  has to agree across all three or the same task needs a different answer per
  language (``test_seeded_account_figures_are_identical``). So a write fans out
  by default: the generated figures are spliced into *every* variant that
  carries the account, with each file's own date format and type vocabulary.
  ``--variants source`` opts out.
* **Config, not database.** The output is seed YAML. Nothing here writes to
  ``openbanking.db``: the app is read-only at runtime, and mutating the live
  database out from under it would inject a diff into every unrelated
  todo/calendar task's reward.

What it will *not* do for you: descriptions are free text, so a fanned-out row
keeps the source variant's wording in the translated files and is flagged with
a comment for a human to finish. And ``config/tasks/openbanking.yaml`` reads
specific figures off specific accounts, with some goals quantifying over the
ledger ("exactly two transactions of type 'Card'", "the one of type 'Card' with
the largest amount"). Use ``--types`` and ``--max-amount`` to stay inside those,
and re-run ``pytest tests/test_openbanking.py`` after writing.

Examples::

    # Preview three rows for the checking account, as every variant would write
    # them
    openbanking-gen-txns --account "BUS COMPLETE CHK (...5555)" --count 3

    # Write two small rows into default.yaml, german.yaml and mandarin.yaml
    openbanking-gen-txns --account "BUS SELECT SAVINGS (...8891)" \\
        --count 2 --max-amount 500 --in-place

    # Write only the file they were generated against
    openbanking-gen-txns --account "5555" --count 1 --variants source --in-place
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Optional

import yaml

from open_apps import config_dir

CONTENT_DIR = config_dir() / "apps" / "openbanking" / "content"

# The date format the English content files print.
DATE_FORMAT = "%b %d, %Y"

# Business days between generated postings, drawn per row. Small enough that a
# handful of rows stay inside one statement cycle.
DAY_STEP = (1, 6)

# German month names as the seeded ledger writes them -- the standard
# abbreviations, which leave the four short months (März, Mai, Juni, Juli)
# unabbreviated and undotted.
#
# A table rather than `strftime`: `%b` renders through the C locale, so it
# would emit "Aug" on a machine without `de_DE` installed and "Aug." on one
# with it. A seed generator whose output depends on the host's installed
# locales is not deterministic in any useful sense.
GERMAN_MONTHS = (
    "Jan.",
    "Feb.",
    "März",
    "Apr.",
    "Mai",
    "Juni",
    "Juli",
    "Aug.",
    "Sep.",
    "Okt.",
    "Nov.",
    "Dez.",
)

_GERMAN_DATE = re.compile(r"^(\d{1,2})\.\s+(\S+)\s+(\d{4})$")
_MANDARIN_DATE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")

# Each variant's word for each transaction type, taken from the seeded rows
# rather than invented -- a generated "Card" charge dropped into `german.yaml`
# untranslated would read as a type that file has never used, and the card
# tasks count rows by type. `test_the_type_tables_match_the_seeded_vocabulary`
# holds these to what the content files actually say.
GERMAN_TYPES = {
    "ACH credit": "SEPA-Gutschrift",
    "ACH debit": "SEPA-Lastschrift",
    "Card": "Karte",
    "Deposit": "Einzahlung",
    "Fee": "Gebühr",
    "Interest": "Zinsen",
    "Other": "Sonstige",
    "Payment": "Zahlung",
    "Refund": "Gutschrift",
    "Transfer": "Überweisung",
}
MANDARIN_TYPES = {
    "ACH credit": "ACH 入账",
    "ACH debit": "ACH 出账",
    "Card": "银行卡",
    "Deposit": "存款",
    "Fee": "手续费",
    "Interest": "利息",
    "Other": "其他",
    "Payment": "还款",
    "Refund": "退款",
    "Transfer": "转账",
}


def _render_english(when: date) -> str:
    return when.strftime(DATE_FORMAT)


def _read_english(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, DATE_FORMAT).date()
    except ValueError:
        return None


def _render_german(when: date) -> str:
    return f"{when.day:02d}. {GERMAN_MONTHS[when.month - 1]} {when.year}"


def _read_german(value: str) -> Optional[date]:
    match = _GERMAN_DATE.match(value)
    if match is None or match.group(2) not in GERMAN_MONTHS:
        return None
    day, month, year = match.groups()
    return date(int(year), GERMAN_MONTHS.index(month) + 1, int(day))


def _render_mandarin(when: date) -> str:
    return f"{when.year}年{when.month}月{when.day}日"


def _read_mandarin(value: str) -> Optional[date]:
    match = _MANDARIN_DATE.match(value)
    if match is None:
        return None
    year, month, day = (int(group) for group in match.groups())
    return date(year, month, day)


@dataclass(frozen=True)
class Variant:
    """How one content file writes a row that the other files also carry.

    Fan-out copies a generated row's *figures* verbatim -- that is the whole
    point, the variants have to agree on every amount and balance -- and
    re-expresses everything a reader sees. Dates and types are closed
    vocabularies, so they translate by table; the description is free text and
    does not, so it is copied in the source language and flagged.
    """

    stem: str
    render_date: Callable[[date], str]
    read_date: Callable[[str], Optional[date]]
    # Source-language type -> this variant's word for it. Empty on an English
    # variant, which needs no mapping.
    types: Mapping[str, str] = field(default_factory=dict)


# Keyed by content-file stem. A stem that is missing from here is treated as
# English (`long_descriptions` and the other noise variants are), which is only
# ever wrong for a file that translates -- and `fanout_targets` refuses to
# write such a file rather than guessing.
VARIANTS: dict[str, Variant] = {
    "default": Variant("default", _render_english, _read_english),
    "german": Variant("german", _render_german, _read_german, GERMAN_TYPES),
    "mandarin": Variant("mandarin", _render_mandarin, _read_mandarin, MANDARIN_TYPES),
}


@dataclass(frozen=True)
class Merchant:
    """One row's wording and type, and the sign its amount takes.

    `credit` is what makes a draw coherent: "PAYROLL" has to be money out of a
    checking account and "PAYMENT THANK YOU" money off a card, so the sign
    belongs to the merchant, not to a coin flip.
    """

    description: str
    type: str
    credit: bool


# Deposit-account wording, modelled on the seeded rows: real statements print
# the originator block, not a tidy merchant name.
DEPOSIT_MERCHANTS = [
    Merchant(
        "ORIG CO NAME:{payer} ORIG ID:{oid} DESC DATE:{yymmdd} CO ENTRY DESCR:"
        "SETTLEMENT SEC:CCD TRACE#:{trace} EED:{yymmdd} IND ID: IND NAME:{holder} "
        "TRN: {trn}",
        "ACH credit",
        True,
    ),
    Merchant(
        "ORIG CO NAME:{payer} ORIG ID:{oid} DESC DATE:{yymmdd} CO ENTRY DESCR:"
        "PAYABLE SEC:CCD TRACE#:{trace} EED:{yymmdd} IND ID: IND NAME:{holder} "
        "TRN: {trn}",
        "ACH debit",
        False,
    ),
    Merchant(
        "DEPOSIT REMOTE CAPTURE BATCH {batch} ITEM COUNT {items} PROCESSED "
        "{mmdd} 09:41:12 CT",
        "Deposit",
        True,
    ),
    Merchant("TRANSFER FROM CHK ...5555", "Transfer", True),
    Merchant("TRANSFER TO CHK ...5555", "Transfer", False),
    Merchant("{merchant} {domain} TX {mmdd} (...{tail})", "Card", False),
    Merchant("MONTHLY MAINTENANCE FEE", "Fee", False),
    Merchant("INTEREST PAYMENT", "Interest", True),
]

# Card wording. A card ledger is charges, payments and finance costs; there is
# no routing traffic on it.
CARD_MERCHANTS = [
    Merchant("{merchant} {domain} {state} {mmdd} (...{tail})", "Card", False),
    Merchant("PAYMENT THANK YOU", "Payment", True),
    Merchant("ANNUAL MEMBERSHIP FEE", "Fee", False),
    Merchant("INTEREST CHARGE ON PURCHASES", "Interest", False),
    Merchant("RETURN CREDIT {merchant} (...{tail})", "Refund", True),
]

# Invented businesses. Deliberately generic and clearly synthetic -- these end
# up in a public config file.
MERCHANT_NAMES = [
    "CLOUD HOST LLC",
    "PRINT SHOP 44",
    "FLEET UNIFORM SUPPLY",
    "NORTHSIDE COURIER",
    "DEPOT HARDWARE CO",
    "BLUE LINE TELECOM",
    "PARKVIEW CATERING",
    "SUMMIT OFFICE PARK",
]
MERCHANT_DOMAINS = [
    "CLOUDHOST.IO",
    "PRINTSHOP44.COM",
    "FLEETUNI.COM",
    "NSCOURIER.COM",
    "DEPOTHW.COM",
    "BLUELINETEL.COM",
    "PARKVIEWCAT.COM",
    "SUMMITOP.COM",
]
PAYER_NAMES = [
    "PART CORP INC",
    "GLOBAL LOGISTICS PARTNERS",
    "PAYROLL PARTNERS",
    "STATE DEPT OF REVENUE",
    "MERIDIAN FACILITIES",
]
STATES = ["TX", "NY", "WA", "MD", "IL", "CA"]


# ---------------------------------------------------------------------------
# Reading the seed


def content_path(name: str) -> Path:
    """Resolve ``--content`` to a file: a variant stem or an explicit path."""
    candidate = Path(name)
    if candidate.suffix == ".yaml" and candidate.exists():
        return candidate
    return CONTENT_DIR / f"{name}.yaml"


def load_accounts(path: Path) -> list[dict]:
    """The accounts a content file *owns*.

    The noise variants append with ``+accounts`` (resolved by
    ``open_apps.utils.merge_plus_keys``, not by Hydra), so both keys count. A
    file that declares neither owns no account, which is the useful thing to
    say when the requested one is missing -- it lives in ``default.yaml``.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("accounts") or data.get("+accounts") or [])


def find_account(accounts: list[dict], wanted: str) -> dict:
    """Match an account by exact name, then by its last-four tail."""
    for account in accounts:
        if account.get("name") == wanted:
            return account
    tail = wanted.strip().lstrip(".").rstrip(")")[-4:]
    if tail.isdigit():
        matches = [a for a in accounts if str(a.get("name", "")).endswith(f"{tail})")]
        if len(matches) == 1:
            return matches[0]
    names = "\n  ".join(str(a.get("name")) for a in accounts) or "(none)"
    raise SystemExit(f"No account matching {wanted!r} in this file. It has:\n  {names}")


def oldest_posting(account: dict) -> tuple[float, Optional[date]]:
    """The balance and date a generated row has to chain off.

    The *last* row is the oldest one -- the ledger is ordered newest-first --
    and its balance is the running balance after it posted, so the next row
    back carries ``balance - amount``. Pending rows (``balance: null``) are
    skipped: they have no posted balance to chain from.
    """
    txns = list(account.get("transactions") or [])
    for txn in reversed(txns):
        if txn.get("balance") is None:
            continue
        balance = round(float(txn["balance"]) - float(txn["amount"]), 2)
        return balance, parse_date(txn.get("date"))
    # An account with no posted rows at all: start from its own balance.
    return float(account.get("present_balance", 0.0)), None


def parse_date(value) -> Optional[date]:
    """The seeded date string as a date, in whichever variant's format it is in.

    Every registered variant is tried, so generating against ``german`` chains
    off "05. Aug. 2026" as readily as against ``default``. None means no
    variant recognised it, and the caller has to be told to pass
    ``--start-date`` -- it is never quietly replaced with today.
    """
    if not value:
        return None
    text = str(value)
    for variant in VARIANTS.values():
        parsed = variant.read_date(text)
        if parsed is not None:
            return parsed
    return None


# ---------------------------------------------------------------------------
# Generating


def draw_amount(rng: random.Random, credit: bool, max_amount: float) -> float:
    """A figure with the cents a real posting has, not a round number."""
    magnitude = round(rng.uniform(max_amount * 0.02, max_amount), 2)
    return magnitude if credit else -magnitude


def render_description(rng: random.Random, merchant: Merchant, when: date, holder: str):
    index = rng.randrange(len(MERCHANT_NAMES))
    return merchant.description.format(
        merchant=MERCHANT_NAMES[index],
        domain=MERCHANT_DOMAINS[index],
        payer=rng.choice(PAYER_NAMES),
        state=rng.choice(STATES),
        holder="".join(ch for ch in holder.upper() if ch.isalnum())[:12],
        oid=rng.randrange(100, 1000),
        trace=rng.randrange(100, 1000),
        trn=f"{rng.randrange(100, 1000)}{rng.choice('ABCDEF')}{rng.randrange(10, 100)}",
        batch=f"{rng.randrange(1, 999):05d}",
        items=f"{rng.randrange(1, 20):03d}",
        tail=f"{rng.randrange(1000, 10000)}",
        yymmdd=when.strftime("%y%m%d"),
        mmdd=when.strftime("%m/%d"),
    )


def generate(
    account: dict,
    count: int,
    seed: int,
    max_amount: float,
    types: Optional[list[str]],
    start: Optional[date],
) -> list[dict]:
    """``count`` rows continuing ``account``'s ledger backwards in time.

    Rows carry ``when`` as a real ``date`` rather than a formatted string:
    which string it becomes depends on the variant it is written into, and
    ``localize`` decides that per file.
    """
    rng = random.Random(seed)
    is_card = account.get("kind", "deposit") == "credit_card"
    pool = CARD_MERCHANTS if is_card else DEPOSIT_MERCHANTS
    if types:
        wanted = {t.casefold() for t in types}
        available = sorted({m.type for m in pool})
        pool = [m for m in pool if m.type.casefold() in wanted]
        if not pool:
            raise SystemExit(
                "--types matched nothing. A "
                f"{'card' if is_card else 'deposit'} account can take: "
                f"{', '.join(available)}"
            )

    balance, oldest = oldest_posting(account)
    when = start or oldest
    if when is None:
        # Deliberately not `date.today()`. Three reasons, any one of them
        # enough: the seeded ledger is a fixed corpus dated Aug 2026 and rows
        # are appended *older* than its oldest posting, so today's date would
        # sort a new row above the ones it is supposed to sit under; the
        # generator's output must be byte-identical for the same arguments, and
        # a date that moves with the calendar makes the config a source of
        # nondeterminism; and the card's statement cycle only reconciles
        # because every posting falls where the seed comments say it does.
        raise SystemExit(
            f"{account.get('name')!r} has no posted row this generator can read a "
            "date from, so there is nothing to chain the new rows off. Pass "
            "--start-date YYYY-MM-DD.\n"
            "Today's date is not used as a fallback: the ledger is a fixed corpus, "
            "rows are appended older than the oldest posting, and output that "
            "changed with the calendar would stop being reproducible."
        )
    holder = str(account.get("holder", ""))

    rows = []
    for _ in range(count):
        when = when - timedelta(days=rng.randint(*DAY_STEP))
        merchant = rng.choice(pool)
        amount = draw_amount(rng, merchant.credit, max_amount)
        rows.append(
            {
                "when": when,
                "description": render_description(rng, merchant, when, holder),
                "type": merchant.type,
                "amount": amount,
                "balance": balance,
            }
        )
        # The next row back is the balance *before* this posting.
        balance = round(balance - amount, 2)
    return rows


# ---------------------------------------------------------------------------
# Fanning out across the content variants


def localize(
    rows: list[dict], variant: Variant, date_format: Optional[str] = None
) -> list[dict]:
    """``rows`` as ``variant`` writes them.

    The figures are copied, never recomputed. Two variants that disagree on an
    amount or a balance would make the same task need a different answer per
    language, which is the whole thing this exists to prevent -- so only the
    date and the type, both closed vocabularies, are re-expressed.
    """
    render = variant.render_date
    if date_format is not None:
        render = lambda when: when.strftime(date_format)  # noqa: E731
    return [
        {
            "date": render(row["when"]),
            "description": row["description"],
            "type": variant.types.get(row["type"], row["type"]),
            "amount": row["amount"],
            "balance": row["balance"],
        }
        for row in rows
    ]


def fanout_targets(source: Path, account_name: str) -> list[tuple[Variant, Path]]:
    """Every *other* content file that carries its own copy of this account.

    Owning is not inheriting: the noise variants append with ``+accounts`` and
    the accounts they add are theirs alone, so they never match a seeded name
    and are correctly skipped. A file that does own the account but has no
    entry in ``VARIANTS`` is an error rather than a guess -- writing English
    dates and types into a translated ledger is exactly the silent damage this
    is meant to stop.
    """
    targets = []
    for path in sorted(CONTENT_DIR.glob("*.yaml")):
        if path.resolve() == source.resolve():
            continue
        if account_name not in {str(a.get("name")) for a in load_accounts(path)}:
            continue
        variant = VARIANTS.get(path.stem)
        if variant is None:
            raise SystemExit(
                f"{path.name} carries its own copy of {account_name!r}, but this "
                "generator has no date format or type table for it. Add one to "
                "`VARIANTS`, or pass `--variants source` and edit that file by hand."
            )
        targets.append((variant, path))
    return targets


def check_parity(source: dict, target: dict, path: Path) -> None:
    """Refuse to fan out onto a ledger that has already diverged.

    Appending identical figures only keeps the variants in step if they were in
    step to begin with. If they are not, the right fix is to reconcile them by
    hand first -- splicing on top would bury the existing disagreement under a
    row that looks correct.
    """
    source_txns = list(source.get("transactions") or [])
    target_txns = list(target.get("transactions") or [])
    name = source.get("name")
    if len(source_txns) != len(target_txns):
        raise SystemExit(
            f"{path.name} and the source disagree on {name!r} before any write: "
            f"{len(target_txns)} transactions there, {len(source_txns)} here. "
            "Reconcile them first (`pytest tests/test_openbanking.py -k figures`)."
        )
    for i, (here, there) in enumerate(zip(source_txns, target_txns)):
        if here.get("amount") != there.get("amount") or here.get(
            "balance"
        ) != there.get("balance"):
            raise SystemExit(
                f"{path.name} and the source disagree on row {i + 1} of {name!r} "
                f"before any write: {there.get('amount')}/{there.get('balance')} "
                f"there, {here.get('amount')}/{here.get('balance')} here. "
                "Reconcile them first."
            )


def translation_note(indent: int) -> str:
    """The comment that marks a generated block in a translating variant.

    Keyed off the *target* file rather than off whether it was fanned out to:
    the merchant pool is English wording, so a row generated straight into
    ``german.yaml`` needs the same flag as one copied there.
    """
    pad = " " * indent
    return (
        f"{pad}# Generated rows. The figures are shared with the other content\n"
        f"{pad}# variants and have to stay identical; the dates and types are this\n"
        f"{pad}# file's own. The descriptions are still English -- translate them.\n"
    )


def splice_all(plan: list[tuple[Path, str, str, int]]) -> None:
    """Apply every splice in ``plan``, or none of them.

    ``splice`` already restores the one file it was editing, but a fan-out
    writes several: a failure on the third would otherwise leave the first two
    written and the variants out of step -- precisely the state this exists to
    prevent.
    """
    originals = {path: path.read_text(encoding="utf-8") for path, *_ in plan}
    try:
        for path, account_name, block, added in plan:
            splice(path, account_name, block, added)
    except Exception:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
        raise


# ---------------------------------------------------------------------------
# Rendering and writing


def render_yaml(rows: list[dict], indent: int) -> str:
    """The rows in the content files' own hand-written style.

    Not ``yaml.dump``: that would reflow the amounts (``-64.2``), pick its own
    quoting and lose the two-decimal money convention every other row in these
    files follows.
    """
    pad = " " * indent
    out = []
    for row in rows:
        description = row["description"].replace('"', '\\"')
        out.append(f'{pad}- date: "{row["date"]}"')
        out.append(f'{pad}  description: "{description}"')
        out.append(f'{pad}  type: "{row["type"]}"')
        out.append(f"{pad}  amount: {row['amount']:.2f}")
        out.append(f"{pad}  balance: {row['balance']:.2f}")
    return "\n".join(out) + "\n"


def splice(path: Path, account_name: str, block: str, added: int) -> None:
    """Insert ``block`` at the end of ``account_name``'s ``transactions`` list.

    Text insertion rather than a YAML round-trip: these files are heavily
    commented and every comment explains a decision, and ``yaml.dump`` would
    drop all of them. The insertion point is the last line that is indented
    *inside* the list, so trailing comments and blank lines that belong to the
    next account stay where they are.

    The result is re-parsed before it is kept; on any mismatch the original
    text is restored, so a failed splice cannot leave a half-edited config.
    """
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    start = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip().startswith("- name:") and account_name in line
        ),
        None,
    )
    if start is None:
        raise SystemExit(f"Could not find `- name:` line for {account_name!r}.")

    txn_line = next(
        (i for i in range(start, len(lines)) if lines[i].strip() == "transactions:"),
        None,
    )
    if txn_line is None:
        raise SystemExit(f"{account_name!r} has no `transactions:` key to append to.")

    txn_indent = len(lines[txn_line]) - len(lines[txn_line].lstrip())
    end = txn_line
    for i in range(txn_line + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= txn_indent:
            break
        end = i

    before = find_account(load_accounts(path), account_name)
    expected = len(before.get("transactions") or []) + added

    path.write_text(
        "".join(lines[: end + 1]) + block + "".join(lines[end + 1 :]),
        encoding="utf-8",
    )
    try:
        after = find_account(load_accounts(path), account_name)
        actual = len(after.get("transactions") or [])
        if actual != expected:
            raise ValueError(
                f"expected {expected} transactions after write, got {actual}"
            )
        check_chain(after)
    except Exception:
        path.write_text(original, encoding="utf-8")
        raise


def check_chain(account: dict) -> None:
    """Assert the ledger's running balance still reconciles row to row."""
    txns = list(account.get("transactions") or [])
    for i in range(len(txns) - 1):
        current, following = txns[i], txns[i + 1]
        if current.get("balance") is None or following.get("balance") is None:
            continue
        expected = round(float(current["balance"]) - float(current["amount"]), 2)
        if abs(expected - float(following["balance"])) > 0.005:
            raise ValueError(
                f"balance does not chain at row {i + 1} of {account.get('name')!r}: "
                f"expected {expected:.2f}, found {float(following['balance']):.2f}"
            )


# ---------------------------------------------------------------------------
# CLI


def main() -> None:
    p = argparse.ArgumentParser(
        prog="openbanking-gen-txns",
        description=(
            "Generate extra OpenBanking ledger rows for one account, chained "
            "off its oldest posting so the running balance still reconciles."
        ),
    )
    p.add_argument(
        "--account",
        required=True,
        help="Account name, or just its last four digits (e.g. '5555').",
    )
    p.add_argument(
        "--content",
        default="default",
        help="Content variant the rows are generated *against*: a stem under "
        "config/apps/openbanking/content, or a path to a yaml file. Defaults to "
        "`default`. See --variants for which files get written.",
    )
    p.add_argument(
        "--variants",
        default="all",
        help="Which content files to write. `all` (the default) writes the "
        "--content file and every other variant carrying its own copy of the "
        "account, so their figures stay in step; `source` writes only the "
        "--content file; or give a comma-separated list of stems.",
    )
    p.add_argument("--count", type=int, default=3, help="How many rows to generate.")
    p.add_argument(
        "--seed",
        type=int,
        default=0,
        help="RNG seed. The same seed and arguments always produce the same rows.",
    )
    p.add_argument(
        "--max-amount",
        type=float,
        default=1500.0,
        help="Largest magnitude any generated amount may take. Keep this under "
        "any figure a task reads off this account.",
    )
    p.add_argument(
        "--types",
        nargs="*",
        help="Restrict to these transaction types (e.g. --types Fee Interest). "
        "Use it to avoid a type a task quantifies over.",
    )
    p.add_argument(
        "--date-format",
        help="strftime format for the dates written into the --content file. "
        "Rarely needed: each variant already knows its own format, and the "
        "fanned-out files always use theirs.",
    )
    p.add_argument(
        "--start-date",
        help="ISO date to count backwards from. Defaults to the account's "
        "oldest existing posting.",
    )
    p.add_argument(
        "--in-place",
        action="store_true",
        help="Append the rows to the account in the content files instead of "
        "printing them. Comments and formatting are preserved.",
    )
    args = p.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be at least 1.")

    path = content_path(args.content)
    if not path.exists():
        raise SystemExit(f"No such content file: {path}")

    accounts = load_accounts(path)
    if not accounts:
        raise SystemExit(
            f"{path.name} declares no accounts of its own -- it inherits them from "
            f"default.yaml. Generate against `--content default` instead."
        )
    account = find_account(accounts, args.account)
    check_chain(account)
    name = str(account["name"])

    start = date.fromisoformat(args.start_date) if args.start_date else None
    rows = generate(
        account,
        count=args.count,
        seed=args.seed,
        max_amount=args.max_amount,
        types=args.types,
        start=start,
    )

    # An unregistered stem is an English variant (`long_descriptions` and the
    # rest of the noise files are); only the translated ones need a table, and
    # they have one.
    source_variant = VARIANTS.get(path.stem, VARIANTS["default"])
    targets = resolve_targets(args.variants, path, name)

    # 6 spaces: `accounts:` items sit at 2, their keys at 4, list entries at 6.
    indent = 6

    def block_for(variant: Variant, date_format: Optional[str] = None) -> str:
        # A variant with a type table is one that translates, so its
        # descriptions -- which the English merchant pool supplied -- are the
        # one part of the row a human still has to finish.
        note = translation_note(indent) if variant.types else ""
        return note + render_yaml(localize(rows, variant, date_format), indent)

    plan = [(path, name, block_for(source_variant, args.date_format), len(rows))]
    for variant, target_path in targets:
        check_parity(
            account, find_account(load_accounts(target_path), name), target_path
        )
        plan.append((target_path, name, block_for(variant), len(rows)))

    if args.in_place:
        splice_all(plan)
        written = "\n  ".join(str(entry[0]) for entry in plan)
        print(
            f"Appended {len(rows)} transaction(s) to {name!r} in:\n  {written}\n"
            "Re-run `pytest tests/test_openbanking.py` -- the content variants "
            "must agree on every amount and balance.",
            file=sys.stderr,
        )
        if any("# Generated rows." in entry[2] for entry in plan):
            print(
                "Descriptions in the translated files are still English; each "
                "block is marked with a comment.",
                file=sys.stderr,
            )
    else:
        for target_path, _, text, _ in plan:
            if len(plan) > 1:
                print(f"# --- {target_path.name} " + "-" * 40)
            print(text, end="")


def resolve_targets(
    selection: str, source: Path, account_name: str
) -> list[tuple[Variant, Path]]:
    """``--variants`` as the list of other files to write.

    Fanning out is the default because the invariant is a cross-file one: the
    variants restate the seeded accounts in full, and a figure added to one of
    them alone makes the same task need a different answer per language.
    """
    choice = selection.strip().casefold()
    if choice == "source":
        return []
    available = fanout_targets(source, account_name)
    if choice == "all":
        return available
    wanted = [stem.strip() for stem in selection.split(",") if stem.strip()]
    by_stem = {
        target_path.stem: (variant, target_path) for variant, target_path in available
    }
    missing = [stem for stem in wanted if stem not in by_stem]
    if missing:
        raise SystemExit(
            f"--variants names {', '.join(missing)}, which do not carry their own "
            f"copy of {account_name!r}. Files that do: "
            f"{', '.join(sorted(by_stem)) or '(none)'}."
        )
    return [by_stem[stem] for stem in wanted]


if __name__ == "__main__":
    main()
