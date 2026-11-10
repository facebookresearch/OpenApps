# Messenger

OpenMessages is a chat client: a list of conversations, a thread view with a
composer, in-thread search, and an auto-reply to every message you send.
Threads are keyed by the other party's handle (a phone number or email, or
`group:<slug>` for a group chat); names are never stored here but drawn from
the Contacts app at render time, so saving or renaming a contact renames its
thread without touching the messenger's state.

```bash
uv run launch.py                 # then open /messages on the URL it prints
./scripts/dev.sh                 # hot-reloading dev server, http://localhost:5001/messages
OPENAPPS_DEV_OVERRIDES="apps/theme=dark apps/messenger/content=mandarin" ./scripts/dev.sh
```

Routes: `/messages` (the conversation list), `/messages/<handle>/` (a thread,
e.g. `/messages/+15550100001/`; old name-keyed URLs such as
`/messages/Alice/` redirect when the name is unambiguous), `POST
/messages/send`, and `/messages_all`, the JSON every reward reads.

## Varying the messenger

```bash
uv run launch.py apps/theme=dark                          # shared theme, every app
uv run launch.py apps/theme=challenging_font              # same palette, script typeface
uv run launch.py apps.messenger.theme=solarized           # this app only
uv run launch.py apps/messenger/layout=split_inbox        # default | split_inbox | compact_list
uv run launch.py apps/messenger/content=german            # default | german | mandarin | long_descriptions
                                                          # | misleading_descriptions | adversarial_descriptions
uv run launch.py device=phone                             # desktop | laptop | tablet | phone
```

| Override | Default | Effect |
| --- | --- | --- |
| `apps/theme=<name>` | `default` | Colours, fonts and radii for every app (`config/apps/theme/`). |
| `apps.messenger.theme=<name>` | `null` | The same, for the messenger alone. |
| `apps/messenger/layout=<name>` | `default` | Page structure, see below. |
| `apps/messenger/content=<name>` | `default` | Conversations, auto-replies and UI language. |
| `apps.messenger.database_path` | `${databases_dir}/messenger.db` | Where the thread table lives. |

### Themes

The page stacks three stylesheets: Pico, daisyUI and Tailwind (the latter two
from a CDN, so they are absent on an offline node and the app's own rules lay
the page out alone). Every colour the app draws is a theme token. Both Pico
and daisyUI paint the root element from their own palettes and follow the
OS's dark-mode preference, so the messenger paints `html:root` from
`--color-bg`, routes daisyUI's base and brand colours through their
`--fallback-*` variables to the tokens, bridges Pico's variables the same way,
and sets `<html data-theme>` from the theme's tone. Under `dark` the whole
window is dark, not just the phone-width column; under `challenging_font`
only the typeface changes (it needs `Brush Script MT` installed, otherwise the
browser's `cursive` fallback is used).

### Layouts

All three keep `/messages` and `/messages/<handle>/` resolving, the rows as
`main a[href^='/messages/']`, the thread as `#chatlist`, and the composer's
`msg` / `interlocutor` fields, so tasks and rewards are unaffected.

- **`default`**: a phone-width column. The list page is the conversation
  list with a full-width outlined "Return to List of Apps" beneath it; a
  thread is its own page with a back arrow, search, Messenger-style bubbles
  (yours on the right in the primary colour) and a pill composer.
- **`split_inbox`**: a desktop client. One framed window with the list in a
  fixed left column ("Chats") and the thread or an empty "Select a
  conversation" pane on the right; the open chat is marked in the list
  (`aria-current`), the back arrow goes, and "Return to List of Apps" becomes
  a quiet link at the foot of the list.
- **`compact_list`**: Slack's compact mode. No avatars, one line per chat
  (name, preview, date), and a thread of flat lines with sender and time on
  the first line of each run instead of bubbles; the composer is a box.

### Languages

`german` and `mandarin` translate the conversations and the interface: the
composer and search placeholders and their result counts, the "You:" preview
prefix, "Chats", "Select a conversation...", "Not in your contacts" / "Add to
contacts", the accessible names of the back, search, clear and send buttons,
and the auto-replies. Timestamps are displayed in the pack's format
(`time_format`, `date_format`, `month_abbr`, `meridiem_labels`: "17. Sept.,
09:32", "9月17日 上午9:32") while the stored value stays "Sep 17, 09:32AM" in
every pack. The page's `<html lang>` follows (`de`, `zh-Hans`), and the shared
strings ("Return to List of Apps", "Search") come from `open_apps/i18n.py`.

Contact names and handles are not translated: the tasks address people by
them. Auto-replies may be translated freely because a task never scores one;
`SendMessageTask` ignores the single reply the app appends to each message it
sends.

The UI strings are flat keys in each pack, English in
`config/apps/messenger/content/default.yaml`; a pack that leaves one out falls
back to English.

## Tasks

From `config/tasks/original_tasks.yaml` (with phrasing variants in
`user_goal_variations.yaml`, e.g. `message_alice_about_badminton__casual_1`):

| Task | Kind |
| --- | --- |
| `message_alice_about_badminton` | `SendMessageTask` |
| `message_bob_to_meet` | `SendMessageTask` |
| `navigate_to_messenger`, `navigate_from_calendar_to_messages`, `navigate_from_messages_to_calendar` | `NavigateToAppTask` |

`contacts.yaml` adds the unknown-sender tasks (`reply_to_unknown_number`,
`save_unknown_sender_then_message`), `openbanking.yaml` has
`message_bob_the_savings_balance`, and messages are steps in many
`longer_horizon.yaml` composites (`book_club_event_and_message_alice`, ...).

```bash
uv run launch_agent.py agent=dummy task_name=message_alice_about_badminton
uv run launch_agent.py agent=dummy task_name=message_bob_to_meet apps/messenger/layout=split_inbox apps/messenger/content=german
uv run launch_agent.py agent=dummy tasks=contacts task_name=reply_to_unknown_number
```

`SendMessageTask` resolves the recipient to a thread handle, then compares the
whole messenger state against the initial one plus your message (text
compared fuzzily, timestamps stripped), so a message to the wrong person fails
it.

## Inspecting the data

The seed is in the content pack: `groups:` (`group:<slug>` -> `title`,
`members`) and `threads:` (handle -> list of `[message, sender, timestamp]`,
where `sender` is `"you"` or a handle). `long_descriptions` appends threads
with `+threads:`. Names for the handles come from
`config/apps/contacts/content/`.

While the server runs, `/messages_all` returns every thread as
`{"handle": ..., "messages": [[text, sender, timestamp], ...]}`. The SQLite
file is `messenger.db` in the run's databases directory (see
[Inspecting databases](../inspecting_databases.md)); the lists are stored as
Python literals:

```bash
DB=$(ls -td log_outputs/*/databases | head -1)/messenger.db
sqlite3 "$DB" "SELECT handle, messages FROM threads"
```
