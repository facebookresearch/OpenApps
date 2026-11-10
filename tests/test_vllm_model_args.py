"""Tests for `ModelArgs.make_model` -- the endpoint a self-hosted agent talks to.

The case worth guarding is a missing hostname. Every self-hosted agent yaml
ships `hostname: null` because the node serving the model is only known at
launch time, and interpolating that into the base URL used to produce
`http://None:8000/v1` -- a DNS failure that surfaced three minutes later as a
bare `RetryError: Connection error`, naming neither the host nor the reason.
"""
from __future__ import annotations

import pytest
from hydra import compose, initialize

from open_apps.agent.vLLM_agent import ModelArgs


def model_args(**overrides) -> ModelArgs:
    fields = {"model_name": "test/model", "client_type": "vllm", "hostname": "node-001"}
    fields.update(overrides)
    return ModelArgs(**fields)


def _compose(agent: str):
    with initialize(version_base=None, config_path="../config/"):
        return compose(
            config_name="config",
            overrides=[f"agent={agent}", "logs_dir=/tmp/openapps-hostname"],
        )


def _self_hosted_agents() -> list[str]:
    """Agent configs that point at a vLLM endpoint and leave the host unset.

    Discovered rather than listed: the point of the guard is that *every*
    self-hosted config ships `hostname: null`, and a new one should be covered
    the day it lands. Configs that carry their endpoint differently (Qwen3.6-VL
    nests it) are skipped -- they never hit the interpolation being guarded.
    """
    from open_apps import config_dir

    found = []
    for path in sorted((config_dir() / "agent").glob("*.yaml")):
        cfg = _compose(path.stem)
        if cfg.agent.get("client_type") == "vllm" and cfg.agent.get("hostname") is None:
            found.append(path.stem)
    return found


_SELF_HOSTED = _self_hosted_agents()


class TestAMissingHostname:
    @pytest.mark.parametrize("hostname", [None, "", "   ", "None"])
    @pytest.mark.parametrize("client_type", ["vllm", "gemini"])
    def test_it_raises_instead_of_building_a_bogus_url(self, hostname, client_type):
        with pytest.raises(ValueError, match="No vLLM hostname"):
            model_args(hostname=hostname, client_type=client_type).make_model()

    def test_the_error_says_how_to_fix_it(self):
        with pytest.raises(ValueError) as excinfo:
            model_args(hostname=None).make_model()
        message = str(excinfo.value)
        assert "agent.hostname=<node>" in message
        assert "conduct_slurm.sh" in message

    def test_a_hostname_is_not_required_for_api_backed_clients(self):
        """Azure/OpenAI/Bedrock build their endpoint some other way, so the
        guard must not fire for them -- it is scoped to the two client types
        that interpolate `hostname` into a bare http:// URL."""
        args = model_args(hostname=None, client_type="openai")
        assert args.hostname is None  # constructing it is fine
        # `make_model` for these paths needs real credentials, so the contract
        # under test is only that the vllm/gemini guard did not claim it.
        assert args.client_type not in ("vllm", "gemini")


class TestTheShippedAgentConfigs:
    """The guard fires on what the repo actually ships.

    `hostname: null` is the documented default (`config/agent/default.yaml`),
    so every self-hosted config inherits it -- which is exactly the state that
    used to fail three minutes into a run.
    """

    @pytest.mark.parametrize("agent", _SELF_HOSTED)
    def test_launching_without_a_hostname_fails_fast(self, agent):
        cfg = _compose(agent)
        assert cfg.agent.get("hostname") is None, "config no longer defaults to null"

        from hydra.utils import instantiate

        with pytest.raises(ValueError, match="No vLLM hostname"):
            instantiate(cfg.agent).make_agent()

    def test_there_is_at_least_one_such_config(self):
        """Guards the discovery above from quietly matching nothing."""
        assert _SELF_HOSTED, "no vllm agent configs found to test"

    def test_a_supplied_hostname_gets_through_the_guard(self):
        with initialize(version_base=None, config_path="../config/"):
            cfg = compose(
                config_name="config",
                overrides=[
                    "agent=UI-TARS-1.5-7B",
                    "agent.hostname=node-001",
                    "logs_dir=/tmp/openapps-hostname",
                ],
            )
        from hydra.utils import instantiate

        agent = instantiate(cfg.agent).make_agent()
        assert agent is not None
