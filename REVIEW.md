# Review rules for yapper

Every gotcha in AGENTS.md is a review check.

## What blocks a merge
- A hook that can block or slow a turn: an unbounded network call or wait, a raised error, or stdout from `speak-hook`.
- `voice.enabled` or `voiceEnabled` turned on, or any path that sends audio off the Mac.
- The emotion tag lists in `speak-hook` and `voice-brief` out of step.
- An unpinned TTS model revision, a `requirements.lock` entry without hashes, or a pin changed without regenerated hashes.
- A launcher change that lets a second `--settings` replace `voice-settings.json`.
- Killing a process found by pattern (`pkill`/`pgrep -f`) instead of a pidfile checked against this clone's script path.
- A launcher that accepts an engine whose `/health` root is not this clone.
- Any network listener (TCP/UDP port) for the engine, or a client that talks to a socket without `check_path` (or `sock_problem` in shell).
- Config parsing that sources, evaluates or expands the file's values.
- A hard-coded checkout path, or a hook that fails loudly when `YAPPER_ROOT` is unset.
- A test that starts the real engine, loads the model, plays sound, or uses a socket outside a private temp folder.
- A behaviour change with no test in `tests/`, a failing test file, or a red or skipped `test` or `secrets` CI job.
- A workflow action not pinned by full SHA, `permissions` wider than `contents: read`, or a checkout without `persist-credentials: false`.
- Recorded speech, a real person's voice, a clip with no provenance line in `voices/README.md`, or a token in the diff.

## Never flag
- Code outside the diff: note it once as "pre-existing", never as a blocker.
- Silent failure paths in `shh` and the hooks: chosen, because speech is an add-on.
- Tests that never start the speech engine: the real model is checked by hand with `yapper --check` and `yapper --sample`.

## Nit cap: 2
Report at most 2 nits per review.
