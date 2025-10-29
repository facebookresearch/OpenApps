"""
Prompt builder for GenericAgent

It is based on the dynamic_prompting module from the agentlab package.
"""

from dataclasses import dataclass
from PIL import Image
import numpy as np
from browsergym.core.action.base import AbstractActionSet
import base64
import io
from agentlab.agents import dynamic_prompting as dp
from agentlab.llm.llm_utils import BaseMessage, HumanMessage as _HumanMessage


from agentlab.llm.llm_utils import (
    BaseMessage,
)

from open_apps.agent.utils import flexible_parser

def image_to_jpg_base64_url(image: np.ndarray | Image.Image):
    """Convert a numpy array to a base64 encoded image url."""

    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    if image.mode in ("RGBA", "LA"):
        image = image.convert("RGB")
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.standard_b64encode(buffered.getvalue()).decode("utf-8")

class HumanMessage(_HumanMessage):
    def __init__(self, content, client_type):
        super().__init__(content)
        self.client_type = client_type

    def add_image(self, image: np.ndarray | Image.Image | str, detail: str = None):
        
        if not isinstance(image, str):
            image = image_to_jpg_base64_url(image)
            

        if self.client_type == "aws":
            self["content"].append({"type": "image", "source": {"type": 'base64', "media_type": "image/jpeg", "data": image}})
            return

        image = f"data:image/jpeg;base64,{image}"
        if detail:
            self.add_content("image_url", {"url": image, "detail": detail})
        else:
            self.add_content("image_url", {"url": image})

@dataclass
class PromptFlags(dp.Flags):
    """
    A class to represent various flags used to control features in an application.
    """

    obs: dp.ObsFlags = None
    action: dp.ActionFlags = None
    use_thinking: bool = True
    use_concrete_example: bool = False
    use_abstract_example: bool = True


class History(dp.PromptElement):
    """
    Format the actions and thoughts of previous steps."""

    def __init__(self, actions, thoughts) -> None:
        super().__init__()
        prompt_elements = []
        for i, (action, thought) in enumerate(zip(actions, thoughts)):
            if i == 0:
                prompt_elements.append("# History of previously taken actions and thoughts")
            prompt_elements.append(
                f"""
## Step {i}
### Thoughts:
{thought}
### Action:
{action}
"""
            )
        self._prompt = "\n".join(prompt_elements) + "\n"


class Observation(dp.PromptElement):
    """Observation of the current step.
    It includes the HTML, AXTree, focused element, and error logs.
    """

    def __init__(self, obs, flags: dp.ObsFlags) -> None:
        super().__init__()
        self.flags = flags
        self.obs = obs

        # if an error is present, we need to show it
        self.error = dp.Error(
            obs["last_action_error"],
            visible=lambda: flags.use_error_logs and obs["last_action_error"],
            prefix="## ",
        )
        self.html = dp.HTML(
            obs[flags.html_type],
            visible_elements_only=flags.filter_visible_elements_only,
            visible=lambda: flags.use_html,
            prefix="## ",
        )
        self.ax_tree = dp.AXTree(
            obs["axtree_txt"],
            visible_elements_only=flags.filter_visible_elements_only,
            visible=lambda: flags.use_ax_tree,
            coord_type=flags.extract_coords,
            visible_tag=flags.extract_visible_tag,
            prefix="## ",
        )
        self.focused_element = dp.FocusedElement(
            obs["focused_element_bid"],
            visible=flags.use_focused_element,
            prefix="## ",
        )

    @property
    def _prompt(self) -> str:
        return f"""
# Observation of current step:
{self.html.prompt}{self.ax_tree.prompt}{self.focused_element.prompt}{self.error.prompt}

"""

    def add_screenshot(self, prompt: BaseMessage) -> BaseMessage:
        if self.flags.use_screenshot:
            if self.flags.use_som:
                screenshot = self.obs["screenshot_som"]
                prompt.add_text(
                    "\n## Screenshot:\nHere is a screenshot of the page, it is annotated with bounding boxes and corresponding bids:"
                )
            else:
                screenshot = self.obs["screenshot"]
                prompt.add_text("\n## Screenshot:\nHere is a screenshot of the page:")
            img_url = image_to_jpg_base64_url(screenshot)
            prompt.add_image(img_url, detail=self.flags.openai_vision_detail)
        return prompt


class ActionPrompt(dp.PromptElement):


    def __init__(self, action_set: AbstractActionSet, action_flags: dp.ActionFlags, concrete_ex_txt: str, abstract_ex_txt: str) -> None:
        super().__init__()
        self.action_set = action_set
        self.action_flags = action_flags
        self.abstract_ex_txt = abstract_ex_txt
        self.concrete_ex_txt = concrete_ex_txt

        action_set_generic_info = """\
Note: This action set allows you to interact with your environment. Most of them
are python function executing playwright code. Remember you can only use one action at a time. 
Check the history for more context about which action you already took.

"""
        action_description = action_set.describe(
            with_long_description=action_flags.long_description,
            with_examples=False,
        )
        if "Example:" in action_description:
            action_description = action_description.split("Example:")[0].strip()

        self._prompt = (
            f"# Action space:\n{action_set_generic_info}{action_description}\n"
        )
        
        if self.abstract_ex_txt is str(None) or self.abstract_ex_txt == "" or self.abstract_ex_txt == "None":
            example = self.action_set.example_action(abstract=True)
            self._abstract_ex = f"""<action>
{example}
</action>
        """
        else:
            self._abstract_ex = abstract_ex_txt

        if self.concrete_ex_txt is str(None) or self.concrete_ex_txt == "" or self.concrete_ex_txt == "None":
            self._concrete_ex = """<action>
click('32')
</action>
"""
        else:
           self._concrete_ex = f"""
{self.concrete_ex_txt}
"""
    

class Think(dp.PromptElement):
    def __init__(self, concrete_ex_txt: str, abstract_ex_txt: str, think_prompt: str) -> None:
        super().__init__()
        self.abstract_ex_txt = abstract_ex_txt
        self.concrete_ex_txt = concrete_ex_txt
        if think_prompt is None or think_prompt == "":
            self._prompt = "" 
        else:
            self._prompt = f"""{think_prompt}
"""

        if abstract_ex_txt is None or abstract_ex_txt == "" or abstract_ex_txt == "None":
            self._abstract_ex = """<think>
Think step by step. If you need to make calculations such as coordinates, write them here. Describe the effect
that your previous action had on the current content of the page.
</think>
"""
        else:
            self._abstract_ex = f"""
{abstract_ex_txt}
            """
        if concrete_ex_txt is None or concrete_ex_txt == "" or concrete_ex_txt == "None":
            self._concrete_ex = """
<think>
From previous action I tried to set the value of year to "2022",
using select_option, but it doesn't appear to be in the form. It may be a
dynamic dropdown, I will try using click with the bid "32" and look at the
response from the page.
</think>
"""
        else:

            self._concrete_ex = f"""
            {concrete_ex_txt}
            """
 

class VllmMainPrompt(dp.PromptElement):
    """
    Here has the main logic for the vllm agent prompt.
    It includes the action set, observation, history, and action prompt.
    """

    def __init__(
        self,
        action_set: AbstractActionSet,
        obs_history: list[dict],
        actions: list[str],
        thoughts: list[str],
        flags: PromptFlags,
        prompt_txt: dict,
        client_type: str = "vllm"
    ) -> None:
        super().__init__()
        self.flags = flags
        self.history = History(actions, thoughts)
        obs = obs_history[-1]
        
        self.goal = obs["goal_object"]
        
        self.obs = Observation(obs_history[-1], self.flags.obs)
        
        self.prompt_txt = prompt_txt

        self.action_prompt = ActionPrompt(action_set, action_flags=flags.action,
                                           concrete_ex_txt=prompt_txt.get("action_concrete_example"), 
                                           abstract_ex_txt=prompt_txt.get("action_abstract_example"),
                                           )
        
        self.think = Think(concrete_ex_txt=prompt_txt.get("think_concrete_example"), 
                           abstract_ex_txt=prompt_txt.get("think_abstract_example"), 
                           think_prompt=prompt_txt.get("think_prompt")
                           )

        self.client_type = client_type

    @property
    def _prompt(self) -> HumanMessage:
        # todo: maybe surface out the ordering of the elements in the prompt
        
        prompt = HumanMessage(f"""
# User Instructions
{self.goal[0]['text']}""", self.client_type)
        
        prompt.add_text(
            f"""
## Output Format
{self.prompt_txt.output_format} \
{self.obs.prompt}\
{self.prompt_txt.action_prompt if self.prompt_txt.action_prompt else self.action_prompt.prompt}
{self.history._prompt}\
{self.think.prompt}\


"""
        )

        if self.flags.use_abstract_example:
            prompt.add_text(
                f"""
# Abstract Example

Here is an abstract version of the answer with description of the content of
each tag. Make sure you follow this structure, but replace the content with your
answer:
{self.think.abstract_ex}\

{self.action_prompt.abstract_ex}\

Do not output anything except the thought and action. 
"""
            )

        if self.flags.use_concrete_example:
            prompt.add_text(
                f"""
# Concrete Example

Here is a concrete example of how to format your answer.
Make sure to the format:
{self.think.concrete_ex}\
{self.action_prompt.concrete_ex}\
It is very important that you follow the format above.
"""
            )

        return self.obs.add_screenshot(prompt)
    
    def _parse_answer(self, text_answer):
        return flexible_parser(text_answer)
