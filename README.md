# yapper

yapper makes Claude Code talk. Each reply ends with a short spoken summary, made by a
text-to-speech engine that runs on your own Mac (Chatterbox Turbo on MLX). The audio never
leaves the machine. Optionally, you talk back with OpenSuperWhisper, which types what you say
into the terminal.

## Quick start

```
brew install uv
brew install --cask opensuperwhisper   # optional: talk instead of type
git clone https://github.com/shmanubhav/yapper.git ~/yapper
~/yapper/bin/yapper --install          # links `yapper`, builds its Python, downloads the voice model (a few minutes)
yapper --setup                          # pick a voice and speed by ear, set up the dictation hotkey
yapper                                  # use instead of `claude`
```

`yapper --setup` plays the voices for you to choose, writes your config file, walks you through
OpenSuperWhisper's permissions and hotkey, then starts the engine and speaks a test phrase.

- Needs an Apple Silicon Mac on macOS 14 or later, with about 4.5 GB of free memory.
- OpenSuperWhisper asks for Microphone and Accessibility permission the first time you open
  it; you click those yourself, then pick a dictation hotkey.
- To change voice or speed later, run `yapper --setup` again (or use `yapper --sample` and the
  [config file](#config)). In the voice step, `b` goes back to the woman/man and accent
  questions and `a` plays every voice.

A plain `claude` session is untouched: everything yapper does lives in its own settings file,
which only the `yapper` command loads.

New here and using Claude Code? From a clone of this repo, ask Claude Code to "walk me through
INSTALL.md". That file is a step-by-step script for Claude Code, with the few steps only you
can do clearly marked.

## How it works

- A `UserPromptSubmit` hook (`bin/voice-brief`) tells Claude, every turn, to end its reply
  with a `<speak>...</speak>` block written for the ear, and cuts off speech still playing.
- A `Stop` hook (`bin/speak-hook`) sends that block to the local speech server.
- The speech server (`lib/speaker.py`) loads the model once and plays each sentence with
  `afplay` while it makes the next one.
- A mic watcher (`lib/micwatch.py`) stops speech the moment any microphone starts recording,
  so dictation never records the speech.

## Requirements

- An Apple Silicon Mac on macOS 14 or later
- About 4.5 GB of free memory while the speech engine runs, and about 600 MB of disk for its
  own Python, plus the model download
- Claude Code
- Homebrew and uv 0.12.22 or newer: `brew install uv` (or `brew upgrade uv`)
- python3 for the hooks (any python3 works; the Xcode Command Line Tools provide one: `xcode-select --install`)
- Optional: OpenSuperWhisper for dictation: `brew install --cask opensuperwhisper`.
  Without it, yapper still speaks; you type as usual.

## Install notes

- Clone where it will stay: yapper runs from that folder, so don't delete or move it later.
  If you do move it, run `bin/yapper --install` again from the new place.
- `yapper --install` first links `~/.local/bin/yapper` to the clone. If something else is
  already there (a link to another clone, or a file), it leaves it alone and prints the `ln`
  command to replace it. If `~/.local/bin` is not on your PATH, it prints the one line to add
  to your shell's startup file (`~/.zshrc` for zsh, `~/.bash_profile` for bash); add it, then
  open a new terminal. It never edits that file itself.
- It then builds yapper's own Python environment in `~/.local/share/yapper` (the exact
  versions in `requirements.lock`, checked by hash) and downloads the voice model once. It
  needs the network; afterwards everything runs offline, without uv.
- OpenSuperWhisper on first open: allow it past Gatekeeper (System Settings, Privacy &
  Security, "Open Anyway"), grant Microphone and Accessibility, let it download its speech
  model, and pick a hotkey. Without it, yapper still speaks; you type as usual.
- Behind a proxy, or a download failed: see Troubleshooting.

## Use

- `yapper` starts Claude Code with speech. Any `claude` arguments pass through, except
  `--settings` (see Troubleshooting).
- `yapper --status` shows what is running; `yapper --stop` stops the speech engine and the
  mic watcher (OpenSuperWhisper is left alone).
- `bin/shh` in the clone cuts speech that is playing right now. Inside Claude Code:
  `! ~/yapper/bin/shh`. Speech also stops when you start dictating or send your next prompt.
- `yapper --sample` plays every voice in `voices/` at three speeds (0.95, 1.07 and 1.2),
  announcing "voice N, speed X" before each, so you can pick by ear. It loads the model once
  in its own short-lived process; a running engine is paused while the samples play and started
  again afterwards. To try only some clips: `yapper --sample derry_b ~/my-voice.wav`.

## Config

Copy `config.example` to `~/.config/yapper/config` (or point `YAPPER_CONFIG` at another file):

```
voice=derry_b
speed=1.07
```

- `voice`: the name of a clip in `voices/` (with or without `.wav`), or a path to your own
  `.wav`. See `voices/README.md` for what makes a good clip.
- `speed`: playback rate from 0.5 to 2.0; pitch is kept.

The file is read as plain data and never run. Comments (`#`) and unknown keys are ignored; a
bad value stops `yapper` with the line number. `yapper --config` shows what it would use.
Environment variables win over the file: `YAPPER_TTS_VOICE_WAV` and `YAPPER_TTS_SPEED`.
After a change, restart the engine: `yapper --stop`, then `yapper`.

Other optional settings: `YAPPER_SOCKET` (the engine's socket, default
`~/.cache/yapper/speech.sock`; its folder must be yours and closed to everyone else) and
`YAPPER_ENV` (where the speech environment lives, default `~/.local/share/yapper`).

## Troubleshooting

- Replies never speak, and `/hooks` inside Claude Code lists no yapper hooks: your
  organization may block hooks it does not manage. Look in
  `/Library/Application Support/ClaudeCode/managed-settings.json` for
  `"allowManagedHooksOnly": true` or `"disableAllHooks": true`. yapper cannot work around that
  policy; ask whoever manages the Mac.
- The hooks are listed but nothing speaks: start with `yapper`, not `claude --settings ...`.
  The hooks find this clone through `YAPPER_ROOT`, which only `yapper` sets; without it they
  quietly do nothing.
- `pass extra settings through voice-settings.json, not --settings`: Claude Code keeps only
  the last `--settings` flag, so a second one would drop yapper's hooks. yapper refuses it.
- Logs live in `~/.cache/yapper/`: `speak.log` (hooks), `tts.log` (speech engine) and
  `micwatch.log` (mic watcher).
- `speech environment missing or out of date`: run `yapper --install` once, online. Only an
  update that changes `requirements.lock` needs it; it stops the speech engine while it runs.
- `--install` says a download failed (a company proxy, say): try
  `UV_SYSTEM_CERTS=1 yapper --install`, set `HTTPS_PROXY`, or point
  `UV_PYTHON_INSTALL_MIRROR` at a reachable copy of the Python builds.
- `a speech engine still answers on ...speech.sock`: run `yapper --stop` again.
- `socket folder ... is open to other users`: yapper only talks to its engine through a folder
  only you can enter. Run the `chmod 700` it prints.
- `held by another engine (root ...)`: another yapper clone is using the same socket. Stop it,
  or give one of them its own `YAPPER_SOCKET`.
- OpenSuperWhisper missing or blocked: yapper warns and carries on; speech output still works.

## Privacy

- Speech is made and played on this Mac. No audio is sent anywhere, and Claude Code's own
  cloud dictation stays off in yapper sessions (`voice-settings.json`).
- The speech engine opens no network port. It listens on a private socket
  (`~/.cache/yapper/speech.sock`, in a folder only your account can enter), so other users
  on the Mac cannot make it speak or read the replies sent to it.
- Your prompts and Claude's replies still go to the Anthropic API, exactly as in any Claude
  Code session.
- `~/.cache/yapper/speak.log` and `tts.log` keep the first ~80 characters of each spoken
  reply, for troubleshooting. Delete them whenever you like.
- Audio made by Chatterbox carries Resemble AI's PerTh watermark, an inaudible marker that
  identifies it as synthetic speech.
- Supply chain: `--install` pins the TTS model to one Hugging Face revision and every Python
  package to a hash. One file is not pinned: mlx-audio downloads the speech tokenizer
  (`mlx-community/S3TokenizerV2`, `model.safetensors`) at whatever revision is current.
- Logs and pidfiles in `~/.cache/yapper/` are readable by your user only.

## Uninstall

```
yapper --stop
rm ~/.local/bin/yapper
rm -r ~/.local/share/yapper ~/.cache/yapper ~/.config/yapper
rm -r ~/yapper            # the clone
```

The model download stays in the Hugging Face cache (`~/.cache/huggingface/hub`, the
`models--mlx-community--chatterbox-turbo-fp16` folder); remove it too if nothing else uses it.
OpenSuperWhisper, if you installed it: `brew uninstall --cask opensuperwhisper`.

## Changing the Python packages

The pins live in `requirements.lock`, with hashes. To change one, edit its pin, then regenerate
the hashes, naming each package whose pin you changed (one `--upgrade-package` per package):

```
MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile --no-config --python-version 3.12 \
  --python-platform aarch64-apple-darwin --generate-hashes --upgrade-package <package> \
  requirements.lock -o requirements.lock
```

Without `--upgrade-package`, uv keeps the old hashes for that package, still exits 0, and
`--install` then fails on the hash check.

To try a change without touching your working setup, give the test clone its own environment
and socket (the socket's folder must exist and be private: `mkdir -m 700 /tmp/yapper-test`):
`YAPPER_ENV=/tmp/yapper-env ./bin/yapper --install`, then
`YAPPER_ENV=/tmp/yapper-env YAPPER_SOCKET=/tmp/yapper-test/speech.sock ./bin/yapper --check`.
`--install` stops only this clone's own speech engine, and leaves an existing
`~/.local/bin/yapper` that points at your main clone alone.

## Tests

`for t in tests/test_*.py; do python3 -I "$t" || echo "FAIL $t"; done`

Plain Python scripts against stubs and fakes. None of them start the speech engine, load the
model or play sound.

## License

MIT, see `LICENSE`. The speech model (Chatterbox Turbo, MIT) is downloaded at install time, not shipped here. All six voice clips in `voices/` were designed with Qwen3-TTS VoiceDesign (Apache-2.0) and are synthetic, not real people; see `voices/README.md`.
