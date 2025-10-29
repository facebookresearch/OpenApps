import base64
import io
import dataclasses
import numpy as np

import logging
import re

from agentlab.llm.llm_utils import ParseError

from PIL import Image
from bgym import HighLevelActionSetArgs
from browsergym.core.action.highlevel import HighLevelActionSet
from browsergym.core.action.functions import (
    clear,
    click,
    dblclick,
    drag_and_drop,
    fill,
    focus,
    go_back,
    go_forward,
    goto,
    hover,
    keyboard_down,
    keyboard_insert_text,
    keyboard_press,
    keyboard_type,
    keyboard_up,
    mouse_click,
    mouse_dblclick,
    mouse_down,
    mouse_drag_and_drop,
    mouse_move,
    mouse_up,
    mouse_upload_file,
    new_tab,
    noop,
    press,
    report_infeasible,
    scroll,
    # scroll_at,
    select_option,
    send_msg_to_user,
    tab_close,
    tab_focus,
    upload_file,
)

from agentlab.llm.chat_api import ChatModel
from agentlab.llm.llm_utils import Discussion, ParseError, extract_code_blocks

action_map = {
    "clear": clear,
    "click": click,
    "dblclick": dblclick,
    "drag_and_drop": drag_and_drop,
    "fill": fill,
    "focus": focus,
    "go_back": go_back,
    "go_forward": go_forward,
    "goto": goto,
    "hover": hover,
    "keyboard_down": keyboard_down,
    "keyboard_insert_text": keyboard_insert_text,
    "keyboard_press": keyboard_press,
    "keyboard_type": keyboard_type,
    "keyboard_up": keyboard_up,
    "mouse_click": mouse_click,
    "mouse_dblclick": mouse_dblclick,
    "mouse_down": mouse_down,
    "mouse_drag_and_drop": mouse_drag_and_drop,
    "mouse_move": mouse_move,
    "mouse_up": mouse_up,
    "mouse_upload_file": mouse_upload_file,
    "new_tab": new_tab,
    "noop": noop,
    "press": press,
    "report_infeasible": report_infeasible,
    "scroll": scroll,
    # "scroll_at": scroll_at,
    "select_option": select_option,
    "send_msg_to_user": send_msg_to_user,
    "tab_close": tab_close,
    "tab_focus": tab_focus,
    "upload_file": upload_file,
}


@dataclasses.dataclass
class CustomActionSetArgs(HighLevelActionSetArgs):
    custom_actions: list[str] = dataclasses.field(default_factory=list)
    
    def make_action_set(self):
        if self.custom_actions is None or len(self.custom_actions) == 0:
            custom_actions = action_map.keys()
        else:
            custom_actions = self.custom_actions
        return HighLevelActionSet(
            subsets=['custom'],  # define a subset of the action space
            custom_actions=[
                action_map[action] for action in custom_actions if action in action_map
            ],
            multiaction=self.multiaction,
            strict=self.strict,
            retry_with_force=self.retry_with_force,
            demo_mode=self.demo_mode,
        )


def image_to_jpg_base64_url(image: np.ndarray | Image.Image):
    """Convert a numpy array to a base64 encoded image url."""

    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    if image.mode in ("RGBA", "LA"):
        image = image.convert("RGB")

    with io.BytesIO() as buffer:
        image.save(buffer, format="JPEG")
        image_base64 = base64.b64encode(buffer.getvalue()).decode()

    return f"data:image/jpeg;base64,{image_base64}"


def retry(
    chat: "ChatModel",
    messages: "Discussion",
    n_retry: int,
    parser: callable,
    log: bool = True,
):
    """Retry querying the chat models with the response from the parser until it
    returns a valid value.

    If the answer is not valid, it will retry and append to the chat the  retry
    message.  It will stop after `n_retry`.

    Note, each retry has to resend the whole prompt to the API. This can be slow
    and expensive.

    Args:
        chat (ChatModel): a ChatModel object taking a list of messages and
            returning a list of answers, all in OpenAI format.
        messages (list): the list of messages so far. This list will be modified with
            the new messages and the retry messages.
        n_retry (int): the maximum number of sequential retries.
        parser (callable): a function taking a message and retruning a parsed value,
            or raising a ParseError
        log (bool): whether to log the retry messages.

    Returns:
        dict: the parsed value, with a string at key "action".

    Raises:
        ParseError: if the parser could not parse the response after n_retry retries.
    """
    tries = 0
    while tries < n_retry:
        answer = chat(messages)
      
        logging.info(f"LLM response at try {tries}: {answer['content']}")
        try:
            return parser(answer["content"])
        except ParseError as parsing_error:
            tries += 1
            if log:
                msg = f"Query failed. Retrying {tries}/{n_retry}.\n[LLM]:\n{answer['content']}\n[User]:\n{str(parsing_error)}"
                logging.info(msg)
            messages.append(dict(role="user", content=str(parsing_error)))

    raise ParseError(f"Could not parse a valid value after {n_retry} retries.")


def flexible_parser(response: str) -> dict:
    """
    A parser that tries to correct or interpret the LLMs output into a valid policy, e.g. if it did not close a parenthesis or tag.
    """
    response = response.strip()
    result = {"action": None, "think": None}
    
    if not response:
        raise ParseError("Empty response received from the model.")
    
    
    # Try HTML tags first (properly closed)
    action_match = re.search(r'<action>(.*?)</action>', response, re.DOTALL | re.IGNORECASE)
    think_match = re.search(r'<think>(.*?)</think>', response, re.DOTALL | re.IGNORECASE)
    
    if action_match:
        result["action"] = action_match.group(1).strip()
    if think_match:
        result["think"] = think_match.group(1).strip()
    
    # Try unclosed HTML tags if properly closed ones weren't found
    if not result["action"]:
        # Match <action> followed by content until </action> or end of string
        unclosed_action_match = re.search(r'<action>\s*(.*?)(?:</action>|$)', response, re.DOTALL | re.IGNORECASE)
        if unclosed_action_match:
            result["action"] = unclosed_action_match.group(1).strip()
    
    if not result["think"]:
        # Match <think> followed by content until </think> or end of string
        unclosed_think_match = re.search(r'<think>\s*(.*?)(?:</think>|$)', response, re.DOTALL | re.IGNORECASE)
        if unclosed_think_match:
            result["think"] = unclosed_think_match.group(1).strip()
    
    # If no HTML tags found, try prefix format
    if not result["action"] or not result["think"]:
        # First try to parse inline format (Thought:...Action:...)
        thought_action_match = re.search(r'thought:\s*(.*?)action:\s*(.*)', response, re.DOTALL | re.IGNORECASE)
        if thought_action_match:
            if not result["think"]:
                result["think"] = thought_action_match.group(1).strip()
            if not result["action"]:
                result["action"] = thought_action_match.group(2).strip()
            # ADDED: Fallback to line-by-line parsing if still missing
    if not result["action"] or not result["think"]:
        lines = response.split('\n')
        for line in lines:
            line = line.strip()
            if not result["action"] and re.match(r'^action:\s*', line, re.IGNORECASE):
                result["action"] = re.sub(r'^action:\s*', '', line, flags=re.IGNORECASE)
            elif not result["think"] and re.match(r'^(think|thought):\s*', line, re.IGNORECASE):
                result["think"] = re.sub(r'^(think|thought):\s*', '', line, flags=re.IGNORECASE)


    if result["action"] is None or not result["action"].strip():
        raise ParseError(f"Failed to parse action from response: {response}")
    
    
    # HACK to help UI TARS: remap UI TARS native actions to browser gym actions
    result["displayed_action"] = result["action"] # store model native actions
    result = uitars_parser(result)

    return result
    

def uitars_parser(result):
    "Translates UITARS actions to browser gym actions"
    # note karenu: I am not sure if the translation is perfect
    # in particular if the coord are just transferable like that, but looks reasonable in practice
    # also both browsergym and uitars docs are ass, so i have to guess

    # UITARS API -> BrowserGym API

    # click(point='(375,292)') or click(point='<point>200 300</point>') ->  mouse_click(x=375.0, y=292.0)
    if result["action"].startswith("click(point=") or result["action"].startswith("click(start_box=") or result["action"].startswith("click(x="):
        coords = re.findall(r'\d+', result["action"])
        if coords:
            result["action"] = f"mouse_click(x={int(coords[0])}, y={int(coords[1])})"
    # type(content=text) -> keyboard_type(text=text)
    if result["action"].startswith("type(content="):
        content = re.findall(r'type\(content=\'(.*?)\'\)', result["action"])
        if content:
            result["action"] = f"keyboard_type(text='{content[0]}')"
    # scroll(direction='down', point='(906,509)') -> scroll(dx, dy)
    if result["action"].startswith("scroll(direction=d"):
        direction = re.findall(r"scroll\(direction='(.*?)', point='\((\d+),(\d+)\)'\)", result["action"])
        if direction:
            result["action"] = f"scroll({int(direction[0][1])}, {int(direction[0][2])})"
    # scroll(direction='up', point='(906,509)') -> scroll(dx, dy)
    if result["action"].startswith("scroll(direction=u"):
        direction = re.findall(r"scroll\(direction='(.*?)', point='\((\d+),(\d+)\)'\)", result["action"])
        if direction:
            result["action"] = f"scroll({-int(direction[0][1])}, {-int(direction[0][2])})"
    # right_single(point='(531,256)') -> mouse_click(x, y, button='right')
    if result["action"].startswith("right_single(point="):
        coords = re.findall(r'\d+', result["action"])
        if coords:
            result["action"] = f"mouse_click(x={int(coords[0])}, y={int(coords[1])}, button='right')"
    # hotkey(key='ctrl alt e') -> keyboard_press(key=key_comb)
    if result["action"].startswith("hotkey(key="):
        key_comb = re.findall(r"hotkey\(key='(.*?)'\)", result["action"])
        if key_comb:
            result["action"] = f"keyboard_press(key='{key_comb[0]}')"
    
    return result