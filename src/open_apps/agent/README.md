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
    - Figure out how to map coordinates correctly! Vision models use coordinates and it's unclear when a coordinate is correct but just off by a factor or a coordinate is wrong.

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