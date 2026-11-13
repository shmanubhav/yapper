# Voices

Each `.wav` here is a reference clip: yapper's speech engine copies the voice in it.
`yapper --sample` plays them all at three speeds so you can choose; put your choice in
`~/.config/yapper/config` as `voice=<name>`.

## Voices

One bullet per voice, in this exact form so `yapper --setup` can ask "woman or man?" and
"which accent?" and play only the matches:

`` - `name.wav` (woman|man, Accent): short style. Where it came from. ``

The accent menu is built from the accents listed here, so a new accent needs no code change.

- `derry_b.wav` (woman, Irish): dry and deadpan, brisk. Synthetic, designed with Qwen3-TTS
  VoiceDesign (Apache-2.0) from the text description "A Northern Irish woman from Derry in her
  thirties with a broad local accent. Dry and deadpan, quick-witted and blunt but friendly,
  speaking at a brisk, natural pace." Not a real person.
- `british_woman.wav` (woman, British): polished and calm, a hint of dry humour. Synthetic, designed with Qwen3-TTS VoiceDesign (Apache-2.0)
  from the text description "An upper-class British woman in her forties with a polished Received Pronunciation accent. Calm and unhurried, quietly confident, impeccably polite, with a hint of dry humour. Smooth, rich low-mid voice and very clear diction." Not a real person.
- `british_man.wav` (man, British): polished and calm, a hint of dry humour. Synthetic, designed with Qwen3-TTS VoiceDesign (Apache-2.0)
  from the text description "An upper-class British man in his forties with a polished Received Pronunciation accent. Calm and unhurried, quietly confident, impeccably polite, with a hint of dry humour. Smooth, rich mid-low voice and very clear diction." Not a real person.
- `galway_woman.wav` (woman, Irish): warm and kind, a gentle musical lilt. Synthetic, designed with Qwen3-TTS VoiceDesign (Apache-2.0)
  from the text description "A woman from Galway in the west of Ireland with a thick, strong Irish accent and a gentle, musical lilt. Unmistakably Irish. Calm, warm and kind, with a twinkle of humour." Not a real person.
- `queensland_woman.wav` (woman, Australian): broad and laid-back, blunt and funny. Synthetic, designed with Qwen3-TTS VoiceDesign (Apache-2.0)
  from the text description "A woman in her thirties from the outback of Queensland, Australia, with the thickest, broadest Australian accent possible: heavy ocker drawl, flat nasal vowels and strongly rising endings. Unmistakably Aussie, laid-back, blunt and funny." Not a real person.
- `aussie_man.wav` (man, Australian): relaxed slow drawl, dryly funny. Synthetic, designed with Qwen3-TTS VoiceDesign (Apache-2.0)
  from the text description "A laid-back Australian bloke in his forties from the bush with the thickest possible broad ocker accent, a slow drawl, very flat wide vowels and dropped endings. Unmistakably Aussie, relaxed and dryly funny." Not a real person.

## Bring your own

Any clean 5 to 15 second mono WAV works: one speaker, no music or background noise, natural
speech. Drop it in this folder (or anywhere, and use its full path), then try it with
`yapper --sample <name-or-path>`.

Only use a voice you have the speaker's consent to clone. Keep your own clips out of this
repo: it ships synthetic voices only.
