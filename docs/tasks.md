
To ask GPT-4o to mark water plants as done in your todo list:

```shell
    # export OPENAI_API_KEY=""
    uv run launch_agent.py agent=GPT-4o task_name=mark_water_plants_as_done
```

`task_name` specifies the task. Tasks are defined in `config/tasks/all_tasks.yaml`. For example,

```yaml
mark_water_plants_as_done:
  # Indicates class where reward logic is defined
  _target_: open_apps.tasks.tasks.MarkToDoDoneTask
  goal: Mark 'Water plants' as done in my todo list.
  goal_category: explicit
  todo_name: "Water plants"
```

## Adding New Tasks

To add a new task using an existing reward function, simply add a new entry to the `config/tasks/all_tasks.yaml`:

```yaml
add_my_special_item_to_todo:
  # _target_ defines the class containing the task reward logic
  _target_: open_apps.tasks.tasks.AddToDoTask
  goal: ENTER YOUR GOAL
  goal_category: explicit
  todo:
    - "ENTER YOUR TODO TITLE"
    - false
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