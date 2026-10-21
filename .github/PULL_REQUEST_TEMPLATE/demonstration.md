<!--
Demonstration (training / analysis data, never ranked). Open with
?template=demonstration.md on the compare URL. Layout:
https://facebookresearch.github.io/OpenApps/leaderboard/#demonstrations
-->

## Demonstration

- **Directory:** `leaderboard/demonstrations/<contributor>__<topic>/`
- **Who acted** (`human`, or the agent / model):
- **What the episodes show and how they were collected:**

## Checklist

- [ ] This PR adds or updates **one demonstration directory and nothing else**.
- [ ] `uv run -m open_apps.leaderboard.validate leaderboard/demonstrations/<contributor>__<topic>` prints `OK`:
  - [ ] `meta.yaml` has `kind: demonstration` and matches `leaderboard/schema/demonstration.schema.json`; the directory name matches `contributor`/`topic`;
  - [ ] every line of `episodes.jsonl.gz` is an episode record matching `leaderboard/schema/episode.schema.json`, and any `scores` it carries match the scorer.
- [ ] If there is an `agent.yaml`, it is redacted and `agent_config_hash` in `meta.yaml` matches it.
- [ ] The episodes contain no personal data, credentials or anything I can't share under this repository's license.
- [ ] Videos are linked, not committed; no file over 100 MB and no symlinks.
