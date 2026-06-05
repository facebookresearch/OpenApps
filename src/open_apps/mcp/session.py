"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

A full OpenApps environment: ``AppServer`` (apps + config) + an async
Playwright browser + the action executor + screenshot/reward.

``Session`` is the protocol-agnostic env unit — the lightweight,
embeddable replacement for the old ``Runtime``. It is driven directly
in-process (``from open_apps.mcp import Session``) or wrapped by the MCP
server (:mod:`open_apps.mcp.server`).

Process = session: the FastHTML app and Playwright are process
singletons, so run one ``Session`` per process and spawn N processes for
parallel envs.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from playwright.async_api import async_playwright

from open_apps.mcp.actions import execute
from open_apps.mcp.appserver import AppServer


__all__ = ["Observation", "Session"]


# Per-action settle: HTMX partial swaps often do not trip networkidle, so
# we wait (bounded) for networkidle then add a small fixed delay.
_NETWORKIDLE_TIMEOUT_MS = 2000
_LOAD_TIMEOUT_MS = 5000


def _ensure_subprocess_capable_policy() -> None:
    """Make sure the active asyncio policy can spawn subprocesses.

    Playwright's async API launches its driver via an asyncio subprocess.
    If a library (notably uvicorn+uvloop) installed an event-loop policy
    whose child watcher isn't available, that spawn fails with
    ``NotImplementedError`` on Python < 3.12. Restore the default policy in
    that case so the browser can launch.
    """
    if sys.platform == "win32":
        return
    policy = asyncio.get_event_loop_policy()
    if isinstance(policy, asyncio.DefaultEventLoopPolicy):
        return
    get_watcher = getattr(policy, "get_child_watcher", None)
    if get_watcher is None:
        return  # Python >= 3.14: child watchers are gone; subprocess works anyway.
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            get_watcher()
    except NotImplementedError:
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())


@dataclass
class Observation:
    """Vision-first observation: a screenshot plus scalar metadata."""

    screenshot_png: bytes
    url: str
    reward: float
    done: bool
    step_count: int
    action_desc: str | None = None
    error: str | None = None

    def meta(self) -> dict:
        """JSON-serializable metadata (everything except the image bytes)."""
        return {
            "url": self.url,
            "reward": self.reward,
            "done": self.done,
            "step_count": self.step_count,
            "action_desc": self.action_desc,
            "error": self.error,
        }


class Session:
    def __init__(
        self,
        app_name: str,
        *,
        port: int | None = None,
        host: str = "127.0.0.1",
        viewport: tuple[int, int] = (1024, 640),
        task=None,
        settle_ms: int = 150,
        extra_overrides: list[str] | None = None,
    ) -> None:
        self.app_name = app_name
        self.port = port
        self.host = host
        self.viewport = viewport
        self.task = task
        self.settle_ms = settle_ms
        self.extra_overrides = extra_overrides

        self.appserver: AppServer | None = None
        self.page = None
        self._pw = None
        self._browser = None
        self._context = None
        self._initial_state: dict | None = None
        self._step_count = 0
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Bring up the app server (blocking, off-loop) then the browser."""
        if self._started:
            return
        # AppServer.__init__ blocks until the server is healthy; run it in a
        # thread so the event loop stays responsive.
        self.appserver = await asyncio.to_thread(
            AppServer,
            self.app_name,
            port=self.port,
            host=self.host,
            extra_overrides=self.extra_overrides,
        )
        _ensure_subprocess_capable_policy()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        w, h = self.viewport
        self._context = await self._browser.new_context(
            viewport={"width": w, "height": h}
        )
        self.page = await self._context.new_page()
        self._started = True

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("Session not started; call `await session.start()` first.")

    @property
    def started(self) -> bool:
        """Whether ``start()`` has completed."""
        return self._started

    async def close(self) -> None:
        # Best-effort teardown; each step is independent so one failure
        # doesn't leak the rest.
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        if self.appserver is not None:
            try:
                await asyncio.to_thread(self.appserver.close)
            except Exception:
                pass
        self._started = False

    # -- control plane -----------------------------------------------------

    async def reset(self, *, seed: int | None = None, options: dict | None = None) -> Observation:
        self._require_started()
        await asyncio.to_thread(self.appserver.reset)
        self._initial_state = await asyncio.to_thread(self.appserver.get_state)
        await self.page.goto(self.appserver.url_for())
        try:
            await self.page.wait_for_load_state("networkidle", timeout=_LOAD_TIMEOUT_MS)
        except Exception:
            pass
        self._step_count = 0
        # Reset carries no reward (gym convention); skip the redundant probe.
        return await self.observe(with_reward=False)

    async def reconfigure(
        self,
        *,
        appearance: str | None = None,
        content: str | None = None,
        seed: int | None = None,
        extras: dict | None = None,
    ) -> None:
        self._require_started()
        await asyncio.to_thread(
            self.appserver.reconfigure,
            appearance=appearance,
            content=content,
            seed=seed,
            extras=extras,
        )

    # -- action ------------------------------------------------------------

    async def act(self, action: str, *, with_reward: bool = True) -> Observation:
        self._require_started()
        self._step_count += 1
        # Mirror BrowserGym: a failed action is recorded (not raised) so the
        # episode continues and the error surfaces in the observation.
        try:
            desc = await execute(self.page, action)
            error = None
        except Exception as e:
            desc = action
            error = f"{type(e).__name__}: {e}"
        await self._settle()
        return await self.observe(
            action_desc=desc, with_reward=with_reward, error=error
        )

    async def _settle(self) -> None:
        try:
            await self.page.wait_for_load_state(
                "networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS
            )
        except Exception:
            pass
        if self.settle_ms > 0:
            await asyncio.sleep(self.settle_ms / 1000)

    # -- observation / reward ---------------------------------------------

    async def observe(
        self,
        *,
        action_desc: str | None = None,
        with_reward: bool = True,
        error: str | None = None,
    ) -> Observation:
        self._require_started()
        png = await self.screenshot()
        reward = await self.get_reward() if with_reward else 0.0
        return Observation(
            screenshot_png=png,
            url=self.page.url,
            reward=reward,
            done=reward >= 1.0,
            step_count=self._step_count,
            action_desc=action_desc,
            error=error,
        )

    async def screenshot(self) -> bytes:
        self._require_started()
        return await self.page.screenshot(type="png")  # lossless

    def get_state(self) -> dict:
        """Synchronous cross-app state probe (wrap in to_thread on the loop)."""
        self._require_started()
        return self.appserver.get_state()

    async def get_reward(self) -> float:
        if self.task is None or self._initial_state is None:
            return 0.0
        cur = await asyncio.to_thread(self.appserver.get_state)
        cur["_url"] = self.page.url  # for URL-based tasks (NavigateToAppTask)
        try:
            done = self.task.check_if_task_is_complete(self._initial_state, cur)
        except Exception:
            return 0.0
        return 1.0 if done else 0.0

    def set_task(self, task) -> None:
        self.task = task
