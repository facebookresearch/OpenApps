import base64
import io
import dataclasses
import json
import logging
from pathlib import Path

import numpy as np

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
from agentlab.llm.llm_utils import Discussion
from open_apps.agent.parse_actions import flexible_parser as flexible_parser

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
            subsets=["custom"],  # define a subset of the action space
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


def save_som_coordinates(obs: dict, step: int, save_dir: Path):
    """Write SOM bounding boxes for one step into set_of_marks_coordinates.json."""
    bid_names = {}
    for node in obs.get("axtree_object", {}).get("nodes", []):
        bid = node.get("browsergym_id")
        if bid:
            bid_names[bid] = {
                "name": node.get("name", {}).get("value", ""),
                "role": node.get("role", {}).get("value", ""),
            }
    step_marks = {}
    for bid, props in obs.get("extra_element_properties", {}).items():
        if props.get("set_of_marks") and props.get("bbox") is not None:
            x, y, w, h = props["bbox"]
            step_marks[bid] = {
                "name": bid_names.get(bid, {}).get("name", ""),
                "role": bid_names.get(bid, {}).get("role", ""),
                "bbox": {"x": x, "y": y, "width": w, "height": h},
                "bbox_xyxy": [x, y, x + w, y + h],
                "visibility": props.get("visibility"),
                "clickable": props.get("clickable"),
            }
    som_file = save_dir / "set_of_marks_coordinates.json"
    existing = []
    if som_file.exists():
        with open(som_file) as f:
            existing = json.load(f)
    existing.append({"step": step, "marks": step_marks})
    with open(som_file, "w") as f:
        json.dump(existing, f, indent=2)
