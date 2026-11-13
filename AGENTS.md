# yapper

## What this repo is
Spoken Claude Code replies on an Apple Silicon Mac: `bin/yapper` starts Claude Code with `voice-settings.json`, whose two hooks inject the `<speak>` instruction each turn and send the reply's `<speak>` block to a local Chatterbox Turbo (MLX) speech server.
Speech is made and played on this Mac only. Speech-to-text is OpenSuperWhisper, a separate, optional app.
Launcher `bin/yapper`; hooks `bin/voice-brief` (UserPromptSubmit) and `bin/speak-hook` (Stop); server `lib/speaker.py` via `bin/tts-server`; voice trial `lib/sample.py` (`yapper --sample`); setup wizard `lib/wizard.py` (`yapper --setup`, interactive only; reads voice tags from the `## Voices` bullets in `voices/README.md`); mic watcher `lib/micwatch.py`; sentence split `lib/sentences.py`.

## Validate locally
- `for t in tests/test_*.py; do python3 -I "$t" || echo "FAIL $t"; done`: stdlib scripts against stubs and fakes; none start the speech engine, load the model or play sound. Keep it that way.
- CI (`.github/workflows/test.yml`) runs `bin/ci-test.sh` (the same loop, failing on any FAIL) on macOS and `bin/ci-secrets.sh` (pinned gitleaks over the whole history) on every PR and on main.
- `yapper --check` speaks a test phrase from the real engine; test a change with its own `YAPPER_ENV` and `YAPPER_SOCKET`, as the README describes.

## Gotchas
- The clone that `yapper` on the PATH points at runs the hooks of every yapper session, so a change there takes effect on the next prompt; work in a separate clone.
- `voice-brief` runs on the blocking prompt path: every network call is bounded at 1 second and every failure is swallowed.
- `speak-hook` always exits 0 and never writes to stdout; failures go to `~/.cache/yapper/speak.log`.
- The hooks find the clone through `YAPPER_ROOT` and the engine through `YAPPER_SOCKET`, both exported by the launcher; with `YAPPER_ROOT` unset the hook commands exit 0 silently.
- The allowed emotion tags live in both `speak-hook` and `voice-brief` and must match (`tests/test_brief.py`); an unknown tag would be read aloud, so it is dropped.
- Claude Code keeps only the last `--settings` flag, so the launcher refuses a caller's `--settings`.
- The engine and mic watcher are tracked by pidfiles next to the socket (`speech.sock.speaker.pid`, `speech.sock.micwatch.pid`); `--stop` kills a pid only while its command line still runs this clone's `lib/` script, and never searches by pattern. With no pidfile, nothing is killed.
- `/health` returns this clone's root; the launcher refuses an engine on its socket whose root differs.
- The launcher runs under `umask 077`; `speak-hook` creates `speak.log` as 600.
- The config file is parsed line by line with strict patterns, never sourced or evaluated.
- Hugging Face runs offline (`HF_HUB_OFFLINE=1`); only `--install` downloads, and it stops this clone's speech server first.
- `--sample` never runs beside an engine (two copies of the model do not fit in memory): the launcher stops this clone's engine first and starts it again afterwards; a foreign engine on the socket is refused.
- Changing a pin in `requirements.lock` needs `--upgrade-package` for that package, or the old hashes stay and `--install` fails.

## Standing choices
- Voice mode exists only in sessions started by `yapper`; nothing infers it from how a message reads.
- `voice-settings.json` keeps built-in dictation off (`voice.enabled` and `voiceEnabled` false), so audio stays on this Mac.
- Pinned: the TTS model's Hugging Face revision (`lib/speaker.py`) and the Python packages, with hashes (`requirements.lock`). Not pinned: mlx-audio itself downloads the speech tokenizer (`mlx-community/S3TokenizerV2`, `model.safetensors`) at its latest revision during `--install`.
- Speech never blocks or fails a turn: it is an add-on, so the hooks swallow every failure, and `speak-hook` logs its own.
- Dictation is optional: a missing OpenSuperWhisper is a warning, never an error.
- The voices in `voices/` are synthetic, not real people (`voices/README.md` records where each came from).

## Never do
- Never turn on built-in dictation in `voice-settings.json` or let audio leave the Mac.
- Never let a hook block a turn, print to stdout from `speak-hook`, or make a network call without a short timeout.
- Never unpin the TTS model revision or add a package to `requirements.lock` without its hash.
- Never commit recorded speech or a real person's voice.
- Never source or eval the config file.
