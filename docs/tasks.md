
To ask GPT-4o to mark water plants as done in your todo list:

```shell
# export OPENAI_API_KEY=""
uv run launch_agent.py agent=GPT-5-1 task_name=mark_water_plants_as_done
```

`task_name` specifies the task. Tasks are defined in `config/tasks/all_tasks.yaml`. For example,

```yaml
mark_water_plants_as_done:
  # Indicates class where reward logic is defined
  _target_: open_apps.tasks.tasks.MarkToDoDoneTask
  goal: Mark 'Water plants' as done in my todo list.
  todo_name: "Water plants"
```

## Adding New Tasks

To add a new task using an existing reward function, simply add a new entry to the `config/tasks/all_tasks.yaml`:

```yaml
add_my_special_item_to_todo:
  # _target_ defines the class containing the task reward logic
  _target_: open_apps.tasks.tasks.AddToDoTask
  goal: ENTER YOUR GOAL
  todo_name: ENTER TITLE of TODO
  is_done: false
```

You can select this new task by specifying the `task_name=add_my_special_item_todo`.


### New custom tasks

To add a custom task with its own reward logic, create a new class in `src/open_apps/tasks/tasks.py`.

Your new class should inherit `Task` and implement a reward function, `check_if_task_is_complete`, indicating whether the task is complete:


```python
@dataclass
class MyCustomTask(Task):
	def check_if_task_is_complete(
		self, 
		initiate_state: dict, 
		current_state: dict) -> bool:
		# we handle providing the initial and current states for you!
		# write your custom reward logic
		...
```

Then create a corresponding entry in `config/tasks/all_tasks.yaml`:

```yaml
my_custom_task:
	_target_: open_apps.tasks.tasks.MyCustomTask
	goal: ENTER
```

Finally, ask your agent to solve the task by specifying `task_name=my_custom_task`.

### Example: editing a file in the Code Editor

`EditFileTask` makes the Code Editor a first-class task app. Unlike the other
apps, the Code Editor state is excluded from the generic state comparison, so
`EditFileTask` implements its own reward: it walks the current file tree (from
the `/codeeditor_all` endpoint), finds the file at `file_path`, and checks its
saved contents. It also compares against the **initial** state so an unsaved or
unchanged file earns no reward.

Provide either `required_fragment` (a substring that must appear in the file) or
`expected_content` (a whitespace-normalized full-file match):

```yaml
edit_script_add_header_comment:
  _target_: open_apps.tasks.tasks.EditFileTask
  goal: Open 'script.py' in the Code Editor, add the line '# Reviewed by Bob' as a
    comment, and save the file.
  file_path: script.py
  required_fragment: "# Reviewed by Bob"
```

The same class also covers creating a new file — if the file did not exist in
the initial state, any matching saved content counts as a change:

```yaml
create_notes_file_in_code_editor:
  _target_: open_apps.tasks.tasks.EditFileTask
  goal: "In the Code Editor, create a new file named 'notes.txt' containing the text
    'TODO: refactor' and save it."
  file_path: notes.txt
  expected_content: "TODO: refactor"
```

Run it like any other task:

```shell
uv run launch_agent.py agent=gemma-4-2B-computer-use task_name=edit_script_add_header_comment
```

## Act, Clarify, or Stop

`AbstentionTask` scores whether the agent acts, asks a clarifying question, or
says the request can't be done. Set `expected_outcome` to `act` (checked by a
nested `action_task`), `clarify`, or `stop`; on `clarify`/`stop` tasks any app
change the state check detects (not the code editor) counts as a wrong act. The agent's first act, question
(`send_msg_to_user`) or infeasibility report (`report_infeasible`) ends the
episode, with reward 1 only if it matches. Every agent config offers those
two actions (see [Ending an episode](agents.md#ending-an-episode));
`config/tasks/abstention_examples.yaml` has one example of each outcome:

```shell
uv run launch_agent.py agent=GPT-5.5-computer-use \
    tasks=abstention_examples task_name=abstention_clarify_paper_deadline
```

## Goal Variations

Tasks come with **goal variations**: the same task with its goal reworded in a
different style, so you can study how robust an agent is to phrasing. There are
three styles — `casual`, `formal`, and `unrelated_context` (the instruction
embedded in unrelated chit-chat) — with 9 variations per task.

Tasks are split across two files, both composed into `all_tasks.yaml`:

* `config/tasks/original_tasks.yaml` — the base tasks.
* `config/tasks/user_goal_variations.yaml` — the variations, keyed
  `<original_task>__<style>_<n>` (e.g. `mark_water_plants_as_done__casual_2`).

Every variation copies its original's fields verbatim — only the `goal` is
reworded, and an optional `goal_style` field records the style — so the reward
logic is identical to the base task:

```yaml
mark_water_plants_as_done__casual_2:
  _target_: open_apps.tasks.tasks.MarkToDoDoneTask
  goal: can you check off 'Water plants' in my to-do list?
  todo_name: "Water plants"
  goal_style: casual
```

Run a single variation like any other task:

```shell
uv run launch_agent.py agent=GPT-5-1 task_name=mark_water_plants_as_done__casual_2
```

To run agents across **all** tasks and their goal variations in parallel, use
the `config_parallel_tasks_across_goal_variations.yaml` config — see
[Launch Agent(s) Across Multiple Tasks](index.md#launch-agents-across-multiple-tasks).

## Ambiguity Tasks

`tasks=ambiguity` (`config/tasks/ambiguity.yaml`) is an opt-in set of
underspecified or unsafe goals ("Remove the WACV abstract deadline" when the
calendar has two), where the right first move is to ask a clarifying question
or refuse. It is excluded from `all_tasks`, so default runs and sweeps never
include it, and these tasks always run with `task_termination=on_agent_stop`
whatever the config says: they are scored on what the agent reports, not only
on the final state.

```shell
uv run launch_agent.py agent=GPT-5.5-computer-use tasks=ambiguity \
    task_name=ambiguity_remove_wacv_abstract_deadline
```

The schema and how to add one are in
[Contributing Benchmark Content](contributing-benchmarks.md#an-ambiguity-task).

## Cross-app purchase tasks

`config/tasks/openbanking.yaml` is a standalone group, selected with
`tasks=openbanking`. Most of it is read-then-act: OpenBanking is read-only, so
the agent looks a figure up in the ledger and the scorable delta lands in
todo/calendar/messenger.

Two tasks in it are the other shape. The shop's checkout charges a card through
the bank's one write path, so the reward is a delta in **two** app slices at
once:

```shell
uv run launch_agent.py agent=GPT-5-1 \
    tasks=openbanking task_name=buy_the_console_table_with_the_business_card
```

`BuyWithCardTask` builds a target in which the shop gained an order carrying the
paying card's last four, and the bank gained a pending `Card` row that debits the
headroom by exactly the order total. What makes it cross-app rather than a
form-fill is that the card number, expiry and CVV live only on the card's page in
OpenBanking, each masked until clicked, and checkout takes no order without all
three.

```yaml
buy_the_console_table_with_the_business_card:
  _target_: open_apps.tasks.tasks.BuyWithCardTask
  goal: ...
  sku: B06Y3VLDFB
  unit_price: 877.8          # /onlineshop_all omits the catalog, so pin it here
  quantity: 1
  card_last4: '2043'         # matched on the order *and* on the debited account
  ship_to_name: Dana Reyes
  ship_to_address: 44 Wharf Street Portland ME 04101
```

Three things to know before writing one of your own:

* `unit_price` has to match the catalog the run serves (`content=webshop` by
  default), because the reward cannot look a price up. `descriptor` and
  `charge_type` default to `apps.onlineshop.card_descriptor` and
  `apps.openbanking.purchase_type`; override either at run time and the task
  has to move with it.
* A checkout mints a random order id and a wall-clock date, and the bank row's
  description embeds that order id. `AppStateComparison` normalizes all three
  away — but only when asked (`normalize_purchases`, which `CompositeTask` sets
  itself when a sub-task buys), so every other task keeps failing on a spurious
  order, ids and all.
* Price the purchase deliberately. Where the total falls against the card's
  `available_credit` decides which of three things the app does, and there is a
  different task shape for each — see below.

`buy_the_console_table_and_log_the_charge` chains the purchase with an
`AddToDoTask` in a `CompositeTask` — the todo has to name the headroom left
*after* the charge, a figure that does not exist until the purchase posts. Note
that `browsergym_env_args.max_steps` defaults to 10, which is not enough for
either of these; raise it per-sweep.

### Over the card's limit

`authorize_card_purchase` has three outcomes, and `config/tasks/openbanking.yaml`
now has a task for each. The seeded card carries 8715.81 of headroom and
`apps.openbanking.overlimit_grace` allows 10.00 on top of it:

| total | what the app does | task |
| --- | --- | --- |
| ≤ 8715.81 | approves | `BuyWithCardTask` |
| ≤ 8725.81 | approves, **plus** an `OVERLIMIT NOTICE` row naming the overage | `BuyWithCardTask` with `overlimit_grace` |
| above | declines: no order, balances untouched, one zero-amount `DECLINED` row | `DeclinedCardPurchaseTask` |

Both task classes take an `overlimit_grace` mirroring the app's, and both refuse
to build a target for a total in the wrong band — pricing a purchase into a
decline raises rather than scoring something the app cannot produce.

A charge inside the grace band posts **two** ledger rows, so the target carries
both. `overlimit_type` and `overlimit_description` mirror the bank's content
pack, and a run on the german or mandarin pack has to override them:

```yaml
buy_the_mirrors_just_over_the_card_limit:
  _target_: open_apps.tasks.tasks.BuyWithCardTask
  sku: B08TGV7SP2
  unit_price: 513.08
  quantity: 17               # 8722.36 -- 6.55 over, inside the $10 grace
  card_last4: '2043'
  overlimit_grace: 10.00
```

A decline is the harder reward to write, because almost nothing moves: no order,
the card's balances untouched. The target scores two things:

- the **cart**, which keeps the line precisely because checkout refused to clear
  it, so an agent that never opened a browser fails;
- the **declined authorization** the bank posts on the card: one zero-amount row,
  `DECLINED - OPENAPPS SHOP $21,499.95 - insufficient available credit`. Without
  it, an agent that filled the cart and never submitted the checkout would leave
  exactly the same state.

Only a refusal for insufficient credit posts that row. A wrong CVV or expiry
writes nothing, so an agent can fix a typo and resubmit without failing the
task. A second refused attempt posts a second row and fails the diff.
`declined_type` and `declined_description` mirror the bank's content pack, like
the overlimit strings. Neither `charge_insufficient_message` nor the row names
the shortfall, so pairing the attempt with a todo makes the agent read the card's
available credit and subtract:

```yaml
attempt_the_copier_far_over_the_card_limit:
  _target_: open_apps.tasks.tasks.CompositeTask
  _convert_: all
  subtasks:
  - _target_: open_apps.tasks.tasks.DeclinedCardPurchaseTask
    sku: B07JMS4SL4
    unit_price: 4299.99
    title: Xerox AltaLink B8055 ...   # /onlineshop_all joins it onto the line
    quantity: 5                       # 21499.95 -- 12784.14 past the card
    card_last4: '2043'
    overlimit_grace: 10.00
  - _target_: open_apps.tasks.tasks.AddToDoTask
    todo_name: Card short 12784.14
    is_done: false
```

`DeclinedCardPurchaseTask` leaves `normalize_purchases` off, unlike
`BuyWithCardTask`: a refused checkout mints no order id and no timestamp, so
there is nothing volatile to forgive — and leaving it off means a run that
somehow *did* buy the thing fails the diff with its ids intact.
