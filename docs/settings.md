# Settings reference

Generated from the v2 defaults. Set values with `tts config --set KEY=VALUE`,
`tts settings`, or the documented `LOCALTTS_...` environment variables.
The editor reloads automatically; new speech requests load current settings.
Startup-only server options apply on the next server start.

| Key | Default |
| --- | --- |
| `provider` | `"kokoro"` |
| `play` | `true` |
| `player` | `""` |
| `terminal_title` | `true` |
| `stream` | `true` |
| `player_args` | `{}` |
| `player_env` | `{}` |
| `ending_silence_ms` | `0` |
| `pronunciations` | `{}` |
| `cache_enabled` | `false` |
| `cache_ttl_hours` | `36.0` |
| `cache_max_mb` | `256.0` |
| `cache_dir` | `""` |
| `cache_revision` | `""` |
| `cache_policy` | `"lfu"` |
| `warm_keep_alive_seconds` | `0` |
| `warm_interval_seconds` | `10.0` |
| `tui_refresh_seconds` | `1.0` |
| `phonetics_hooks` | `[]` |
| `phonetics_hook_timeout` | `5.0` |

## Terminal editor

Launch with `tts settings`. The title, settings list, selected-value/input pane
and key legend stay in place as you navigate or resize the terminal. The editor
uses an alternate screen and restores your shell on exit. Minimum size: 40×12.

| Key | Action |
| --- | --- |
| Up/Down or j/k | Select a setting; scroll help |
| PgUp/PgDn | Move one page |
| Home/End or g/G | First/last row |
| Enter or e | Edit selected setting |
| / | Filter by name |
| a | Add `KEY=VALUE` (including map entries or new languages) |
| ? or F1 | Open/close complete keyboard and CLI help |
| Esc | Cancel input, close help, or clear filter |
| q or Ctrl-C | Quit; q is ordinary text while editing |
| Enter / Backspace / Ctrl-U in input | Save / delete character / clear input |

Values use text, numbers, true/false, or JSON for lists/maps. Blank edits cancel;
`""` clears a string. For example, add `languages.es=kokoro` or
`rvc.delivery.es.speed=0.95`. Empty map-entry assignments remove entries.
Secret values stay masked during editing. Environment overrides retain priority
over saved values. Invalid saves leave the config intact and input open to correct.

`tts settings --help` lists every key without a terminal. `tts help` lists every
CLI command; `tts help COMMAND` and `tts COMMAND --help` list its options.

## llamacpp

| Key | Default |
| --- | --- |
| `llamacpp.binary` | `"llama-tts"` |
| `llamacpp.model` | `""` |
| `llamacpp.vocoder` | `""` |
| `llamacpp.hf_repo` | `""` |
| `llamacpp.hf_file` | `""` |
| `llamacpp.hf_repo_vocoder` | `""` |
| `llamacpp.hf_file_vocoder` | `""` |
| `llamacpp.speaker_file` | `""` |
| `llamacpp.max_words` | `26` |
| `llamacpp.max_workers` | `2` |
| `llamacpp.threads` | `0` |
| `llamacpp.gpu_layers` | `null` |
| `llamacpp.guide_tokens` | `true` |
| `llamacpp.extra_args` | `[]` |

## openai

| Key | Default |
| --- | --- |
| `openai.base_url` | `"https://api.openai.com/v1"` |
| `openai.api_key` | `""` |
| `openai.model` | `"tts-1"` |
| `openai.voice` | `"alloy"` |
| `openai.speed` | `1.0` |
| `openai.timeout` | `120` |
| `openai.tone` | `""` |
| `openai.auto_tone` | `false` |

## piper

| Key | Default |
| --- | --- |
| `piper.binary` | `"piper"` |
| `piper.model` | `""` |
| `piper.language_models` | `{}` |
| `piper.speaker` | `null` |
| `piper.length_scale` | `null` |
| `piper.volume` | `null` |
| `piper.auto_tone` | `false` |
| `piper.extra_args` | `[]` |

## kokoro

| Key | Default |
| --- | --- |
| `kokoro.binary` | `"kokoro-tts"` |
| `kokoro.model_dir` | `""` |
| `kokoro.voice` | `""` |
| `kokoro.lang` | `""` |
| `kokoro.language_voices` | `{}` |
| `kokoro.speed` | `1.0` |
| `kokoro.emphasis_lengthen` | `0` |
| `kokoro.sentence_pause` | `""` |
| `kokoro.clause_pause` | `""` |
| `kokoro.auto_tone` | `false` |
| `kokoro.extra_args` | `[]` |
| `kokoro.server_url` | `""` |
| `kokoro.server_start` | `""` |
| `kokoro.server_timeout` | `30` |

## rvc

| Key | Default |
| --- | --- |
| `rvc.python` | `""` |
| `rvc.base_provider` | `""` |
| `rvc.model` | `""` |
| `rvc.index` | `""` |
| `rvc.device` | `"cpu"` |
| `rvc.pitch` | `0` |
| `rvc.method` | `""` |
| `rvc.index_rate` | `null` |
| `rvc.protect` | `null` |
| `rvc.conversion` | `{}` |
| `rvc.extra_args` | `[]` |
| `rvc.server_url` | `""` |
| `rvc.server_start` | `""` |
| `rvc.server_timeout` | `60` |
| `rvc.delivery` | `{"es": {"speed": 1.0, "pause_ms": 45, "pause_tone_ms": 130}, "en": {"speed": 1.0, "pause_ms": 60, "pause_tone_ms": 160}}` |
| `rvc.server_models` | `{}` |
| `rvc.server_model` | `""` |
| `rvc.language_models` | `{}` |

## command

| Key | Default |
| --- | --- |
| `command.template` | `"espeak-ng -w {output} {text}"` |
| `command.audio_fx` | `false` |
| `command.tone_tags` | `"strip"` |

## Language and dictionary maps

`tts languages --set es=rvc` selects a backend. Provider maps accept JSON or entry
assignments. RVC delivery/conversion also accept leaf settings, for example
`tts config --set rvc.delivery.es.speed=0.95`. Pronunciation keys can contain dots
and spaces; quote the entire assignment. Empty map-entry values remove entries.

Cache byte limits cover managed entries (header and audio), not model weights,
user exports, retained background files, filesystem allocation or RAM/VRAM.
TTL is fixed at creation; reads do not extend it. Cleanup occurs on activity.
Use `cache_policy=lfu` for frequency, or `lru` for recency.
