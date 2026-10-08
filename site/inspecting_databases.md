# Inspecting the app databases

Every app keeps its state in a SQLite file. This page is about finding those
files, reading them, and pointing a client at them without fighting the running
app.

## Usually you want the grading state, not the database

If the question is "what will the task grader see?", skip SQLite entirely.
`open_apps.state.get_current_state(url)` fetches every app's `/<app>_all`
endpoint from a running server and returns one dict keyed by app name -- the
exact snapshot tasks diff against to score a run:

```python
from open_apps.state import get_current_state

state = get_current_state("http://localhost:5001")  # the port the launcher printed
state["openbanking"]["accounts"]
```

Or one app at a time: `curl -s localhost:5001/openbanking_all | jq`. Reach for
the database itself only when you need to edit rows, query across tables, or
look at a run after the server has stopped.

## Two layers: the config seeds, the database serves

It is worth being precise about this, because the two disagree the moment
anything writes to the database:

- **Hydra config is the seed.** `config/apps/openbanking/content/*.yaml` holds
  the accounts and ledger rows. It is read exactly once, by `set_environment`,
  which inserts it into the tables.
- **SQLite is what the app serves.** Every page render, and the
  `/openbanking_all` reward state, reads out of the `account` and `transaction`
  tables. Nothing is cached between requests.

So a balance shown on screen is the database's answer, not the config's. Edit a
row in the database and the next page load reflects it; edit the config while
the app is running and nothing happens until the app re-seeds.

`TestBalancesComeFromSqlite` in `tests/test_openbanking.py` pins this down — it
writes to the file through a separate connection and asserts the change appears
in the next request.

To change the *seed* rather than the live state, edit the content YAML (or use
`openbanking-gen-txns`, which appends ledger rows to the content files) and
restart, or re-seed in place — see [Resetting](#resetting) below.

## Where the files are

`config/mode/local.yaml` puts each run in its own timestamped directory:

```yaml
logs_dir: ${hydra:runtime.cwd}/log_outputs/${now:%Y-%m-%d_%H-%M-%S}-${oc.env:USER}
databases_dir: ${logs_dir}/databases
```

So a run's databases land in
`log_outputs/<timestamp>-<user>/databases/`. The launcher prints the directory
on startup (`logging to: ...`), and the resolved config is written to
`log_outputs/<timestamp>-<user>/config.yaml` if you want to read the path back
out.

Nothing is shared between runs — each launch gets a fresh directory and re-seeds
from scratch. To grab the most recent one:

```shell
DB=$(ls -td log_outputs/*/databases | head -1)/openbanking.db
```

| App | File |
| --- | --- |
| OpenBanking | `openbanking.db` |
| Calendar | `calendar.db` |
| Messenger | `messenger.db` |
| Todo | `todo.db` |
| Maps | `maps.db` |
| Code editor | `codeeditor/` (a directory, not a database) |
| Onlineshop | `onlineshop/` (a directory; only present when onlineshop is enabled) |

Each `.db` sits next to a `-wal` and a `-shm` file while the app is running.
That matters if you copy a database somewhere to poke at it: copy all three, or
you get a snapshot missing every write still sitting in the write-ahead log.
Copying just the `.db` of a *stopped* app is fine — the WAL is checkpointed back
into it on a clean shutdown.

## The OpenBanking schema

```sql
CREATE TABLE [account] (
   [id] INTEGER PRIMARY KEY,
   [name] TEXT,
   [holder] TEXT,
   [kind] TEXT,                  -- 'deposit' | 'credit_card'
   [account_number] TEXT,
   [routing_number] TEXT,
   [available_balance] FLOAT,
   [present_balance] FLOAT,
   [available_credit] FLOAT,
   [credit_limit] FLOAT,
   [card_brand] TEXT,
   [card_expiration] TEXT,
   [statement_balance] FLOAT,
   [statement_close_date] TEXT,
   [minimum_payment] FLOAT,
   [payment_due_date] TEXT
);

CREATE TABLE [transaction] (
   [id] INTEGER PRIMARY KEY,
   [account_id] INTEGER,
   [position] INTEGER,           -- ledger order; 0 is newest
   [date] TEXT,                  -- display string, format varies by content variant
   [description] TEXT,
   [type] TEXT,
   [amount] FLOAT,               -- signed: negative is money out
   [balance] FLOAT               -- NULL means the posting is still pending
);
```

Two things that surprise people:

- A credit card's `available_balance` is `0.00` by design. The figures that mean
  something on a card are `present_balance`, `statement_balance`, and
  `available_credit` — which is why the card summary renders a different set of
  fields from a deposit account.
- `transaction` is a SQL keyword, so it has to be quoted in every query:
  `SELECT ... FROM "transaction"`.

## Reading it from the command line

`sqlite3` ships with macOS and most Linux distributions. Use `-readonly` — you
do not want to take a write lock on a database an app is serving from.

```shell
DB=$(ls -td log_outputs/*/databases | head -1)/openbanking.db

sqlite3 -readonly "$DB" ".tables"
sqlite3 -readonly "$DB" ".schema account"
```

Balances across every account:

```shell
sqlite3 -readonly -box "$DB" \
  "SELECT id, name, kind, available_balance, present_balance, statement_balance
   FROM account ORDER BY id;"
```

```
┌────┬──────────────────────────────┬─────────────┬───────────────────┬─────────────────┬───────────────────┐
│ id │             name             │    kind     │ available_balance │ present_balance │ statement_balance │
├────┼──────────────────────────────┼─────────────┼───────────────────┼─────────────────┼───────────────────┤
│ 0  │ BUS COMPLETE CHK (...5555)   │ deposit     │ 6102.8            │ 6102.8          │                   │
│ 1  │ BUS SELECT SAVINGS (...8891) │ deposit     │ 24310.55          │ 24310.55        │                   │
│ 2  │ INK BUSINESS CARD (...2043)  │ credit_card │ 0.0               │ -1284.19        │ 872.19            │
└────┴──────────────────────────────┴─────────────┴───────────────────┴─────────────────┴───────────────────┘
```

One account's ledger, in the order the page shows it:

```shell
sqlite3 -readonly -box "$DB" \
  'SELECT position, date, description, type, amount, balance
   FROM "transaction" WHERE account_id = 0 ORDER BY position LIMIT 10;'
```

## Connecting a client

Any SQLite client works — the file is a plain database with no extensions.

**Point it at the run directory, not the repo root.** The path is
`log_outputs/<timestamp>-<user>/databases/openbanking.db`, and it changes every
launch, so a saved connection goes stale as soon as you restart. If you are
iterating, symlink a stable path once and re-point it:

```shell
ln -sfn "$(ls -td log_outputs/*/databases | head -1)" ./current-dbs
```

then connect to `./current-dbs/openbanking.db`.

**Open read-only if the client offers it.** The app holds the database open in
WAL mode. Reads never conflict, but a client that starts an interactive write
transaction and then sits on it will block the app's writes.

Any GUI that takes a file path works (DB Browser for SQLite, TablePlus,
DBeaver, the VS Code SQLite extensions); tick its read-only option. For
browsing in a browser without writing SQL, `uvx datasette <path>` runs Datasette
without adding a dependency -- pick a port other than the app's.

### Do not use Python's `sqlite3` module in the app's process

This one costs an afternoon if you hit it cold. fastlite talks to SQLite through
`apsw`, which links its **own** copy of the SQLite library; the stdlib `sqlite3`
module links the **system** one. Two different SQLite builds in one process, both
holding the same WAL database open, do not share the WAL index coherently — the
apsw side keeps serving a stale snapshot and gives no error. Symptom: your first
write shows up on the page, the second silently does not.

It only bites *in-process*. From a separate process — the `sqlite3` CLI, a GUI
client, a standalone script — `sqlite3` is completely fine, and every write is
picked up on the next request.

Inside the app's process (a test, a debugger session, an MCP handler), use
`apsw` instead, and drain every cursor, because apsw cursors are lazy:

```python
import apsw

con = apsw.Connection(app.config.openbanking.database_path)
rows = list(con.execute("SELECT id, available_balance FROM account"))
list(con.execute("UPDATE account SET available_balance = 12345.67 WHERE id = 0"))
con.close()
```

Or just go through fastlite's own tables, which is what the app does:

```python
from open_apps.apps.openbanking_app import main as ob

ob.accounts()          # every account row
ob.txns_for(0)         # one account's ledger, in display order
```

## Resetting

To put every app back to its configured initial state without restarting, call
`open_apps.apps.start_page.main.reset_all_apps(config.apps)`, or
`AppServer.reset()` if you are driving the environment over MCP. It drops each
app's tables and re-runs `set_environment`, so anything you edited by hand goes
away and the config seed comes back. There is no HTTP route for this.
