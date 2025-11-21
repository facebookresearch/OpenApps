# Agent Configuration & Setup Guide

## Quickstart: Getting Your Agent to Run

### Prerequisites

To use GPT-4o to complete tasks, set your OpenAI API key:

```bash
export OPENAI_API_KEY=YOUR_KEY
```

Alternatively, edit the corresponding line in `config/agent/GPT-4o.yaml`.

### Supported Clients

We support multiple client types:

- vLLM
- OpenAI 
- Azure
- AWS

To use a different client, change the `client_type` argument in your configuration. Check out `/src/open_apps/agent/vLLM_agent.py` Line 49 and following for specifics about how these clients are called internally.

### Running Your Agent

```bash
uv run launch_agent.py agent=GPT-4o
```
To run a local model with [vLLM](https://docs.vllm.ai/en/latest/), 

1. Launch your local vLLM model: `vllm serve [MODEL_NAME]`. VLLM will tell you your hostname.

2. Launch your agent

```bash
uv run launch_agent.py agent=AGENT_CONFIG agent.hostname=VLLM_HOSTNAME
```

## Configuring Your Policy

Our agent policies are built on top of [AgentLab](https://github.com/ServiceNow/AgentLab). Our setup enables automatic configuration of your prompt with config flags.
Here are some key flags you can configure in your agent's YAML file:

**Observation Flags:**

- `use_axtree`: Enable AXTree observation (accessibility tree)
- `use_screenshot`: Enable screenshot observation
- `use_som`: Add visual marks to screenshots for element identification
- `extract_coords`: Include element coordinates in observations

**History & Memory Flags:**

- `use_history`: Enable action/thought history tracking
- `use_action_history`: Track previous actions taken by the agent
- `use_think_history`: Track previous thoughts/reasoning steps

**Reasoning & Examples Flags:**

- `use_thinking`: Enable chain-of-thought reasoning before actions
- `use_concrete_example`: Include concrete examples in the prompt
- `use_abstract_example`: Include abstract reasoning examples in the prompt

**Custom Prompts:**

- `prompt_txt.system_prompt`: Override the default system prompt
- `prompt_txt.action_prompt`: Define custom action instructions
- `prompt_txt.think_prompt`: Define custom thinking/reasoning instructions

For the complete set of configuration options, see `config/agent/default.yaml`.


## Creating Your Own Agent

If AgentLab's capabilities don't meet your needs, you can create a custom agent.

1. Navigate to `src/open_apps/agent/`
2. Copy and modify the following files:
   
      - `vLLM_agent.py`
      - `vLLM_prompt.py`

This allows you to build rich, custom agent implementations tailored to your specific requirements.