<!-- filepath: /home/claudiashi/agent-playground/src/web_agent_playground/agent/README.md -->
# Web Agent Playground - Agents

This directory contains the agent implementations for the Web Agent Playground, following the AgentLab codebase structure.

## Quick Start

For the main agent implementation, see `vLLM_agent.py`.
For the prompt structure, see `vllm_prompt.py`

## Files Overview

The `vLLM_agent.py` implementation consists of three main components:

### 1. Agent Arguments (`AgentArgs`)
- **Purpose**: Contains all configuration parameters from YAML files
- **Key Functions**: 
  - `make_agent()` - instantiates the actual agent
  - `make_flags()` - creates prompt flags ensuring YAML compatibility
  - `make_chat_model_flags()` - creates base LLM model configuration

### 2. Agent Class (`VLLMAgent`)
- **Purpose**: Defines the core agent behavior
- **Key Functions**: 
  - `get_action()` - processes observations and returns actions
  - The logic is defined in `vllm_prompt.py` 

### 3. Model Arguments (`ModelArgs`)
- **Purpose**: Contains LLM-specific configuration (VLLM, API settings, etc.)
- **Key Function**: `make_model()` - instantiates a new ChatModel

### Notes and Todos
- To add a new base LLM, the easiest way is to write a new ModelArgs. For example: [https://github.com/ServiceNow/AgentLab/blob/main/src/agentlab/llm/chat_api.py#L96]. 
- Base LLM args are passed as part of the agentargs right now. It might be good to rewrite it as a separate dictionary or class in the future. 

## Prompt Structure (`vllm_prompt.py`)

The `vllm_prompt.py` has several key components:

### 1. Prompt Constructor (`VllmMainPrompt`)
- **Purpose**: Assembles the complete prompt by ordering different components
- **Current Components**:
  - User instructions and goal
  - Current observation (HTML, AXTree, screenshots)
  - Action history and thought history
  - Action space description
  - Examples (concrete and abstract)
  - Output format specification

### 2. Individual Prompt Elements
- `Observation`: Formats current page state (HTML, AXTree, screenshots, errors)
- `History`: Formats previous actions and thoughts
- `ActionPrompt`: Describes available actions with examples
- `Think`: Provides thinking prompts and examples

### 3. Response Parser (`flexible_parser`)
- **Purpose**: Parses LLM responses to extract actions and thoughts
- **Supports multiple formats**:
  - HTML tags: `<action>...</action>` and `<think>...</think>`
  - Prefix format: `Action: ...` and `Thought: ...`
  - Fallback line-by-line parsing

### Notes and Todos
- If we just want to change the prompt text, you can pass them in default.yaml.
- We can't change the prompt ordering automatically through default.yaml yet. The agent performance seems to be sensitive to the ordering. It's unclear if that's a functionality we want to include, because all the agent prompt structures are quite different. If we are replicating an existing agent, it might be worthwhile to just start a new prompt class.
- Current observation is that the agents are doing the "right" thing but the outputs are not currently parsed into the environment. I think the main ways of improving the agents are:
    - Improve the prompt such that the model can output according to instruction.
    - Improve the response parser to be more lenient in parsing. Note that if you change the prompt format, you might need to change the parser!
    - Enable multi-action? (for example, don't some tasks require fill and click at the same time step?)

## Coordinate Spaces

Vision models disagree about what an `(x, y)` in their output means, and getting
it wrong is silent: the click lands somewhere plausible and the episode scores 0
without an error. `action_parsers/coords.py::rescale_xy` is the single place that
conversion happens; every parser goes through `ActionParser.rescale`.

| convention | `coord_scale` | who |
| --- | --- | --- |
| raw viewport pixels | `null` | UI-TARS 1.5, GPT-4o-style computer use |
| normalized 0-1000 | `1000` | Qwen-VL, GLM-VL |
| normalized [0, N) | `N` | PaliGemma/Gemma-lineage `<locNNNN>` bins are 0-1024 |

Each parser family carries a default (`uitars`: null, `qwen3vl`: 1000). Override
per model in the agent yaml with `coord_scale: N`. Note this is one scalar applied
against each viewport axis, which is what a *square* normalized grid means — it
cannot express a model predicting in its own non-square resized image space.

The most reliable setup is to *declare* the grid in the prompt and set
`coord_scale` to match, rather than reverse-engineering a checkpoint's native
convention — then the conversion is correct by construction as long as the model
complies. `config/agent/Qwen3.6-VL-computer-use.yaml` does this with a 1000x1000
grid.

Under the `uitars` grammar, rescaling applies to UI-TARS-native forms
(`click(point=)`, `click(start_box=)`, `click(x=)`, `right_single(point=)`, and
the `scroll(direction=, point=)` magnitude) *and* to models prompted directly in
browsergym syntax (`mouse_click(x=, y=)` and friends). Bare `scroll(dx, dy)` is
left alone — say so in the prompt, since a model on a normalized grid will
otherwise not know what units to scroll in.

### Calibrating a new model

Don't guess the scale, measure it:

1. Run a few episodes with `save_dir` set. Each step writes ground-truth element
   boxes to `<exp_dir>/set_of_marks_coordinates.json` (see `utils.save_som_coordinates`).
2. For a step where the model clearly intended a particular element, compare the
   raw predicted `(x, y)` (kept verbatim in `displayed_action`) against that
   element's `bbox`.
3. `predicted / actual` should come out near a constant ratio per axis. A ratio
   of ~0.52 on a 1920-wide viewport means the model is emitting 0-1000; ~0.53
   means 0-1024. A ratio near 1.0 with scattered error means the model is
   grounding badly, not scaling wrong — no `coord_scale` will fix that. Fall back
   to targeting elements by set-of-marks bid (`save_som: true`) instead.

## Configuration Options

### Observation Flags
Controls what observational data is included in prompts:

##### HTML and Structure 
- `use_html` (bool): Include raw HTML in the prompt
- `use_axtree` (bool): Include accessibility tree in the prompt
- `use_focused_element` (bool): Provide ID of the currently focused element

##### Visual Information
- `use_screenshot` (bool): Add page screenshots
- `use_som` (bool): Add set-of-marks to screenshots
- `extract_visible_tag` (bool): Tag visible elements in AXTree
- `extract_clickable_tag` (bool): Tag clickable elements in AXTree
- `extract_coords` (bool): Add element coordinates
- `filter_visible_elements_only` (bool): Show only visible elements

### Prompt flags 
#### History and Context 
- `use_history` (bool): Include previous steps in the prompt
- `use_action_history` (bool): Include action history (requires `use_history=True`)
- `use_think_history` (bool): Include thought history (requires `use_history=True`)

##### Agent Behavior
- `use_thinking` (bool): Enable chain of thought reasoning
- `use_concrete_example` (bool): Include concrete examples in prompts
- `use_abstract_example` (bool): Include abstract examples in prompts

#### Prompt txt
- A dictionary of custom prompt txt.

#### Actions
- `custom_actions` (list[str]): List of allowed actions (see `utils.py` for available actions)

## Related Resources

- [AgentLab Generic Agent](https://github.com/ServiceNow/AgentLab/blob/main/src/agentlab/agents/visual_agent/visual_agent.py)
- [AgentLab Prompt Constructor](https://github.com/ServiceNow/AgentLab/blob/main/src/agentlab/agents/visual_agent/visual_agent_prompts.py)
- [AgentLab Dynamic Prompting](https://github.com/ServiceNow/AgentLab/blob/main/src/agentlab/agents/dynamic_prompting.py)