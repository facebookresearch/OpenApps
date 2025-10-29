# --- Standard Library Imports ---
import dataclasses
from dataclasses import asdict
import logging
from typing import Any

# --- Project Imports ---
from browsergym.experiments.agent import Agent, AgentInfo
from agentlab.agents.agent_args import AgentArgs
from agentlab.llm.chat_api import BaseModelArgs, ChatModel, AnthropicChatModel
from agentlab.llm.base_api import AbstractChatModel
import agentlab.agents.dynamic_prompting as dp
from agentlab.llm.llm_utils import (
    Discussion,
    ParseError,
    SystemMessage,
)
from .utils import CustomActionSetArgs, retry
from .vLLM_prompt import VllmMainPrompt, PromptFlags

from anthropic import AnthropicBedrock
from openai import AzureOpenAI, OpenAI

# --- Logging Setup ---
logger = logging.getLogger(__name__)


@dataclasses.dataclass
class VLLMModelArgs(BaseModelArgs):
    model_name: str = "demo"
    model_pretty_name: str = "demo"
    port: str = "8000"
    api_key: str = "AMI_RULZ"
    api_version: str = None
    hostname: str = "0.0.0.0/v1"
    host_name_updated_on: str = "2025-01-01:00:00:00"
    temperature: float = 0.5
    vision_support: bool = True
    max_tokens: int = 100
    client_type: str = "vllm"
    aws_access_key: str = None
    aws_secret_key: str = None
    aws_session_token: str = None
    aws_region: str = "us-west-2"

    def make_model(self) -> AbstractChatModel:
        logger.info(f"Creating Model with model_name: {self.model_name}")

        if self.client_type == "vllm" or self.client_type == "gemini":
            suffix = "v1" if self.client_type == "vllm" else ""
            base_url = f"http://{self.hostname}:{self.port}/{suffix}"
            client_args = {"base_url": base_url}
            client_class = OpenAI
            return VLLMChatModel(
                model_name=self.model_name,
                hostname=self.hostname,
                port=self.port,
                api_key=self.api_key,
                api_version=self.api_version,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                host_name_updated_on=self.host_name_updated_on,
                n_retry_server=3,
                min_retry_wait_time=60,
                client_class=client_class,
                client_args=client_args,
            )

        elif self.client_type == "azure":
            client_args = {
                "azure_endpoint": f"https://{self.hostname}",
                "api_version": self.api_version,
            }
            client_class = AzureOpenAI
            return VLLMChatModel(
                model_name=self.model_name,
                hostname=self.hostname,
                port=self.port,
                api_key=self.api_key,
                api_version=self.api_version,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                host_name_updated_on=self.host_name_updated_on,
                n_retry_server=3,
                min_retry_wait_time=60,
                client_class=client_class,
                client_args=client_args,
            )
        elif self.client_type == "aws":
            return BedrockChatModel(
                model_name=self.api_version,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                max_retry=3,
                aws_access_key=self.aws_access_key,
                aws_secret_key=self.aws_secret_key,
                aws_session_token=self.aws_session_token,
                aws_region=self.aws_region,
            )
        elif self.client_type == "openai":
            client_args = {"base_url": "https://api.openai.com/v1"}
            client_class = OpenAI
            return VLLMChatModel(
                model_name=self.model_name,
                hostname=self.hostname,
                port=self.port,
                api_key=self.api_key,
                api_version=self.api_version,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                host_name_updated_on=self.host_name_updated_on,
                n_retry_server=3,
                min_retry_wait_time=60,
                client_class=client_class,
                client_args=client_args,
            )
        else:

            raise ValueError(f"Unknown client_type: {self.client_type}.")


class VLLMChatModel(ChatModel):
    def __init__(
        self,
        model_name,
        hostname,
        port,
        api_key="AMI_RULZ",
        api_version=None,
        temperature=0.5,
        max_tokens=100,
        n_retry_server=4,
        min_retry_wait_time=60,
        host_name_updated_on="2025-01-01:00:00:00",
        client_class=None,
        client_args=None,
    ):
        super().__init__(
            model_name=model_name,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retry=n_retry_server,
            min_retry_wait_time=min_retry_wait_time,
            client_class=client_class,
            client_args=client_args,
            pricing_func=None,
        )
        self.host_name_updated_on = host_name_updated_on


class BedrockChatModel(AnthropicChatModel):
    def __init__(
        self,
        model_name,
        temperature=0.5,
        max_tokens=100,
        max_retry=3,
        aws_access_key=None,
        aws_secret_key=None,
        aws_session_token=None,
        aws_region="us-west-2",
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retry = max_retry

        self.client = AnthropicBedrock(
            aws_access_key=aws_access_key,
            aws_secret_key=aws_secret_key,
            aws_session_token=aws_session_token,
            aws_region=aws_region,
        )


@dataclasses.dataclass
class VLLMAgentArgs(AgentArgs):
    """
    This class takes the yaml config and categorize the arguments into different subsets
    It also instantiate the agent.
    """

    model_name: str = "demo"
    model_pretty_name: str = "demo"
    custom_actions: list[str] = dataclasses.field(default_factory=list)
    use_html: bool = False
    use_axtree: bool = False
    use_screenshot: bool = False
    use_som: bool = False
    extract_visible_tag: bool = False
    extract_clickable_tag: bool = False
    extract_coords: bool = False
    filter_visible_elements_only: bool = False  # filter elements that are not visible
    use_focused_element: bool = False  # use focused element in the observation
    # --- Agent Flags ---
    use_memory: bool = False
    use_thinking: bool = False
    use_concrete_example: bool = False
    use_abstract_example: bool = False
    # --- ARGS for history ---
    use_history: bool = False  # enable history
    use_action_history: bool = False  # enable action history
    use_think_history: bool = False  # enable think history
    # --- Prompt Flags ---
    prompt_txt: dict = dataclasses.field(
        default_factory=dict
    )  # prompt text for the agent
    # --- ChatModel Flags ---
    hostname: str = "0.0.0.0/v1"
    port: str = "8000"
    api_key: str = "AMI_RULZ"
    api_version: str = None
    host_name_updated_on: str = "2025-01-01:00:00:00"
    temperature: float = 0.5
    max_tokens: int = 100
    client_type: str = "vllm"
    # --- AWS Bedrock Flags ---
    aws_access_key: str = None
    aws_secret_key: str = None
    aws_session_token: str = None
    aws_region: str = "us-west-2"

    def make_flags(self) -> PromptFlags:
        return PromptFlags(
            # figure out what to include in generic prompt flags
            obs=dp.ObsFlags(
                use_html=self.use_html,
                use_ax_tree=self.use_axtree,
                use_focused_element=self.use_focused_element,
                # --- ARGS for screenshot ---
                use_screenshot=self.use_screenshot,
                use_som=self.use_som,
                extract_visible_tag=self.extract_visible_tag,
                extract_clickable_tag=self.extract_clickable_tag,
                extract_coords=self.extract_coords,
                filter_visible_elements_only=self.filter_visible_elements_only,
                # --- ARGS for history tory---
                use_history=self.use_history,
                use_action_history=self.use_action_history,
                use_think_history=self.use_think_history,
            ),
            action=dp.ActionFlags(
                action_set=CustomActionSetArgs(
                    subsets=["custom"],  # define a subset of the action space
                    custom_actions=self.custom_actions,  # list of custom actions
                    strict=False,  # less strict on the parsing of the actions
                    multiaction=False,  # does not enable the agent to take multiple actions at once
                ),
                multi_actions=False,
            ),
            # --- ARGS for agent ---
            use_thinking=self.use_thinking,  # enable thoughts
            use_concrete_example=self.use_concrete_example,  # keep
            use_abstract_example=self.use_abstract_example,  # keep
        )

    def make_chat_model_flags(self) -> VLLMModelArgs:

        return VLLMModelArgs(
            model_name=self.model_name,
            model_pretty_name=self.model_pretty_name,
            port=self.port,
            api_key=self.api_key,
            api_version=self.api_version,
            hostname=self.hostname,
            host_name_updated_on=self.host_name_updated_on,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            client_type=self.client_type,
            aws_access_key=self.aws_access_key,
            aws_secret_key=self.aws_secret_key,
            aws_session_token=self.aws_session_token,
            aws_region=self.aws_region,
        )

    def make_agent(self) -> Agent:
        print("Creating DemoAgent with model_name: ", self.model_name)
        return VLLMAgent(
            chat_model_args=self.make_chat_model_flags(),
            flags=self.make_flags(),
            prompt_txt=self.prompt_txt,
        )


class VLLMAgent(Agent):
    def __init__(
        self,
        chat_model_args: BaseModelArgs,
        flags: PromptFlags,
        prompt_txt: dict,
        max_retry: int = 3,
    ):
        logging.info("Initializing vllmAgent with flags: %s", asdict(flags))
        self.chat_llm = chat_model_args.make_model()
        self.chat_model_args = chat_model_args
        self.max_retry = max_retry
        self.flags = flags
        self.action_set = flags.action.action_set.make_action_set()
        self._obs_preprocessor = dp.make_obs_preprocessor(flags.obs)
        self.prompt_txt = prompt_txt
        self.reset(seed=None)
        self.obs_history = []

    def obs_preprocessor(self, obs: dict) -> dict:
        return self._obs_preprocessor(obs)

    def get_action(self, obs: Any):

        self.obs_history.append(obs)
        main_prompt = VllmMainPrompt(
            action_set=self.action_set,
            obs_history=self.obs_history,
            actions=self.displayed_actions,  # in most cases same as self.actions, but in UItars we change the API, so displayed actions are the model native action calls, and actions are the browsergym native action calls
            thoughts=self.thoughts,
            flags=self.flags,
            prompt_txt=self.prompt_txt,  # pass the flags to the prompt
            client_type=self.chat_model_args.client_type,
        )

        system_prompt = SystemMessage(
            self.prompt_txt.system_prompt
            if self.prompt_txt.system_prompt is not None
            else dp.SystemPrompt().prompt
        )
        logging.info(f"The  prompt is: {str(main_prompt.prompt)}")
        try:
            chat_messages = Discussion([system_prompt, main_prompt.prompt])
            ans_dict = retry(
                self.chat_llm,
                chat_messages,
                n_retry=self.max_retry,
                parser=main_prompt._parse_answer,
            )
            print("the ans_dict is", ans_dict)
            ans_dict["busted_retry"] = 0
            # inferring the number of retries, TODO: make this less hacky
            ans_dict["n_retry"] = (len(chat_messages) - 3) / 2
        except ParseError:
            ans_dict = dict(
                action=None,
                n_retry=self.max_retry + 1,
                busted_retry=1,
            )
        stats = self.chat_llm.get_stats()
        stats["n_retry"] = ans_dict["n_retry"]
        stats["busted_retry"] = ans_dict["busted_retry"]

        self.actions.append(ans_dict["action"])
        if "displayed_action" in ans_dict: 
            self.displayed_actions.append(ans_dict["displayed_action"])
        else: # catch KeyError, this only happens for faulty parsing and hence None action anyways
            self.displayed_actions.append(ans_dict["action"])
        self.thoughts.append(ans_dict.get("think", None))

        agent_info = AgentInfo(
            think=ans_dict.get("think", None),
            chat_messages=chat_messages,
            stats=stats,
            extra_info={"chat_model_args": asdict(self.chat_model_args)},
        )
        return ans_dict["action"], agent_info

    def reset(self, seed=None):
        self.seed = seed
        self.thoughts = []
        self.actions = []
        self.displayed_actions = []
        self.obs_history = []
