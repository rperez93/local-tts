# Changelog

## 2.0.1

- List every CLI command in `tts --help` and add `tts help [COMMAND]`.
- Reshape the settings editor around Collab-style panes, title and key bars,
  highlighted selection and in-screen input. Refresh changed rows without flicker,
  preserve shell history and restore the terminal on exit.
- Add scrollable keyboard/command help, page and first/last navigation, cancelable
  input, Unicode-aware clipping and an explicit way to clear strings. Preserve
  validated atomic saves, masked secrets and external configuration reloads.

## 2.0.0

- Publish the distribution as **agents-local-tts** on PyPI. Commands remain `tts`
  and `local-tts`; Python imports remain `localtts`; existing settings/models stay
  in place. Uninstall a legacy `local-tts` distribution before replacing it in the
  same environment, to avoid conflicting console entry points.
- Add a configurable disk audio cache: fixed TTL (36 hours), a 256 MiB entry budget,
  LFU/LRU eviction, corruption cleanup, atomic publication, and buffered copies.
  Fingerprints cover synthesis inputs, model/voice identities, format, platform and
  implementation. Dynamic phonetics hooks bypass caching. Cache is opt-in.
- Add `tts warm`, including phrase prefill and bounded keep-alive for local models.
  No automatic daemon; holding models resident consumes RAM/VRAM.
- Add `tts settings`, a terminal editor with filtering, masked secrets and live
  refresh. Existing CLI/environment controls remain. Config writes are atomic and
  serialized. New requests see new settings; active utterances keep their snapshot.
  Backend startup-only changes apply on the next server start.
- Support native skills for Codex, Claude Code, OpenCode, Cursor, Gemini, Windsurf,
  Copilot CLI and Qwen; preserve unrelated legacy instructions during migration.
- Add optional final WAV silence without inserting it between streamed fragments.
  Batch ready compatible fragments to reduce repeated player startup overhead.
- Add calibration/sound suites and retained pronunciation trials. Fix scoped
  dictionary precedence, isolated-word IPA and overlapping phrase replacement.
- Add per-language RVC conversion parameters, with capability checks and server-side
  validation/reset so overrides do not leak across requests.
- Add tested build/publish automation with PyPI trusted publishing through `pypi`.

Local listening preferences and measurements are recorded separately in `docs/`;
quality scores and tests do not prove human-like pronunciation for every voice.
