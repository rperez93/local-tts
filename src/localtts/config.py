"""Configuration loading: defaults < config file < environment < CLI flags."""

import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

from localtts.errors import TTSError

APP_NAME = "local-tts"
ENV_PREFIX = "LOCALTTS_"

DEFAULTS = {
    # Provider used when --provider is not given. Kokoro speaks eight languages from a
    # single 82M model (VOICE_LANGS in providers/kokoro.py is the list), where llama.cpp's
    # OuteTTS covers only English, Chinese, Japanese and Korean -- so the old default silently mispronounced most of the world with
    # English phonetics. llamacpp is still a provider, just not the one you get by
    # accident.
    "provider": "kokoro",
    # Play the generated audio after synthesis (unless --output is given).
    "play": True,
    # Force a specific playback command (e.g. "ffplay"). Empty => autodetect, which
    # prefers Windows' own player on Windows and WSL and a Linux player elsewhere.
    # "windows" (or "powershell") selects the Windows player explicitly -- worth setting
    # on a WSL box where the Linux audio bridge is noisier than reaching out to Windows.
    "player": "",
    "cache_enabled": False,
    "cache_ttl_hours": 36.0,
    "cache_max_mb": 256.0,
    "cache_policy": "lfu",
    "warm_keep_alive_seconds": 0,
    "warm_interval_seconds": 10.0,
    "tui_refresh_seconds": 1.0,
    "cache_dir": "",
    "cache_revision": "",
    "ending_silence_ms": 0,  # Optional final WAV padding; never between fragments.
    # Per-machine tuning for whichever player is used, keyed by player name, e.g.
    #   {"ffplay": ["-af", "aresample=48000"]}
    # Inserted just before the file argument. Audio stacks differ per box (WSL's pulse
    # bridge, a resampling ALSA default, a device that wants a bigger buffer), and the
    # fix is nearly always a flag rather than a code change -- so it is configuration.
    # Say these words this way. Keys are matched whole-word and case-insensitively, and
    # a value takes one of two forms:
    #   {"jarvis": "JAR-viss"}            a respelling, used exactly as written and
    #                                     rewritten into the text, so it works on every
    #                                     backend
    #   {"pull request": "/pˈʊl ɹᵻkwˈɛst/"}  IPA between slashes, handed to the model as
    #                                     phonemes so a borrowed word keeps its own sound
    #                                     without the sentence being cut into pieces
    # IPA needs a backend with a phonemizer of its own -- `tts check` prints which, and
    # an entry a backend cannot use is ignored rather than mangled.
    # A bare key applies to every language; `<lang>:<word>` applies to that one only, so
    # a word said differently in two languages needs no nested structure. <tag> markup is
    # left untouched either way.
    "pronunciations": {},
    "player_args": {},
    # Environment applied to the player process only, e.g.
    #   {"SDL_AUDIODRIVER": "pulseaudio", "PULSE_LATENCY_MSEC": "90"}
    # The other half of the same problem: some players are tuned by environment rather
    # than argv, and this keeps that out of the user's shell profile.
    "player_env": {},
    # Show a speaker icon in the terminal's tab/window title while audio plays,
    # restoring the title when it stops. Purely cosmetic and skipped entirely when
    # there's no terminal to write to (piped output, a hook, CI).
    "terminal_title": True,
    # Play each fragment as soon as it is synthesized, instead of waiting for the whole
    # text to finish and playing one joined file. Time-to-first-sound then stops
    # depending on how long the text is -- the difference between a second and most of a
    # minute for a tagged story or a document. The single joined file is still written
    # either way. Set false to go back to synthesize-everything-then-play.
    "stream": True,
    # Executables that shape the /IPA/ table before it reaches a backend. Each one gets
    # {"text", "lang", "provider", "phonetics"} on stdin as JSON and prints the table to
    # use, so a lexicon, a house glossary or a real transcriber can answer "how is this
    # word said" for words nobody wrote into `pronunciations` by hand -- work this package
    # cannot do itself and should not grow a dependency for. Run in order, each seeing
    # what the last returned; a hook that fails leaves the table as it was. Nothing to do
    # with `tts hooks`, which is the status-bar hook. See localtts/phonetics.py.
    "phonetics_hooks": [],
    # How long one hook may take before the call goes on without it. Speech that is
    # slightly wrong beats speech that does not happen.
    "phonetics_hook_timeout": 5.0,
    # Which backend speaks which language, e.g.
    #   {"es": {"provider": "piper", "voice": "~/voices/es_MX-claude-high.onnx"}}
    # Shared by every coding agent so the preference is remembered in one place.
    "languages": {},
    "providers": {
        "llamacpp": {
            "binary": "llama-tts",
            # Path to the TTS gguf model. Leave empty to let llama.cpp fetch
            # its default OuteTTS weights (--tts-oute-default).
            "model": "",
            # Path to the vocoder gguf (WavTokenizer). Required with "model".
            "vocoder": "",
            # Alternative to local paths: pull from Hugging Face.
            "hf_repo": "",
            "hf_file": "",
            "hf_repo_vocoder": "",
            "hf_file_vocoder": "",
            # Speaker/voice profile JSON accepted by --tts-speaker-file.
            "speaker_file": "",
            # OuteTTS degrades past a couple of sentences per prompt, so long text is
            # synthesized in pieces and joined. 0 disables chunking.
            "max_words": 26,
            # Chunks run concurrently, up to this many at once. Each llama-tts call pays
            # several seconds of fixed process-startup/model-load cost regardless of how
            # short the chunk is, so overlapping chunks matters more than raising
            # max_words (which risks the degradation above). Measured on one RTX 4070
            # laptop GPU: 2 concurrent calls ~1.4x faster wall-clock than sequential;
            # 4 concurrent was not meaningfully faster than 2 (single-GPU compute is
            # still serialized) but used more VRAM for no benefit. 2 is a safe default
            # across GPU sizes; raise it if you have VRAM/cores to spare.
            "max_workers": 2,
            "threads": 0,          # 0 => let llama.cpp decide
            "gpu_layers": None,    # None => leave llama.cpp default
            "guide_tokens": True,  # improves word recall on OuteTTS
            "extra_args": [],
        },
        "openai": {
            # Works with api.openai.com and any OpenAI-compatible server
            # (openedai-speech, Kokoro-FastAPI, LocalAI, ...).
            "base_url": "https://api.openai.com/v1",
            "api_key": "",         # falls back to $OPENAI_API_KEY
            "model": "tts-1",
            "voice": "alloy",
            "speed": 1.0,
            "timeout": 120,
            # Free-text voice-style control -- the "instructions" field of the create-speech
            # API. Real, but only for model=gpt-4o-mini-tts (or its dated alias); tts-1 and
            # tts-1-hd reject it. Verified against OpenAI's own API reference, 2026-08. Sent
            # with every call when set and no <tag> is active in the text (see
            # text.resolve_tone_segments(), text.TAG_PROFILES).
            "tone": "",
            # Derive tone from sentence-ending punctuation for any stretch of text with no
            # explicit <tag>. Off by default -- it changes what gets said, not just how the
            # audio is produced, so it's opt-in. Shared, hand-written phrases for every
            # <tag> name (built-in ones like <anger>/<whisper>, and the punctuation-derived
            # question/exclamation/assertion categories) live in text.TAG_PROFILES, not
            # here -- one table, not one setting per tag per provider.
            "auto_tone": False,
        },
        "piper": {
            "binary": "piper",
            "model": "",           # path to a .onnx voice
            # Which voice model speaks which language, e.g.
            #   {"es": "~/voices/es_MX-claude-high.onnx", "en": "~/voices/en_US-lessac-high.onnx"}
            # A piper voice *is* a language, so speaking two means having two files. An
            # exact tag beats its base language.
            "language_models": {},
            "speaker": None,       # speaker id for multi-speaker voices
            # Real piper flags (verified via `piper --help`) -- inverse-of-rate and
            # loudness. None => piper's own default (1.0 either way), flag omitted. A
            # <tag>'s speed/volume multiplies onto whatever is configured here, it doesn't
            # replace it -- see PiperProvider._prosody_overrides().
            "length_scale": None,
            "volume": None,
            "auto_tone": False,    # see openai.auto_tone above for what this means
            "extra_args": [],
        },
        "kokoro": {
            "binary": "kokoro-tts",
            # Optional. Only some kokoro CLIs need this (see providers/kokoro.py);
            # leave empty unless yours resolves its model files by working directory.
            "model_dir": "",
            "voice": "",    # empty => the binary's own default
            "lang": "",     # empty => the binary's own default
            # Which voice speaks which language, e.g.
            #   {"es": "ef_dora", "en": "bm_george"}
            # Kokoro voices are per-language -- the first letter of the name says which
            # (a/b English, e Spanish, f French, ...) -- so one flat `voice` cannot serve
            # two languages. An exact tag beats its base language, and the phonemizer
            # language is then taken from the chosen voice rather than from `lang`, which
            # would otherwise stay stale and read one language with another's phonetics.
            "language_voices": {},
            "speed": 1.0,
            # Emphasis as a phonetician writes it: N IPA length marks on the vowel
            # carrying primary stress (kˈasa -> kˈaːsa). Kokoro has the length mark in
            # its own vocabulary, so the model hears it -- an isolated word measures
            # 0.576s plain, 0.640s with one mark, 0.661s with two. 0 disables it, and
            # it needs the persistent server, which is where the phonemizer lives.
            "emphasis_lengthen": 0,
            # Kokoro's own within-utterance pauses, in seconds (its defaults are 0.25
            # and 0.1). Empty leaves them alone. Distinct from rvc's pause_ms, which is
            # the gap *between* fragments.
            "sentence_pause": "",
            "clause_pause": "",
            "auto_tone": False,    # see openai.auto_tone above -- kokoro has no volume
                                   # knob at all, so only a <tag>'s speed is realized here
            "extra_args": [],
            # Optional: talk to a persistent server that keeps the model loaded, instead
            # of paying model-load cost on every call. Not installed or run by this tool
            # -- see the local-tts-configure skill for the (small, self-written) server
            # script this expects. Empty server_url means "spawn kokoro-tts per call",
            # exactly today's behavior.
            "server_url": "",          # e.g. http://127.0.0.1:8765
            "server_start": "",        # command to launch it if not already reachable
            "server_timeout": 30,      # seconds to wait for it to come up
        },
        "rvc": {
            # rvc-python (https://github.com/daswer123/rvc-python) does audio-to-audio
            # voice conversion only, no text-to-speech -- it is never installed
            # automatically by this tool. Point at the interpreter from whatever venv
            # it was installed into once a human has chosen to install it.
            "python": "",
            # Which provider synthesizes the base voice before conversion. Empty means
            # "whatever the overall default provider is" at the time this runs.
            "base_provider": "",
            "model": "",            # path to a .pth voice model
            "index": "",            # optional .index file, improves quality
            "device": "cpu",
            "pitch": 0,             # semitone shift
            "method": "",           # pitch extraction: harvest, crepe, rmvpe, pm; "" => rvc-python's default
            "index_rate": None,     # None => let rvc-python use its own default
            "protect": None,
            # Per-language conversion overrides, separate from pacing. Requests are
            # isolated so tuning Spanish never changes the next English utterance.
            "conversion": {},         # {"es": {"index_rate": 0.65, "protect": 0.2}}
            "extra_args": [],
            # Optional: talk to a persistent server that keeps the model (and torch
            # itself) loaded, instead of paying that load cost -- seconds, not
            # milliseconds -- on every call. The model/index the server holds is fixed
            # at server startup, not per request: that's the nature of keeping one
            # loaded. Not installed or run by this tool -- see the local-tts-configure
            # skill. Empty server_url means "spawn rvc-python per call", today's default.
            "server_url": "",          # e.g. http://127.0.0.1:8766
            "server_start": "",        # command to launch it if not already reachable
            "server_timeout": 60,      # seconds to wait -- a cold torch+model load is slow
            # A multi-model server holds several voices resident at once and picks one
            # per request, so a second language no longer costs a second server (or a
            # second copy of torch in memory). `server_models` maps the name the server
            # was started with to the files behind it -- local-tts never loads these
            # itself, it only names one in the request and reports them in `tts check`.
            # How each language is delivered, over the built-in defaults (see
            # providers/rvc.py DELIVERY_DEFAULTS). "*" applies to any language not named.
            #   speed          rate multiplier, folded into the base provider's own
            #                  rate control alongside a <tag>'s speed
            #   pause_ms       silence between fragments delivered the same way
            #   pause_tone_ms  silence where the tone changes -- the breath a speaker
            #                  takes when the delivery shifts
            # Spanish runs faster with shorter gaps than English, which is why this is
            # per-language rather than one number.
            #   trim_ms        silence left at each fragment edge before the pause is
            #                  applied, so pause_ms actually controls the rhythm rather
            #                  than being added on top of each fragment's own dead air
            "delivery": {
                "es": {"speed": 1.0, "pause_ms": 45, "pause_tone_ms": 130},
                "en": {"speed": 1.0, "pause_ms": 60, "pause_tone_ms": 160},
            },
            "server_models": {},       # {"jarvis": {"model": "...pth", "index": "...index"}}
            # Which of those names to ask for. `language_models` wins when the call has
            # a --lang; `server_model` is the fallback for everything else. Both empty
            # means "whatever the server loaded first", i.e. today's single-model
            # behavior, so an existing setup keeps working untouched.
            "server_model": "",        # e.g. "jarvis"
            "language_models": {},     # {"es": "cortana-es", "en": "jarvis"}
        },
        "command": {
            # Escape hatch: any CLI that writes a wav file.
            # {text} and {output} are substituted as single argv items.
            "template": "espeak-ng -w {output} {text}",
            # Whether local-tts may reshape what the command produced (a <tag>'s speed
            # and volume, applied to the rendered wav). False by default: the command is
            # somebody else's script and may already be acting on the tone itself, and
            # post-processing audio it deliberately shaped would fight it. Every other
            # backend's capabilities are known here, so only this one has the choice.
            "audio_fx": False,
            # local-tts can't know whether an arbitrary command understands <tag> markup,
            # so this is a plain user choice, not an auto-detected capability (see
            # CommandProvider.supports_tone_tags): "strip" (default) removes <tag>s before
            # {text} is filled in, same as every provider with no real tone hook; "pass"
            # leaves them in verbatim, for a custom script written to parse them itself.
            "tone_tags": "strip",
        },
    },
}


TOP_LEVEL_KEYS = ("provider", "play", "player", "terminal_title", "stream",
                  "player_args", "player_env", "ending_silence_ms", "pronunciations",
                  "cache_enabled", "cache_ttl_hours", "cache_max_mb", "cache_dir", "cache_revision",
                  "cache_policy", "warm_keep_alive_seconds", "warm_interval_seconds", "tui_refresh_seconds",
                  "phonetics_hooks", "phonetics_hook_timeout")
#: Top-level settings that are maps, so `--set` takes one more level:
#: `player_args.ffplay="-af aresample=48000"`, `player_env.SDL_AUDIODRIVER=pulseaudio`.
TOP_LEVEL_MAPS = ("player_args", "player_env", "pronunciations")
LANGUAGE_KEYS = ("provider", "voice")


def config_root():
    """Per-platform config directory: %APPDATA% on Windows, ~/.config elsewhere.

    $XDG_CONFIG_HOME wins everywhere when it is set, so a user can keep one location
    across machines.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata)
    return Path.home() / ".config"


def config_dir():
    return config_root() / APP_NAME


def config_path():
    override = os.environ.get(ENV_PREFIX + "CONFIG")
    return Path(override).expanduser() if override else config_dir() / "config.json"


def _deep_merge(base, override):
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce(raw, current):
    """Turn a string from the environment or --set into the right type."""
    if isinstance(current, bool):
        value = raw.strip().lower()
        if value not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
            raise TTSError("expected true or false, got %r" % raw)
        return value in ("1", "true", "yes", "on")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw.split()
        return parsed if isinstance(parsed, list) else [parsed]
    if isinstance(current, dict):
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise TTSError("expected JSON for a map setting, got %r -- or set one entry "
                           "at a time with <key>.<name>=<value>" % raw)
        if not isinstance(parsed, dict):
            raise TTSError("expected a JSON object, got %r" % raw)
        return parsed
    return raw


def _apply_env(cfg):
    """LOCALTTS_PROVIDER, LOCALTTS_PLAY, LOCALTTS_<PROVIDER>_<KEY>."""
    for env_key, raw in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        suffix = env_key[len(ENV_PREFIX):].lower()
        if suffix in TOP_LEVEL_KEYS:
            cfg[suffix] = _coerce(raw, DEFAULTS[suffix])
            continue
        if suffix.startswith("lang_"):
            code = suffix[len("lang_"):].replace("_", "-")
            if code:
                cfg.setdefault("languages", {})[code] = parse_language_value(raw)
            continue
        for provider, settings in cfg["providers"].items():
            prefix = provider + "_"
            if suffix.startswith(prefix):
                key = suffix[len(prefix):]
                if key in settings:
                    settings[key] = _coerce(raw, settings[key])
                break
    return cfg


def load():
    cfg = deepcopy(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise TTSError("invalid JSON in %s: %s" % (path, exc))
        if not isinstance(user, dict):
            raise TTSError("config in %s must be a JSON object" % path)
        cfg = _deep_merge(cfg, user)
    validate_structure(cfg)
    try:
        cfg = _apply_env(cfg)
    except (ValueError, TypeError) as exc:
        raise TTSError("invalid environment setting: %s" % exc)
    validate(cfg)
    return cfg


def normalize_language(code):
    """'es-MX', 'es_MX', 'ES' -> ('es-mx', 'es'): the exact tag and its base."""
    tag = str(code or "").strip().lower().replace("_", "-")
    return tag, tag.split("-")[0]


def language_entry(cfg, code):
    """The {provider, voice} recorded for a language, matching 'es-MX' before 'es'."""
    languages = cfg.get("languages") or {}
    exact, base = normalize_language(code)
    for key in (exact, base):
        for stored, entry in languages.items():
            if normalize_language(stored)[0] == key:
                return entry
    return None


def parse_language_value(raw):
    """Accept 'piper' or 'piper:/path/to/voice.onnx'."""
    provider, _, voice = str(raw).partition(":")
    provider = provider.strip()
    if provider and provider not in DEFAULTS["providers"]:
        raise TTSError(
            "unknown provider %r (available: %s)" % (provider, ", ".join(sorted(DEFAULTS["providers"])))
        )
    entry = {"provider": provider} if provider else {}
    if voice.strip():
        entry["voice"] = voice.strip()
    return entry


def read_user_config():
    """The on-disk config only, without defaults or environment applied."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise TTSError("invalid JSON in %s: %s" % (path, exc))


def set_values(assignments):
    from localtts import lock
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+b") as handle:
        lock.acquire(handle)
        try:
            return _set_values_unlocked(assignments)
        except (ValueError, TypeError) as exc:
            raise TTSError("invalid setting: %s" % exc)
        finally:
            lock.release(handle)


def _set_values_unlocked(assignments):
    """Persist "key=value" or "provider.key=value" pairs. Returns the new config."""
    user = read_user_config()
    for item in assignments:
        if "=" not in item:
            raise TTSError("expected key=value, got %r" % item)
        key, raw = item.split("=", 1)
        key = key.strip()
        if key.startswith("languages."):
            rest = key[len("languages."):]
            code, _, field = rest.partition(".")
            if not code:
                raise TTSError("expected languages.<code>=<provider>[:<voice>]")
            bucket = user.setdefault("languages", {}).setdefault(code, {})
            if field:
                if field not in LANGUAGE_KEYS:
                    raise TTSError(
                        "unknown language key %r (valid: %s)" % (field, ", ".join(LANGUAGE_KEYS))
                    )
                if field == "provider" and raw and raw not in DEFAULTS["providers"]:
                    raise TTSError("unknown provider %r" % raw)
                bucket[field] = raw
            else:
                bucket.clear()
                bucket.update(parse_language_value(raw))
            if not bucket:
                user["languages"].pop(code, None)
            continue
        if "." in key and key.split(".", 1)[0] in TOP_LEVEL_MAPS:
            head, _, entry = key.partition(".")
            if not entry:
                raise TTSError("expected %s.<name>=<value>" % head)
            bucket = user.setdefault(head, {})
            if not isinstance(bucket, dict):
                bucket = {}
                user[head] = bucket
            if raw == "":
                bucket.pop(entry, None)          # empty value removes the entry
            elif head == "player_args":
                bucket[entry] = _coerce(raw, [])
            else:
                bucket[entry] = raw
            continue
        if "." in key:
            provider, sub = key.split(".", 1)
            if provider not in DEFAULTS["providers"]:
                raise TTSError("unknown provider %r" % provider)
            known = DEFAULTS["providers"][provider]
            # A dict-valued setting (rvc.language_models, rvc.server_models) takes one
            # more level, so a single entry can be set without rewriting the whole map
            # as JSON: `rvc.language_models.es=cortana-es`. Setting it to an empty value
            # removes that entry rather than storing a blank one.
            entry = ""
            if sub not in known and "." in sub:
                head, _, entry = sub.partition(".")
                if head in known and not isinstance(known[head], dict):
                    raise TTSError(
                        "%s.%s is a single value, not a map -- use %s.%s=<value>"
                        % (provider, head, provider, head)
                    )
                if isinstance(known.get(head), dict):
                    if not entry:
                        raise TTSError("expected %s.%s.<name>=<value>" % (provider, head))
                    sub = head
                else:
                    entry = ""          # unknown head: fall through to the normal error
            if sub not in known:
                raise TTSError(
                    "unknown key %r for provider %r (valid: %s)"
                    % (sub, provider, ", ".join(sorted(known)))
                )
            bucket = user.setdefault("providers", {}).setdefault(provider, {})
            if entry and sub in ("delivery", "conversion") and "." in entry:
                language, field = entry.split(".", 1)
                if sub == "delivery":
                    from localtts.providers.rvc import DELIVERY_DEFAULTS
                    fields = DELIVERY_DEFAULTS
                else:
                    fields = {"pitch": 0, "index_rate": 0.0, "protect": 0.0}
                if field not in fields:
                    raise TTSError("unknown %s field %r" % (sub, field))
                target = bucket.setdefault(sub, {}).setdefault(language, {})
                if not isinstance(target, dict):
                    raise TTSError("%s.%s.%s must be an object" % (provider, sub, language))
                if raw == "": target.pop(field, None)
                else: target[field] = _coerce(raw, fields[field])
                continue
            if entry:
                target = bucket.setdefault(sub, {})
                if not isinstance(target, dict):
                    target = {}
                    bucket[sub] = target
                if raw == "":
                    target.pop(entry, None)
                elif raw.lstrip()[:1] in ("{", "["):
                    # A nested value (rvc.delivery.es={"speed": 1.0, ...}) would otherwise
                    # be stored as its own JSON text. Only braces/brackets are parsed, so
                    # an ordinary value like `es=cortana-es` -- or one that merely looks
                    # numeric -- still stays the string it was typed as.
                    try:
                        target[entry] = json.loads(raw)
                    except ValueError:
                        raise TTSError("%s looks like JSON but does not parse: %s"
                                       % (key, raw))
                else:
                    target[entry] = raw
            else:
                bucket[sub] = _coerce(raw, known[sub])
        else:
            if key not in TOP_LEVEL_KEYS:
                raise TTSError(
                    "unknown key %r (use %s, or <provider>.<key>)"
                    % (key, ", ".join(TOP_LEVEL_KEYS))
                )
            user[key] = _coerce(raw, DEFAULTS[key])

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    validate(_deep_merge(DEFAULTS, user))
    fd, pending = tempfile.mkstemp(prefix=".local-tts-config-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(user, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        if os.path.exists(pending): os.unlink(pending)
    return user


coerce = _coerce  # public alias for the CLI


#: Providers that started life as `command` templates before local-tts supported them
#: natively. Detected by a substring in the template's binary name or arguments; flags
#: are mapped to the equivalent native provider's own setting keys. Extend this when a
#: new provider is added that people would plausibly have wired through `command` first.
MIGRATIONS = {
    "kokoro": {
        "match": ("kokoro-tts", "kokoro_cli"),
        "flags": {"-v": "voice", "--voice": "voice", "-l": "lang", "--lang": "lang",
                  "-s": "speed", "--speed": "speed"},
    },
    "rvc": {
        "match": ("rvc_python", "rvc-python"),
        "flags": {"-mp": "model", "--model": "model", "-ip": "index", "--index": "index",
                  "-de": "device", "--device": "device", "-pi": "pitch", "--pitch": "pitch",
                  "-me": "method", "--method": "method"},
    },
}


def detect_migrations(cfg):
    """Look at the configured `command.template` for a tool local-tts now supports as a
    real provider. Returns a list of {"provider", "reason", "sets"} -- informational
    only, nothing here writes anything; the caller decides whether to apply `sets` via
    set_values() after asking. `sets` maps "<provider>.<key>" to the value found in the
    template, and always includes "provider" only when the migrated provider is the one
    currently active (so a suggestion never implies switching the default unprompted).
    """
    import shlex

    template = ((cfg.get("providers") or {}).get("command") or {}).get("template") or ""
    if not template:
        return []
    try:
        tokens = shlex.split(template)
    except ValueError:
        return []
    if not tokens:
        return []

    haystack = " ".join(tokens).lower()
    found = []
    for provider_name, spec in MIGRATIONS.items():
        if not any(marker in haystack for marker in spec["match"]):
            continue
        sets = {}
        for index, token in enumerate(tokens):
            key = spec["flags"].get(token)
            if key and index + 1 < len(tokens):
                value = tokens[index + 1]
                if value not in ("{text}", "{output}"):
                    sets["%s.%s" % (provider_name, key)] = value
        found.append({
            "provider": provider_name,
            "reason": "command.template runs %r, which local-tts now supports natively"
                      % os.path.basename(tokens[0]),
            "sets": sets,
            "was_default": cfg.get("provider") == "command",
        })
    return found


def validate(cfg):
    """Shared validation for runtime controls edited by the CLI or terminal UI."""
    import math
    validate_structure(cfg)
    for key, low, high in (("ending_silence_ms", 0, 5000),
                           ("cache_ttl_hours", .001, 87600),
                           ("cache_max_mb", .001, 1048576),
                           ("warm_keep_alive_seconds", 0, 86400),
                           ("warm_interval_seconds", .1, 60),
                           ("tui_refresh_seconds", .1, 60)):
        try:
            value = float(cfg[key])
            if not math.isfinite(value) or not low <= value <= high: raise ValueError
        except (ValueError, TypeError):
            raise TTSError("%s must be between %s and %s" % (key, low, high))
    if cfg["cache_policy"] not in ("lfu", "lru"):
        raise TTSError("cache_policy must be lfu or lru")


def validate_structure(cfg):
    for key in ("providers", "languages", "pronunciations", "player_args", "player_env"):
        if not isinstance(cfg.get(key, {}), dict):
            raise TTSError("%s must be a JSON object" % key)
    for language, entry in cfg.get("languages", {}).items():
        if not isinstance(entry, dict): raise TTSError("languages.%s must be an object" % language)
    for name, options in cfg["providers"].items():
        if not isinstance(options, dict): raise TTSError("providers.%s must be an object" % name)
        for key, default in DEFAULTS["providers"].get(name, {}).items():
            value = options.get(key, default)
            if isinstance(default, dict) and not isinstance(value, dict):
                raise TTSError("%s.%s must be an object" % (name, key))
        for kind in ("delivery", "conversion"):
            for lang, values in (options.get(kind) or {}).items():
                if not isinstance(values, dict):
                    raise TTSError("%s.%s.%s must be an object" % (name, kind, lang))
        if "speed" in options and options["speed"] is not None:
            import math
            try:
                speed = float(options["speed"])
                if not math.isfinite(speed) or speed <= 0: raise ValueError
            except (ValueError, TypeError):
                raise TTSError("%s.speed must be positive" % name)
