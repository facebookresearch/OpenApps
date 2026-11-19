"""The agent reports each step's token usage in its stats.

AgentLab's chat models count tokens into a tracker only while one is active.
``VLLMAgent.get_action`` opens one around its model calls so the counts land
in the step's stats, which AgentLab sums into ``summary_info.json`` as
``stats.cum_input_tokens`` / ``stats.cum_output_tokens``. Those sums are the
results preview's Tokens column. Without the tracker they were silently dropped.
"""
from __future__ import annotations

from types import SimpleNamespace

from agentlab.llm.llm_utils import HumanMessage
from hydra import compose, initialize
from hydra.utils import instantiate

from open_apps.agent import vLLM_agent


class _Prompt:
    """Stands in for VllmMainPrompt: a fixed message and a parser that accepts
    the first answer, so the test needs no real observation."""

    def __init__(self, **_):
        self.prompt = HumanMessage("Add a todo.")

    def _parse_answer(self, text: str) -> dict:
        return {"action": "noop()", "think": None}


def _agent():
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(
            config_name="config",
            overrides=[
                "agent=UI-TARS-1.5-7B",
                "agent.hostname=node-001",
                "logs_dir=/tmp/openapps-token-stats",
            ],
        )
    return instantiate(cfg.agent).make_agent()


def _completion(prompt_tokens: int, completion_tokens: int):
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        choices=[SimpleNamespace(message=SimpleNamespace(content="Action: wait()"))],
    )


def test_each_step_reports_its_own_token_usage(monkeypatch):
    agent = _agent()
    monkeypatch.setattr(vLLM_agent, "VllmMainPrompt", _Prompt)
    replies = iter([_completion(1200, 30), _completion(1500, 45)])
    monkeypatch.setattr(
        agent.chat_llm.client.chat.completions, "create", lambda **_: next(replies)
    )

    _, first = agent.get_action({})
    _, second = agent.get_action({})

    assert (first.stats["input_tokens"], first.stats["output_tokens"]) == (1200, 30)
    # Per step, not running totals: AgentLab does the summing.
    assert (second.stats["input_tokens"], second.stats["output_tokens"]) == (1500, 45)
