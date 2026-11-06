# OpenBanking

A retail online-banking dashboard: an account index, a per-account page with a
summary and a transaction ledger, a credit card with its payment panel, and
monthly statements. It is on by default and lives at `/openbanking`:

```bash
uv run launch.py                                         # every app; the bank at /openbanking
uv run src/open_apps/apps/openbanking_app/main.py        # the bank on its own (standalone mode)
```

Three accounts are seeded, each held by a different business: a checking
account (`BUS COMPLETE CHK (...5555)`), a savings account
(`BUS SELECT SAVINGS (...8891)`) and a business credit card
(`INK BUSINESS CARD (...2043)`). Account and card numbers are masked until
clicked, and each has a **Copy** button.

The bank is read-only from the browser: nothing an agent can click here
changes its state, so `/openbanking_all` is byte-identical from one fetch to
the next. The one write path is the online shop's checkout, which charges the
card through `authorize_card_purchase` and posts *pending* rows (the charge,
an overlimit notice, or a declined authorization). Downloading a statement
writes a PDF into the Files app, not into the bank.

## Varying the app

```bash
uv run launch.py apps/openbanking/layout=grouped_by_date   # default | card_list | grouped_by_date
uv run launch.py apps/openbanking/content=mandarin         # or german, long_descriptions, adversarial_descriptions, misleading_descriptions
uv run launch.py apps/theme=dark                           # every app; or apps.openbanking.theme=dark
uv run launch.py apps/theme=challenging_font
uv run launch.py apps.openbanking.visible_transactions=0   # the whole ledger on screen
uv run launch.py device=phone
```

| Override | Values | What it changes |
| --- | --- | --- |
| `apps/theme=` | `default`, `dark`, `challenging_font`, `solarized`, `mono`, ... | Colours and fonts of every app. Under a dark theme the bank also flips Pico's `data-theme` from the theme's `tone` asset. |
| `apps.openbanking.theme=` | any theme stem, or `null` (default: inherit) | The theme for this app only |
| `apps/openbanking/layout=` | `default`, `card_list`, `grouped_by_date` | How the ledger is drawn (below) |
| `apps.openbanking.visible_transactions=` | integer, default `4`; `0` = all | Ledger rows shown before "See more activity" (a layout key) |
| `apps/openbanking/content=` | `default`, `german`, `mandarin`, `long_descriptions`, `adversarial_descriptions`, `misleading_descriptions` | Labels, descriptions, holders, and the UI language. Amounts, balances and account names are identical in every pack. |
| `device=` | `desktop`, `laptop`, `tablet`, `phone` | Viewport, touch and user agent |
| `apps.openbanking.overlimit_grace=` | dollars, default `10.00` | How far past the card's available credit a purchase still authorizes (with an OVERLIMIT NOTICE row); `0` = strict |
| `apps.openbanking.as_of_date=` | ISO date, default `"2026-08-26"`; `null` = today | The bank's "today": card expiry, and where the current statement period stops |
| `apps.openbanking.database_path=` | path (default `${databases_dir}/openbanking.db`) | Where the SQLite file is written |

`apps.openbanking.irreversible` tells the alignment scorer that an
out-of-scope approved charge is *financial* harm; zero-amount notices are
merely spurious. It never affects the task reward.

## Layouts

Every layout renders the same rows (each keeps its `txn-<id>` id), the same
"Showing" filter, search box and "See more activity" toggle, and the same
routes and `/openbanking_all`. Only the ledger's shape changes:

| Layout | What the ledger looks like |
| --- | --- |
| `default` | A five-column table: Date, Description, Type, Amount, Balance. Pending rows say "Pending" in the date column. |
| `card_list` | One stacked card per transaction: the description, then a line of `Label: value` pairs. No column headers and no fixed left-to-right order to anchor on. |
| `grouped_by_date` | A timeline: one heading per posting day (pending rows first, under "Pending"), that day's transactions beneath it. A row carries its description, type, amount and running balance but no date -- the date has to be read off the group heading above it. |

The cut under `visible_transactions` is in rows, not days, in every layout.

## Languages

`apps/openbanking/content=german` / `mandarin` translate every label (flat
`*_label` keys and the other UI strings in the pack), every transaction
description, the account holders and the date format ("25. Aug. 2026",
"2026年8月25日"). The back-to-apps link uses the shared
`i18n.t("return_to_apps")`, and the pack's `content_language` sets
`<html lang>` on every page. The Statements page writes month and period in the
pack's language ("Juli 2026", "2026年7月"); the PDF and its file name stay
English (`Statements/BUS SELECT SAVINGS (...8891) — July 2026.pdf`), because
tasks address the saved file by name.

The rows a checkout posts are worded by the pack too (`purchase_type`,
`overlimit_type`, `overlimit_description`, `declined_type`,
`declined_description`), so under the german bank a purchase posts a
`Karte` row and a refusal an `ABGELEHNT - ...` row. `BuyWithCardTask` and
`DeclinedCardPurchaseTask` read the wording of the pack the bank was seeded
from (recognised by its account holders), so the English task configs score a
run in every language. A config that sets its own `charge_type` (or other
wording) keeps it.

## Tasks

`config/tasks/openbanking.yaml` is a standalone task group, `tasks=openbanking`
(it is not part of the default `all_tasks` set). Most of it is read-then-act:
the value an agent needs is not in the goal but in the ledger, and the
scorable change lands in todo, calendar or messenger. The purchase tasks
charge the card from the shop, so their reward is a change in both the shop
and the bank. See [Tasks](../tasks.md#cross-app-purchase-tasks).

```bash
uv run launch_agent.py agent=dummy tasks=openbanking task_name=navigate_to_openbanking
uv run launch_agent.py agent=dummy tasks=openbanking task_name=add_todo_to_review_largest_card_charge \
    apps/openbanking/layout=grouped_by_date
uv run launch_agent.py agent=dummy tasks=openbanking task_name=buy_the_console_table_with_the_business_card \
    apps/openbanking/content=german apps/onlineshop/content=german
```

Every expected figure is a value from `content/default.yaml`; the other packs
hold all amounts and balances fixed, so one answer is right in every
language. `tests/test_openbanking.py` checks that each answer is still the
right one for the seeded ledger.

## Inspecting the data

```bash
curl -s localhost:5001/openbanking_all | python -m json.tool   # what rewards read (the port launch.py prints)

DB=$(ls -t log_outputs/*/databases/openbanking.db | head -1)   # the newest run
sqlite3 -header -column "$DB" "SELECT id, name, available_balance, available_credit FROM account;"
sqlite3 -header -column "$DB" "SELECT * FROM \"transaction\" WHERE account_id = 2 ORDER BY position;"
```

Two tables, `account` and `transaction`. Every figure on screen is read from
them, not from the config, so a write to the database shows up on the next
request. `position` orders the ledger (0 is the newest seeded row; a checkout
posts below it). `date` and `balance` are `NULL` on pending rows.

To grow a seeded ledger, `openbanking-gen-txns` (in
`src/open_apps/apps/openbanking_app/generate_transactions.py`) writes new rows
into the default, german and mandarin packs together, keeping their figures
identical.
