
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

## Task Context

Tasks can also carry a **context**: a short multi-turn user/assistant conversation
that precedes and motivates the goal. This mimics how a real assistant request is
usually grounded in prior dialog, letting you study how agents behave when the goal
arrives at the end of a conversation rather than in isolation.

Every high-level task has a `__with_context` variant in a separate composable file,
`config/tasks/task_contexts.yaml`, which is merged into `all_tasks.yaml` just like
the goal variations. Each variant copies its original's fields **verbatim** — only a
`context` field is added — so the reward logic is identical to the base task:

```yaml
remove_wacv_abstract_deadline__with_context:
  _target_: open_apps.tasks.tasks.RemoveEventTask
  goal: Remove the WACV 2026 Abstract Deadline event from my calendar.
  title: WACV 2026 Abstract Deadline
  date: 2025-07-11
  context: |
    User: I was planning to submit our tracking paper to WACV 2026, but the experiments are taking longer than expected.
    Assistant: Are you thinking of moving the paper to a later venue instead?
    User: Yes. We still need to finish the ablations and rewrite most of the methods section, so that deadline is no longer useful.
    Assistant: Understood. Do you want help cleaning up anything related to the old submission plan?
```

The `context` holds only the turns that come *before* the goal. At prompt-assembly
time the agent is shown the conversation followed by a `User goal:` line carrying the
task's `goal`:

```
<context conversation>

User goal: <goal>
```

Run a context variant like any other task:

```shell
uv run launch_agent.py agent=GPT-5-1 task_name=remove_wacv_abstract_deadline__with_context
```

To add your own, create a `<original_task>__with_context` entry in
`config/tasks/task_contexts.yaml`, copy every non-`context` field verbatim from the
original task, and write a conversation that stays consistent with the goal's
concrete details (exact titles, dates, names, message text, places) so the task
remains solvable.

### Context on goal variations

The [goal variations](#goal-variations) also get contexts, so you can vary phrasing
and preceding conversation together. `config/tasks/user_goal_variations_with_context.yaml`
adds the base task's context to each of its goal variations, keyed
`<original_task>__<style>_<n>__with_context`. The context motivates the underlying
task (identical across phrasings), so it is shared across a task's variations while
the goal wording still differs per variation.

That file is **generated** — regenerate it after editing `task_contexts.yaml` or
`user_goal_variations.yaml`:

```shell
uv run scripts/generate_goal_variation_contexts.py
```
