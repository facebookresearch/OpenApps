<!--
Leaderboard submission. Open with ?template=leaderboard_submission.md on the
compare URL. Quickstart: https://facebookresearch.github.io/OpenApps/leaderboard/#submit
CI runs `validate` and `build` with main's code and comments the row this PR
would add; it fails a PR that also changes leaderboard or scoring code.
-->

## Submission

- **Bundle:** `leaderboard/submissions/<org>__<agent>__<suite>/`
- **Agent / model:**
- **How it was run** (local or cluster, hardware, anything unusual):
- **W&B report** (optional, public):

## Checklist

What `uv run -m open_apps.leaderboard.validate <bundle>` checks, plus what only a person can:

- [ ] This PR adds or updates **one bundle directory and nothing else** (no code, config, schema or workflow changes).
- [ ] The runs were launched with `benchmark=<suite>` and no overrides of the suite's tasks, cells, seeds, `max_steps` or termination.
- [ ] The bundle was written by `uv run -m open_apps.leaderboard.pack` and not edited afterwards (`agent.yaml` matches `agent_config_hash` in `meta.yaml`; every episode's `scores` match the scorer).
- [ ] `validate` prints `OK` (ranked) or `PARTIAL` (listed separately, not ranked) for the bundle, not `FAIL`:
  - [ ] layout: `meta.yaml`, `agent.yaml`, `episodes.jsonl.gz`, and the directory name matches `org`/`agent`/`suite` in `meta.yaml`;
  - [ ] schema: `meta.yaml` and every episode match `leaderboard/schema/`;
  - [ ] protocol: every episode ran with `task_termination=on_agent_stop`;
  - [ ] provenance: every episode's embedded task config, irreversible map, events and adversarial flags match the repo's config for its cell;
  - [ ] re-score: no episode's scores disagree with the scorer (run under the repo's config);
  - [ ] coverage: every suite cell at most once, none outside the suite. An unscorable episode, or one missing a configured event that should have fired ("event X should have fired at step N"), covers nothing: its cell is listed as missing until it is re-run.
- [ ] `agent.yaml` has no keys, tokens, hostnames, URLs or endpoints, and no episode's error messages or chat name a host or carry a secret (`pack` redacts and scrubs them; `validate` fails if any remain).
- [ ] Each cell is the run `pack` kept (a re-run replaces an infrastructure error; otherwise the later run), not the best of several attempts.
- [ ] Videos are linked as `https://` URLs in `meta.yaml`, not committed; no file over 100 MB and no symlinks.
- [ ] `contact` in `meta.yaml` reaches me, and `verified` is `false`: maintainers set it with `verify record` in a separately labelled PR, and CI fails a submission that adds or changes it (re-submitting a verified bundle: reset it to `false`).
