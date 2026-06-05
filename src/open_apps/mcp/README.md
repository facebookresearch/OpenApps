# OpenApps MCP server

Run OpenApps as an **environment-as-a-service** over the
[Model Context Protocol](https://modelcontextprotocol.io): reset the apps,
drive the UI with pixel actions, read screenshots, and score tasks — from a
world-model trainer, a UI agent, an eval harness, or interactively.

One process serves **one app + one browser** (FastHTML server in a thread +
an async Playwright Chromium). Spawn N processes for N parallel envs.

## Setup

```bash
uv sync                            # installs mcp, playwright, etc.
uv run playwright install chromium # one-time: download the browser
```

## Launch

```bash
# stdio (a local client spawns this as a subprocess)
uv run python -m open_apps.mcp --app todo

# HTTP / SSE (remote or multiple clients connect to a running server)
uv run python -m open_apps.mcp --app todo --transport http --host 127.0.0.1 --port 8000
```

Flags: `--app {todo,calendar,messages,map,codeeditor}` (default `todo`),
`--transport {stdio,http,sse}` (default `stdio`), `--host`, `--port`.

On startup you'll see the apps initialize ("Setting environment for ...");
the server is then ready for tool calls.

## Tools

| Tool | Description |
| --- | --- |
| `reset(seed=None)` | Reset all apps + reload the page → first observation. |
| `act(action, with_reward=True)` | Run a BrowserGym action string → observation. |
| `observe()` | Current observation without acting. |
| `screenshot()` | Current screenshot (PNG). |
| `get_state()` | Structured cross-app state (the JSON behind reward). |
| `describe_actions()` | The action grammar accepted by `act`. |
| `list_tasks(app=None)` | Task keys from `config/tasks/all_tasks.yaml`. |
| `load_task(key)` | Bind a task for scoring; returns its goal. Call `reset` after. |
| `get_reward()` | Reward for the bound task (1.0 if complete, else 0.0). |
| `set_goal(goal)` | Free-form goal, no automatic scoring. |
| `reconfigure(appearance, content, seed, extras)` | Live variant/seed change. |
| `list_apps()` | App keys actually registered (Java-aware). |
| `list_variants(app, group)` | Variant stems for `appearance`/`content`. |
| `app_url(app=None)` | Absolute URL of an app's landing page. |

**Actions** are BrowserGym action strings (full-resolution pixels):

```text
mouse_click(375, 292)        keyboard_type('Call Mom')      keyboard_press('Enter')
scroll(0, 300)               mouse_drag_and_drop(10, 20, 200, 40)
mouse_dblclick(120, 80)      goto('/calendar')              noop(300)
```

Call `describe_actions()` for the full list. An **observation** comes back as a
screenshot (PNG image content) plus a JSON blob:
`{url, reward, done, step_count, action_desc, error}`.

## Minimal client

```python
import asyncio, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def meta(result):  # the JSON text block in an observation
    txt = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return json.loads(txt[0]) if txt else {}


async def main():
    params = StdioServerParameters(
        command="python", args=["-m", "open_apps.mcp", "--app", "todo"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            await s.call_tool("reset", {})
            await s.call_tool("load_task", {"key": "add_call_mom_to_my_todo"})

            # Drive the UI: click the "New Todo" field, type, submit.
            # (In practice you'd read the screenshot to choose coordinates.)
            await s.call_tool("act", {"action": "mouse_click(465, 91)"})
            await s.call_tool("act", {"action": "keyboard_type('Call Mom')"})
            obs = await s.call_tool("act", {"action": "keyboard_press('Enter')"})
            print("after add:", meta(obs))  # reward -> 1.0


asyncio.run(main())
```

For HTTP, swap the transport:

```python
from mcp.client.streamable_http import streamablehttp_client
async with streamablehttp_client("http://127.0.0.1:8000/mcp") as (read, write, _):
    async with ClientSession(read, write) as s:
        ...
```

## In-process (no MCP)

`Session` is usable directly — handy for tests:

```python
import asyncio
from open_apps.mcp import Session

async def main():
    s = Session("todo")
    await s.start()
    obs = await s.reset()
    obs = await s.act("mouse_click(465, 91)")
    await s.close()

asyncio.run(main())
```

## Notes

- **One Session per process.** The FastHTML app + Playwright are process
  singletons; run multiple server processes for parallel environments.
- **uvloop / Python < 3.12.** The server forces plain-asyncio uvicorn and
  restores a subprocess-capable event-loop policy before launching the
  browser, so the Playwright driver spawns correctly even when uvloop is
  installed.
