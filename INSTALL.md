# Installing yapper: a guide for Claude Code

This file is written for Claude Code. A new user clones this repo, opens Claude Code in the
clone, and says "walk me through INSTALL.md". Claude, follow the steps in order.

Rules for the walk-through:

- Run the checks yourself and report each result in one plain line. Do not ask the user to
  run a command you can run.
- Steps marked **HUMAN-ONLY** need the user's hands (system dialogs, permissions, choosing by
  ear). Explain what to do, then say: "you do this, then say done". Wait for "done".
- Stop at the first failed check, explain it in plain words, and offer the fix. Do not go on
  past a failure.
- Never print or ask for passwords or tokens. Never use `sudo`.
- `REPO` below means the absolute path of this clone (run `pwd` in it).

## 1. Preflight (Claude runs these)

Run the hooks check (below the table) first: if the organization blocks hooks, nothing else
matters, so stop before installing or upgrading anything.

| Check | Command | Pass |
|---|---|---|
| Apple Silicon | `uname -m` | `arm64` |
| macOS 14 or later | `sw_vers -productVersion` | major version 14 or higher |
| Free memory | `memory_pressure \| tail -1` and `vm_stat` | about 4.5 GB free or reclaimable; if less, suggest closing big apps |
| Disk | `df -h ~` | at least 3 GB free (environment plus model) |
| Homebrew | `command -v brew` | a path |
| uv 0.12.22 or newer | `uv --version` | version at least 0.12.22; if missing: `brew install uv`, if older: `brew upgrade uv` (ask first either way) |
| Claude Code | `command -v claude` | a path |
| python3 (the hooks run on it) | `command -v python3` | a path; if missing: `xcode-select --install` (HUMAN-ONLY dialog) |
| Hooks allowed | see below | neither key set to true |

Hooks check: if `/Library/Application Support/ClaudeCode/managed-settings.json` exists, read
it and look for `"allowManagedHooksOnly": true` or `"disableAllHooks": true`. Either one means
the organization blocks yapper's hooks: stop here and tell the user that yapper cannot speak on
this Mac until whoever manages it allows user hooks. Do not try to work around the policy.

## 2. Install (Claude runs these, after the user agrees)

1. Before installing, tell the user in one plain sentence: "yapper will run from this folder,
   so keep it here; if you move it later, run the install again from the new place." If REPO
   looks temporary (in Downloads, /tmp, or a folder they called a test), suggest moving it to
   `~/yapper` first.
2. Install: `REPO/bin/yapper --install`. This also puts the command on the PATH (it links
   `~/.local/bin/yapper` to REPO first, before the slow part), so there is no separate link
   step. Run it with the longest Bash timeout (10 minutes); if your tool cannot wait that
   long, hand it to the user as `! REPO/bin/yapper --install`. On a download failure, read the
   hint it prints (proxy settings) and offer those fixes.
3. Read its output about the link:
   - "already points at this checkout" or "linked": fine.
   - "left as is" with an `ln -sfn` command: something else holds `~/.local/bin/yapper`
     (another clone, or a file). Tell the user what is there and ask before running the
     printed command.
   - "not on your PATH" with a line to add: offer to append that exact line to the startup
     file it names (ask first). Either way, use the full path `~/.local/bin/yapper` for the
     rest of this session; the user's new terminals pick it up.

## Shortcut: `yapper --setup` (the user's own terminal)

`yapper --setup` is an interactive wizard that covers steps 3 to 6 below: it plays the voices
and speeds for the user to pick, writes the config, walks through OpenSuperWhisper's
permissions and hotkey, then starts the engine and speaks a test phrase. It reads from a real
terminal, so neither your Bash tool nor a `!` line can run it (it exits 2 without one). Offer it
first: ask the user to open a separate terminal window and run `yapper --setup` there, wait for
them to say done, then go to step 7. If they would rather you lead, follow steps 3 to 6 with
`--sample` and the config file.

## 3. Dictation (optional)

Ask the user whether they want to talk to Claude, or only listen. If only listen, skip to
step 4: yapper works without dictation.

1. Install it (ask first): `brew install --cask opensuperwhisper`
2. **HUMAN-ONLY**: open OpenSuperWhisper from Applications. If macOS says it cannot check it
   for malware, open System Settings, Privacy & Security, and click "Open Anyway". Then say
   done.
3. **HUMAN-ONLY**: grant Microphone and Accessibility when it asks (System Settings, Privacy &
   Security, Microphone and Accessibility, switch OpenSuperWhisper on). Let it download its
   speech model. Then say done.
4. **HUMAN-ONLY**: in OpenSuperWhisper's settings, pick a hotkey for dictation that you do not
   use for anything else. Try it: press it, say a sentence, and check that the words appear
   in a text field. Then say done.

## 4. Pick a voice

1. No need to stop the engine: `yapper --sample` pauses it and starts it again afterwards.
2. List the clips: `ls REPO/voices/*.wav`. If the user has their own clip, see
   `voices/README.md` for what makes a good one (5 to 15 seconds, clean, mono WAV) and remind
   them to use only a voice they have the speaker's consent to clone.
3. **HUMAN-ONLY** (listening): run `yapper --sample` (or `yapper --sample derry_b
   /path/to/theirs.wav`), with the longest Bash timeout, or hand it to the user as
   `! yapper --sample`. It loads the model once (about 30 seconds) and then plays each voice
   at speeds 0.95, 1.07 and 1.2, announcing "voice N, speed X" before each. Ask the user to
   listen with sound on and tell you which voice number and speed they liked. Then say done.

## 5. Write the config (Claude does this)

1. `mkdir -p ~/.config/yapper`
2. Write `~/.config/yapper/config` with exactly two lines, using what the user picked:
   ```
   voice=derry_b
   speed=1.07
   ```
   `voice` is a clip name from `voices/` or an absolute path to a `.wav`.
3. `yapper --config` must show the chosen voice path and speed with no error.

## 6. Start and check

1. Restart the engine so it loads the config: `yapper --stop`, then `yapper --check`. The
   first start loads the model and can take over a minute: use the longest Bash timeout, or
   hand it to the user as `! yapper --check`.
   It should end with "all engines healthy." and speak "Yapper is ready."
2. Ask the user whether they heard it. If not: check the Mac's output device and volume,
   then read `~/.cache/yapper/tts.log` (last 20 lines).
3. `yapper --status` should show speech ready, and dictation running if they set it up.

## 7. First session

1. **HUMAN-ONLY**: in a new terminal, run `yapper`, and inside that session type `/hooks`.
   Check that both hooks are listed: a `UserPromptSubmit` hook running `bin/voice-brief` and
   a `Stop` hook running `bin/speak-hook`. Then ask any question and listen for the spoken
   summary at the end of the reply. Then say done.
2. If `/hooks` shows neither hook, go back to the hooks check in step 1. If they are listed
   but nothing speaks, read the last lines of `~/.cache/yapper/speak.log`: `TTS-OK` means the
   engine accepted the text, `TTS-FAIL` means it could not reach the engine (run
   `yapper --status`).

Finish by telling the user:

- Start voice sessions with `yapper` from now on; plain `claude` stays silent.
- `! REPO/bin/shh` inside a session stops speech that is playing.
- To change voice or speed later, run `yapper --setup` again (or edit `~/.config/yapper/config`,
  then `yapper --stop` and start `yapper` again).
