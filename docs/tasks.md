
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
uv run launch_agent.py agent=gemma-4 task_name=edit_script_add_header_comment
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
