import dataclasses
import wandb

from typing import Any

import bgym
from bgym import HighLevelActionSetArgs
from browsergym.experiments.agent import Agent, AgentInfo

from agentlab.agents.generic_agent.generic_agent import GenericAgentArgs, GenericAgent, GenericPromptFlags
from agentlab.llm.chat_api import BaseModelArgs
from agentlab.llm.base_api import AbstractChatModel, BaseModelArgs
import agentlab.agents.dynamic_prompting as dp
from agentlab.llm.llm_utils import (
    Discussion,
    HumanMessage,
    SystemMessage,
)

from browsergym.experiments.agent import Agent

import re
import numpy as np

@dataclasses.dataclass
class DummyModelArgs(BaseModelArgs):
    model_name: str = "dummy"
    model_pretty_name: str = "dummy"

    def make_model(self) -> AbstractChatModel:
        print(f"Creating DummyModel with model_name: {self.model_name}")
        return DummyModel(self)

        
class DummyModel(AbstractChatModel):
    def __init__(self, args: 'DummyModelArgs'):
        self.args = args
    def __call__(self, messages: list[dict]) -> dict:
        return {"response": f"Dummy response from {self.args.model_name}"}
    def get_stats(self):
        return {"model_name": self.args.model_name}
    

@dataclasses.dataclass
class DummyAgentArgs(GenericAgentArgs):
    """
    This class is meant to store the arguments that define the agent.

    By isolating them in a dataclass, this ensures serialization without storing
    internal states of the agent.
    """
    model_name: str = "dummy"
    model_pretty_name: str = "dummy"
    custom_actions: list[str] = dataclasses.field(default_factory=list)
    use_html: bool = False
    use_axtree: bool = True
    use_screenshot: bool = False
    hostname: str ="no host name for dumb dumbs "
    client_type: str = "dummy_client"


    def make_flags(self) -> GenericPromptFlags:    
        return GenericPromptFlags(
            obs = dp.ObsFlags(
                use_html=self.use_html,
                use_ax_tree=self.use_axtree,
                use_screenshot=self.use_screenshot
            ),
            action= dp.ActionFlags(
                action_set = HighLevelActionSetArgs(
                    subsets=["chat", "tab", "nav", "bid", "infeas"],  # define a subset of the action space
                    strict=False,  # less strict on the parsing of the actions
                    multiaction=False,  # does not enable the agent to take multiple actions at once
                )
            )
        )
    
    def make_chat_model_flags(self) -> DummyModelArgs:
        return DummyModelArgs(
            model_name=self.model_name,
            model_pretty_name=self.model_pretty_name,
        )
 

    def make_agent(self) -> Agent: 
        print("Creating DummyAgent with model_name: ", self.model_name)
        return DummyAgent(
            chat_model_args=self.make_chat_model_flags(),
            flags=self.make_flags(),
          
        )

class DummyAgent(GenericAgent):
    def __init__(
            self,
            chat_model_args: BaseModelArgs,
            flags: GenericAgentArgs,
           
    ):        
        super().__init__(chat_model_args=chat_model_args, flags=flags)
        self.action_history = []
        self.obs_history = []
   
        
    def get_action(self, obs:Any):
        response = "I'm a dummy agent, I click on a random link"
        print("the response is: ", response)

        messages = Discussion(SystemMessage("You are a web assistant."))
        messages.append(
            HumanMessage(
                f"""{obs["axtree_txt"]} """
            )
        )
        clickable_elements = re.findall(r'\[(\d+)\] link', messages[-1]['content'])
        print("the content of the last message is: ", messages[-1]['content'])
        if clickable_elements:
            print("clickable elements are: ", clickable_elements)
            action = f"click(\"{np.random.choice(clickable_elements)}\")"
        else:
            action = "noop()"
        
        self.action_history.append(action)
        agent_info = AgentInfo(
            think="i like clicking on random links ",
            chat_messages="".join([msg['content'] for msg in messages]),
            stats={},
            extra_info={},
        )
        return action, {"agent_info": agent_info}
