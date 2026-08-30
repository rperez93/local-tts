"""Command-line entry point."""

import argparse
import json
import os
import shutil
import sys
import tempfile

from localtts import __version__, audio, config, hooks, providers, skills, text as textutil
from localtts.errors import TTSError

PROG = "tts"
SUBCOMMANDS = ("config", "providers", "check", "languages", "skills", "hooks",
               "playback", "stop", "pause", "resume")

#: Environment variables known to hold a stable per-run session id, checked in order.
#: Verified against a live capture: Claude Code's own status-line JSON payload carries
#: the exact same value as this env var (see AGENT_INSTALL.md). Add an entry here only
#: once confirmed the same way for another host -- an unverified guess would silently
#: mis-scope playback state instead of falling back to the safe global default.
SESSION_ENV_VARS = ("CLAUDE_CODE_SESSION_ID",)


def _session_from_env():
    for name in SESSION_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _resolve_session(explicit, stdin_json=None):
    """Priority: an explicit --session flag, then a session_id embedded in JSON piped on
    stdin (what a status-line hook's host feeds it), then a known host env var, then None
    -- which means "the single global slot", exactly the pre-multi-session behavior."""
    if explicit:
        return explicit
    if stdin_json:
        try:
            data = json.loads(stdin_json)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("session_id", "sessionId"):
                if data.get(key):
                    return data[key]
    return _session_from_env()


def _speak_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Speak text with a local TTS model (kokoro by default).",
        epilog=(
            "subcommands:\n"
            "  %(prog)s providers            list available backends\n"
            "  %(prog)s languages            show which backend speaks which language\n"
            "  %(prog)s skills               install agent skills for this CLI\n"
            "  %(prog)s hooks                install a status-bar hook (fewer chat messages)\n"
            "  %(prog)s stop | pause | resume control background playback\n"
            "  %(prog)s check                verify backends and audio players\n"
            "  %(prog)s config --show        print the effective configuration\n"
            "  %(prog)s config --set k=v     persist a setting\n"
            "\nexamples:\n"
            "  %(prog)s \"hello world\"\n"
            "  %(prog)s -o out.wav -f script.txt\n"
            "  echo \"from a pipe\" | %(prog)s\n"
            "  %(prog)s -b --lang es \"en segundo plano\"\n"
            "  %(prog)s --provider openai --voice nova \"hi there\"\n"
        ) % {"prog": PROG},
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("text", nargs="*", help="text to speak (or pipe it on stdin)")
    parser.add_argument("-f", "--file", help="read the text from a file ('-' for stdin)")
    parser.add_argument("--markdown", dest="markdown", action="store_true", default=None,
                        help="strip markdown before speaking (automatic for .md files)")
    parser.add_argument("--no-markdown", dest="markdown", action="store_false",
                        help="speak the input verbatim, markdown included")
    parser.add_argument("-o", "--output", help="write audio here instead of playing it")
    parser.add_argument("-p", "--provider", choices=providers.names(), help="backend to use")
    parser.add_argument("-l", "--lang", metavar="CODE",
                        help="use the backend and voice remembered for this language (see `tts languages`)")
    parser.add_argument("-v", "--voice", help="voice: name (kokoro, openai), .onnx file (piper) or speaker file (llamacpp)")
    parser.add_argument("-m", "--model", help="override the provider's model for this run")
    parser.add_argument("-s", "--set", dest="overrides", action="append", default=[],
                        metavar="KEY=VALUE", help="override a provider setting for this run (repeatable)")
    parser.add_argument("-b", "--background", action="store_true",
                        help="play in the background and return immediately; keeps the file "
                             "and prints its path (control it with `tts stop|pause|resume`)")
    parser.add_argument("--session", metavar="ID",
                        help="scope background playback to this session, so concurrent "
                             "sessions don't stop each other's audio or share one status-bar "
                             "entry (default: autodetected from the environment, see "
                             "`tts hooks`; falls back to one shared slot)")
    parser.add_argument("--play", action="store_true", help="play the audio even when --output is given")
    parser.add_argument("--no-play", action="store_true", help="never play, just report the file path")
    parser.add_argument("--player", help="playback command to use (default: autodetect)")
    parser.add_argument("--stream", dest="stream", action="store_true", default=None,
                        help="start playing the first fragment while the rest is still "
                             "being synthesized (default; see the `stream` setting)")
    parser.add_argument("--no-stream", dest="stream", action="store_false",
                        help="synthesize the whole text first, then play one joined file")
    parser.add_argument("--keep", action="store_true", help="keep the temporary file and print its path")
    parser.add_argument("--dry-run", action="store_true", help="print the command that would run, then exit")
    parser.add_argument("--verbose", action="store_true", help="show the backend's own output")
    parser.add_argument("--version", action="version", version="%s %s" % (PROG, __version__))
    return parser


def _read_text(args):
    """Return the text to speak, and whether it came from a markdown source."""
    if args.file:
        if args.file == "-":
            return sys.stdin.read(), False
        path = os.path.expanduser(args.file)
        if not os.path.exists(path):
            raise TTSError("input file not found: %s" % path)
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read(), textutil.looks_like_markdown(path)
    if args.text:
        return " ".join(args.text), False
    if not sys.stdin.isatty():
        return sys.stdin.read(), False
    raise TTSError("no text given; pass it as an argument, with --file, or on stdin")


def _apply_overrides(provider, args):
    for item in args.overrides:
        if "=" not in item:
            raise TTSError("expected KEY=VALUE, got %r" % item)
        key, raw = item.split("=", 1)
        key = key.strip()
        # Accept both "threads=8" and "llamacpp.threads=8".
        if "." in key:
            target, key = key.split(".", 1)
            if target != provider.name:
                raise TTSError("--set %s.%s does not apply to provider %r" % (target, key, provider.name))
        defaults = config.DEFAULTS["providers"][provider.name]
        if key not in defaults:
            raise TTSError(
                "unknown setting %r for %s (valid: %s)" % (key, provider.name, ", ".join(sorted(defaults)))
            )
        provider.settings[key] = config.coerce(raw, defaults[key])

    if args.model:
        provider.settings["model"] = args.model


def _resolve_language(cfg, args):
    """Pick the backend (and voice) for --lang, without overriding explicit flags."""
    name = args.provider or cfg["provider"]
    voice = args.voice
    if not args.lang:
        return name, voice

    entry = config.language_entry(cfg, args.lang)
    if entry is None:
        known = ", ".join(sorted(cfg.get("languages") or {})) or "none recorded yet"
        raise TTSError(
            "no backend remembered for language %r (known: %s). Record one with "
            "`%s languages --set %s=<provider>[:<voice>]`."
            % (args.lang, known, PROG, args.lang)
        )
    recorded = entry.get("provider")
    if not args.provider and recorded:
        name = recorded
    # A voice is provider-specific: a piper .onnx means nothing to llama.cpp. Only apply
    # the recorded voice when the backend actually in use is the one it was recorded for.
    if not args.voice and entry.get("voice") and (not recorded or recorded == name):
        voice = os.path.expanduser(entry["voice"])
    return name, voice


def speak(argv):
    args = _speak_parser().parse_args(argv)
    cfg = config.load()

    text, is_markdown = _read_text(args)
    if args.markdown or (args.markdown is None and is_markdown):
        text = textutil.strip_markdown(text)
    text = text.strip()
    # Before anything else looks at the words: every backend benefits, --dry-run shows
    # what will actually be spoken, and a provider composing another (rvc) passes the
    # rewritten text down rather than each layer having to remember to do this.
    text = textutil.apply_pronunciations(text, cfg.get("pronunciations"), args.lang or "")
    if not text:
        raise TTSError("nothing to speak: the input was empty")

    name, voice = _resolve_language(cfg, args)
    provider = providers.build(name, cfg, verbose=args.verbose, lang=args.lang or "")
    _apply_overrides(provider, args)

    if args.output:
        out_path = os.path.expanduser(args.output)
        parent = os.path.dirname(os.path.abspath(out_path))
        if not os.path.isdir(parent):
            raise TTSError("output directory does not exist: %s" % parent)
        suffix = os.path.splitext(out_path)[1].lstrip(".").lower()
        if suffix and provider.name != "openai" and suffix != provider.default_format:
            raise TTSError(
                "%s can only write .%s files (got %s)" % (provider.name, provider.default_format, args.output)
            )
        temporary = False
    else:
        handle, out_path = tempfile.mkstemp(prefix="local-tts-", suffix="." + provider.default_format)
        os.close(handle)
        temporary = True

    args.voice = voice
    if args.dry_run:
        # A provider that can't act on <tag> tone tags (text.tone_segments()) never sees
        # them at runtime either -- synthesize_chunked() strips them first. Mirror that
        # here so --dry-run shows the words that actually get spoken/passed, not literal
        # markup that's really about to be stripped. rvc composes another provider, so
        # what matters is whether *that one* supports tags, not rvc itself (rvc never
        # touches text -- see RvcProvider.synthesize()).
        dry_base = provider.base_provider_instance() if provider.name == "rvc" else None
        tag_aware = dry_base.supports_tone_tags if dry_base else provider.supports_tone_tags
        chunk_text = text if tag_aware else textutil.strip_tone_tags(text)
        pieces = textutil.chunks(chunk_text, provider.max_words)
        if len(pieces) > 1:
            print("# %d words -> %d chunks of <=%d words, joined into one file"
                  % (len(chunk_text.split()), len(pieces), provider.max_words))
        server_url = provider.settings.get("server_url")
        if provider.name == "rvc":
            # rvc has no single command: it synthesizes with another provider first,
            # then converts the result. build_command(wav_in, out_path) only covers the
            # second half -- showing that alone against a positional text arg would
            # silently pass the text itself as if it were an input wav path.
            base = dry_base
            print("# step 1: synthesize the base voice with %s" % base.name)
            base_builder = getattr(base, "build_command", None)
            if base_builder is None:
                print("# %s runs no external command; it POSTs to %s"
                      % (base.name, base.settings.get("base_url")))
            else:
                try:
                    base_cmd = base_builder(pieces[0], "<base-voice.wav>", args.voice)
                except TypeError:
                    base_cmd = base_builder(pieces[0], "<base-voice.wav>")
                print(" ".join(base_cmd))
            print("# step 2: convert to the target voice")
            if server_url:
                print("# POST %s/convert (auto-starts via rvc.server_start if not "
                      "already running)" % server_url)
            else:
                print(" ".join(provider.build_command("<base-voice.wav>", out_path)))
        elif server_url:
            print("# %s: POST %s/synthesize (auto-starts via %s.server_start if not "
                  "already running)" % (provider.name, server_url, provider.name))
        elif provider.name in ("openai", "piper", "kokoro"):
            # Each of these can realize a <tag>/auto_tone-driven segment in its own way
            # (openai: the "instructions" field [+ speed]; piper/kokoro: speed, and piper
            # also volume -- see their own supports_tone_tags docstrings) -- so unlike the
            # generic branch below, more than one segment means more than one call.
            def _describe(profile):
                if not profile:
                    return "(none)"
                bits = [repr(profile["instructions"])] if profile.get("instructions") else []
                if profile["speed"] != 1.0:
                    bits.append("speed x%.2f" % profile["speed"])
                if profile.get("volume", 1.0) != 1.0:
                    bits.append("volume x%.2f" % profile["volume"])
                return ", ".join(bits) or "(none)"

            if provider.name == "openai":
                segments = provider.resolved_segments(text)
            else:
                segments = textutil.resolve_tone_segments(
                    text, auto_tone=bool(provider.settings.get("auto_tone")))

            if len(segments) == 1 and segments[0][1] is None:
                chunk = segments[0][0]
                if provider.name == "openai":
                    print("# POSTs to %s" % provider.settings.get("base_url"))
                else:
                    print(" ".join(provider.build_command(chunk, out_path, args.voice)))
                    if provider.name == "piper":
                        print("# (the text is piped to stdin, not passed as an argument)")
            else:
                print("# %d tone segments -> %d calls, joined%s:"
                      % (len(segments), len(segments), " as wav" if provider.name == "openai" else ""))
                for chunk, profile in segments:
                    label = chunk if len(chunk) <= 60 else chunk[:57] + "..."
                    print("#   [%s] %s" % (_describe(profile), label))
        else:
            builder = getattr(provider, "build_command", None)
            if builder is None:
                print("# %s runs no external command; it POSTs to %s"
                      % (provider.name, provider.settings.get("base_url")))
            else:
                try:
                    cmd = builder(pieces[0], out_path, args.voice)
                except TypeError:
                    cmd = builder(pieces[0], out_path)
                print(" ".join(cmd))
                if provider.name == "piper":
                    print("# (the text is piped to stdin, not passed as an argument)")
        if len(pieces) > 1:
            print("# ... and %d more like it" % (len(pieces) - 1))
        if temporary:
            os.unlink(out_path)
        return 0

    should_play = not args.no_play and (
        args.play or args.background or (temporary and cfg["play"]))
    session = _resolve_session(args.session) if should_play else None
    stream_on = cfg.get("stream", True) if args.stream is None else args.stream
    # Streaming hands the player one fragment file at a time, so it needs a format every
    # installed player can open standalone -- true of wav, not of a compressed stream
    # (Windows' built-in player cannot take mp3 at all).
    use_stream = should_play and stream_on and provider.default_format == "wav"

    sink = _StreamSink(audio.stream_new()) if use_stream else None
    runner = None
    try:
        if sink:
            # Started *before* synthesis so it is already queueing for the playback lock
            # while the first fragment is still being made.
            runner_pid, runner = audio.play_stream_detached(
                sink.directory, args.player or cfg["player"], verbose=args.verbose,
                session=session, title=bool(cfg.get("terminal_title", True)),
                producer_pid=os.getpid(), label=out_path)
            if runner is None:
                audio.stream_cleanup(sink.directory)   # no player at all -- fall through
                sink = None
            else:
                provider.on_part = sink.add

        try:
            _synthesize(provider, text, out_path, args)
        finally:
            provider.on_part = None
            if sink:
                # Also on failure: the runner must play whatever was rendered before the
                # error and then stop, rather than wait on a producer that has given up.
                if not sink.count and os.path.exists(out_path):
                    sink.add(out_path)     # one part only -- stream it as a stream of one
                sink.finish()

        played = False
        if sink:
            played = True
            length = audio._safe_duration(out_path)
            if args.background:
                print("playing in the background (pid %d, %s) — `%s stop` to end it, "
                      "`%s playback` for progress"
                      % (runner_pid, audio.format_time(length), PROG, PROG), file=sys.stderr)
                # The file outlives this process, so it must not be deleted on the way out.
                temporary = False
            else:
                try:
                    runner.wait()          # blocking playback, same as audio.play()
                except KeyboardInterrupt:
                    audio.stop_playback(session)
        elif should_play and args.background:
            pid, length = audio.play_detached(out_path, args.player or cfg["player"],
                                              verbose=args.verbose, session=session,
                                              title=bool(cfg.get("terminal_title", True)))
            played = pid is not None
            if played:
                # `tts stop` (no flag) resolves the same session automatically as long as
                # it runs in the same environment -- that's the common case, so the hint
                # stays simple rather than repeating a session id back at the caller.
                print("playing in the background (pid %d, %s) — `%s stop` to end it, "
                      "`%s playback` for progress"
                      % (pid, audio.format_time(length), PROG, PROG), file=sys.stderr)
            # The file outlives this process, so it must not be deleted on the way out.
            temporary = False
        elif should_play:
            played = audio.play(out_path, args.player or cfg["player"], verbose=args.verbose,
                                title=bool(cfg.get("terminal_title", True)))

        if should_play and not played:
            print("no audio player found (tried: %s). Install ffmpeg, or use --output."
                  % ", ".join(name for name, _ in audio.PLAYERS), file=sys.stderr)

        if not temporary:
            print(out_path)
        elif args.keep or not played:
            print(out_path)
            temporary = False
    finally:
        if temporary and os.path.exists(out_path):
            os.unlink(out_path)
    return 0


class _StreamSink:
    """Publishes finished fragments into a stream directory, numbering them in the order
    they arrive. Providers call this through Provider.emit_part(); see audio.stream_add.
    """

    def __init__(self, directory):
        self.directory = directory
        self.count = 0

    def add(self, path):
        audio.stream_add(self.directory, self.count, path)
        self.count += 1

    def finish(self):
        audio.stream_finish(self.directory, self.count)


def _synthesize(provider, text, out_path, args):
    """Thin wrapper over text.synthesize_chunked() that prints chunk progress to stderr --
    the chunking/concurrency logic itself is shared with providers that compose another
    provider internally (rvc), so it lives in text.py, not here."""
    def report(done, total):
        print("  chunk %d/%d" % (done, total), end="\r", file=sys.stderr, flush=True)

    pieces = textutil.chunks(text, provider.max_words)
    result = textutil.synthesize_chunked(provider, text, out_path, voice=args.voice,
                                         on_progress=report if len(pieces) > 1 else None)
    if len(pieces) > 1:
        print(" " * 24, end="\r", file=sys.stderr)
    return result


def list_providers(argv):
    parser = argparse.ArgumentParser(prog="%s providers" % PROG, description="List the available backends.")
    parser.parse_args(argv)
    cfg = config.load()
    for name in providers.names():
        marker = "*" if name == cfg["provider"] else " "
        print("%s %-9s %s" % (marker, name, providers.DESCRIPTIONS.get(name, "")))
    print("\n* = current default (change with `%s config --set provider=<name>`)" % PROG)
    return 0


def languages(argv):
    parser = argparse.ArgumentParser(
        prog="%s languages" % PROG,
        description="Remember which backend and voice to use per language.",
        epilog=("examples:\n"
                "  %(prog)s --set es=piper:~/voices/es_MX-claude-high.onnx\n"
                "  %(prog)s --set en=llamacpp\n"
                "  %(prog)s --forget es\n") % {"prog": "%s languages" % PROG},
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--set", dest="assignments", action="append", default=[],
                        metavar="CODE=PROVIDER[:VOICE]",
                        help="record a language preference (repeatable)")
    parser.add_argument("--forget", action="append", default=[], metavar="CODE",
                        help="drop a recorded language (repeatable)")
    args = parser.parse_args(argv)

    if args.assignments or args.forget:
        config.set_values(
            ["languages.%s" % item for item in args.assignments]
            + ["languages.%s=" % code for code in args.forget]
        )
        print("updated %s" % config.config_path())

    recorded = config.load().get("languages") or {}
    if not recorded:
        print("no language preferences recorded yet.\n"
              "Record one with `%s languages --set es=piper:/path/to/voice.onnx`." % PROG)
        return 0
    width = max(len(code) for code in recorded)
    for code in sorted(recorded):
        entry = recorded[code] or {}
        voice = entry.get("voice")
        print("  %-*s  %-9s %s" % (width, code, entry.get("provider") or "-",
                                   os.path.basename(voice) if voice else ""))
    print("\nUse with: %s --lang <code> \"...\"" % PROG)
    return 0


def check(argv):
    parser = argparse.ArgumentParser(prog="%s check" % PROG, description="Verify backends and audio players.")
    parser.parse_args(argv)
    cfg = config.load()

    print("config file : %s%s" % (config.config_path(), "" if config.config_path().exists() else " (not created yet)"))
    print("default     : %s" % cfg["provider"])
    print("")
    ok_default = True
    for name in providers.names():
        provider = providers.build(name, cfg)
        ok, message = provider.check()
        print("[%s] %-9s %s" % ("ok" if ok else "--", name, message))
        if name == cfg["provider"]:
            ok_default = ok
    print("")
    found = audio.available_players()
    chosen = cfg.get("player") or (found[0] if found else "")
    print("players     : %s%s" % (", ".join(found) if found else "none found (install ffmpeg for ffplay)",
                                  "  -> using %s" % chosen if chosen and len(found) > 1 else ""))
    tuning = ["%s %s" % (name, " ".join(str(a) for a in args))
              for name, args in sorted((cfg.get("player_args") or {}).items()) if args]
    tuning += ["%s=%s" % (key, value)
               for key, value in sorted((cfg.get("player_env") or {}).items())]
    if tuning:
        print("player tuning: %s" % "; ".join(tuning))
    print("tone shaping: %s" % _tone_shaping_status())
    print("phonetics   : %s" % _phonetics_status(cfg))
    print("transcriber : %s" % _transcriber_status())
    print("streaming   : %s" % ("on -- each fragment plays as it is synthesized"
                                if cfg.get("stream", True) else
                                "off (`%s config --set stream=true` to play as it renders)" % PROG))
    return 0 if ok_default else 1


def _transcriber_status():
    """Which transcriber local-tts itself has, which is not the same question as which
    backend can be handed phonemes.

    Worth printing because the two are easy to confuse: a real phonemizer here means
    every language espeak knows can be transcribed, while the rules cover only the
    languages someone wrote them for.
    """
    from localtts import g2p
    if g2p.using_library():
        return "espeak, through the `phonemes` extra -- every language it knows"
    languages = ", ".join(g2p.supported()) or "none"
    return ("built-in rules only (%s); `pip install -e \".[phonemes]\"` adds espeak "
            "and every language it knows" % languages)


def _phonetics_status(cfg):
    """Whether the dictionary's IPA entries can actually reach the model.

    Reported rather than left to be discovered, because a `/…/` entry that silently
    does nothing is the worst kind of setting: the word still gets said, just the wrong
    way, and nothing anywhere says why. local-tts has no runtime dependencies and cannot
    transcribe text itself, so it can only pass the table to a backend that has a
    phonemizer of its own -- and it asks that backend rather than assuming, because a
    server that is merely configured, or is an older copy of the script, would take the
    table and drop it.
    """
    entries = cfg.get("pronunciations") or {}
    # Distinct words, not table keys: "pull request" and "es:pull request" are one word
    # said two ways, and no single call ever sees both -- `check` has no --lang, so it
    # cannot resolve which, and reporting "2" would match no call that ever runs.
    phonetic = {str(key).partition(":")[2].strip().lower() or str(key).strip().lower()
                for key, value in entries.items() if textutil.is_phonetic(value)}
    if not phonetic:
        return "no /IPA/ entries in `pronunciations` (plain respellings work everywhere)"

    able, unable = [], []
    for name in providers.names():
        try:
            instance = providers.build(name, cfg)
        except Exception:
            continue
        (able if getattr(instance, "supports_phonetics", False) else unable).append(name)
    if not able:
        return ("%d word(s) with /IPA/ in the table, but no backend here accepts "
                "phonemes right now -- "
                "they are ignored. kokoro's persistent server is the one that can; if "
                "it is configured, check it is running and its script is current."
                % len(phonetic))
    return "%d word(s) with /IPA/ in the table -> %s%s" % (
        len(phonetic), ", ".join(able),
        "; ignored by %s" % ", ".join(unable) if unable else "")

def _tone_shaping_status():
    """Whether a <tag>'s speed change goes through ffmpeg or the built-in fallback.

    Worth a line of its own because the difference is audible, not academic: the
    fallback is a pure-Python WSOLA stretch, good enough for speech but measurably
    noisier than ffmpeg's atempo, and there is no other way for a user to discover
    which one their tagged speech is getting.
    """
    if shutil.which("ffmpeg"):
        return "ffmpeg atempo (best quality)"
    return ("built-in WSOLA -- install ffmpeg for cleaner tagged speech "
            "(<happy>, <sad>, ... change pacing, and ffmpeg does that resampling better)")


def playback_command(argv, action=None):
    parser = argparse.ArgumentParser(
        prog="%s playback" % PROG,
        description="Control audio started with `%s --background`." % PROG,
        epilog=("shortcuts:\n"
                "  %s stop      %s pause      %s resume\n" % (PROG, PROG, PROG)),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="is anything playing? (default)")
    group.add_argument("--stop", action="store_true", help="end playback")
    group.add_argument("--pause", action="store_true", help="suspend playback")
    group.add_argument("--resume", action="store_true", help="continue a paused playback")
    group.add_argument("--compact", action="store_true",
                       help="one short line for a status bar: '' when idle, no trailing "
                            "newline. If JSON with a session_id/sessionId field is piped "
                            "on stdin (what a host feeds its status-line command), that "
                            "session is used automatically -- see `tts hooks`.")
    parser.add_argument("--session", metavar="ID",
                        help="target this session instead of autodetecting one (see "
                             "`tts --session` for what autodetection uses)")
    args = parser.parse_args(argv)

    if action is None and args.compact:
        stdin_json = sys.stdin.read() if not sys.stdin.isatty() else None
        session = _resolve_session(args.session, stdin_json=stdin_json)
        sys.stdout.write(audio.compact_status(session=session))
        return 0

    session = _resolve_session(args.session)
    chosen = action or ("stop" if args.stop else "pause" if args.pause
                        else "resume" if args.resume else "status")
    handler = {
        "stop": audio.stop_playback,
        "pause": audio.pause_playback,
        "resume": audio.resume_playback,
        "status": audio.playback_status,
    }[chosen]
    ok, message = handler(session=session)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok or chosen == "status" else 1


def hooks_command(argv):
    parser = argparse.ArgumentParser(
        prog="%s hooks" % PROG,
        description="Install a status-bar hook so playback progress lives in your coding "
                    "agent's own status bar instead of chat messages.",
        epilog=("Only agents with a real 'run my command, show its stdout in the status "
                "bar' mechanism support this. With no options, prints what was found — "
                "supported agents detected, and why the rest can't do it.\n\n"
                "If a status line is already configured (yours, or another tool's),\n"
                "install NEVER rewrites it -- it appends into the same script file so\n"
                "that tool keeps managing itself exactly as before. Only when nothing was\n"
                "configured does it take the (empty) slot for its own standalone script.\n\n"
                "examples:\n"
                "  %(prog)s                    show detected agents and status\n"
                "  %(prog)s --install          install into every detected supported agent\n"
                "  %(prog)s --install claude-code\n"
                "  %(prog)s --status           is a hook live right now? (used by the skill)\n"
                "  %(prog)s --uninstall\n") % {"prog": "%s hooks" % PROG},
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("agents", nargs="*", metavar="AGENT",
                        help="limit to these agents (default: every detected one); one of: "
                             + ", ".join(sorted(hooks.HOOK_AGENTS)))
    parser.add_argument("--install", action="store_true", help="write the hook")
    parser.add_argument("--uninstall", action="store_true", help="remove it (appended mode: "
                        "just our block, leaving everything else untouched)")
    parser.add_argument("--status", action="store_true",
                        help="print 'active' if any installed hook is being called right "
                             "now by a live session, else 'inactive'; exit 0/1 to match")
    parser.add_argument("--force", action="store_true",
                        help="with --install, replace an existing status-line command that "
                             "can't be safely appended to (not a plain script file). This "
                             "is the fragile chain-by-reference mode -- only use it if the "
                             "existing command has no simpler script file to extend.")
    parser.add_argument("--refresh-interval", type=int, metavar="SECONDS",
                        help="with --install: how often the status bar re-renders on a "
                             "timer. 1-60 for a real timer; 0 for explicitly event-based "
                             "(no timer at all, only redraws on host events). When nothing "
                             "else is configured this already defaults to 2s. When "
                             "appending into an existing status line (e.g. another tool's), "
                             "refreshInterval is left exactly as it was unless you pass "
                             "this -- it also changes how often THAT tool's own script "
                             "re-runs, so it's opt-in there either way.")
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    args = parser.parse_args(argv)

    if sum([args.install, args.uninstall, args.status]) > 1:
        raise TTSError("choose one of --install, --uninstall, --status")
    if args.refresh_interval is not None and not (0 <= args.refresh_interval <= 60):
        raise TTSError("--refresh-interval must be 0 (event-based) or between 1 and 60 seconds")

    if args.status:
        active = hooks.any_active()
        print("active" if active else "inactive")
        return 0 if active else 1

    detected = hooks.detect()
    chosen = args.agents or sorted(detected)
    unknown = [a for a in chosen if a not in hooks.HOOK_AGENTS]
    if unknown:
        reasons = [hooks.UNSUPPORTED.get(a, "unknown agent") for a in unknown]
        raise TTSError("not supported: %s" % "; ".join(
            "%s (%s)" % pair for pair in zip(unknown, reasons)))

    if not args.install and not args.uninstall:
        print("supported : %s" % ", ".join(sorted(hooks.HOOK_AGENTS)))
        print("")
        for name in sorted(hooks.HOOK_AGENTS):
            if name not in detected:
                print("[  ] %-12s agent not detected" % name)
                continue
            installed = hooks.is_installed(name)
            active = hooks.is_active(name)
            state = "active" if active else "installed, not currently running" if installed else "not installed"
            mark = "ok" if active else ("--" if installed else "  ")
            print("[%s] %-12s %s" % (mark, name, state))
        print("")
        for name in sorted(hooks.UNSUPPORTED):
            print("[xx] %-12s not supported: %s" % (name, hooks.UNSUPPORTED[name]))
        print("")
        if detected:
            print("Install with: %s hooks --install" % PROG)
        else:
            print("No supported agent detected on this machine.")
        return 0

    if not chosen:
        raise TTSError("no supported agents detected; pass a name explicitly")

    needs_restart = False
    for name in chosen:
        try:
            if args.install:
                result = hooks.install(name, dry_run=args.dry_run, force=args.force,
                                       refresh_interval=args.refresh_interval)
                verb = "would" if args.dry_run else "did"
                if result["mode"] == "appended":
                    if not result.get("settings_changed"):
                        print("  %-12s %s append into %s -- your existing status line is "
                              "untouched (including its refresh rate), and picks this up "
                              "on its very next refresh"
                              % (name, verb, result["target_file"]))
                    elif result["refresh_interval"] == 0:
                        print("  %-12s %s append into %s and set it to event-based (no "
                              "timer -- also affects the existing command's refresh rate)"
                              % (name, verb, result["target_file"]))
                        needs_restart = True   # refreshInterval IS read at startup
                    else:
                        print("  %-12s %s append into %s and set refreshInterval to %ss "
                              "(also affects the existing command's own refresh rate)"
                              % (name, verb, result["target_file"], result["refresh_interval"]))
                        needs_restart = True
                elif result["mode"] == "standalone":
                    cadence = ("event-based (no timer)" if result["refresh_interval"] == 0
                              else "every %ss" % result["refresh_interval"])
                    print("  %-12s %s write -> %s (%s)" % (
                        name, verb, result["settings_path"], cadence))
                    needs_restart = True
                else:   # forced
                    print("  %-12s %s replace statusLine.command, chaining %r (--force)" % (
                        name, verb, result["chained_from"]))
                    needs_restart = True
            else:
                result = hooks.uninstall(name)
                print("  %-12s %s" % (name, result["detail"]))
        except TTSError as exc:
            print("  %-12s failed: %s" % (name, exc), file=sys.stderr)
    if needs_restart and not args.dry_run:
        print("\nRestart your agent so it picks up the new statusLine setting.")
    return 0


def skills_command(argv):
    parser = argparse.ArgumentParser(
        prog="%s skills" % PROG,
        description="Install the local-tts skills into your coding agents.",
        epilog=("Skills teach an agent to speak to you with this CLI and to configure it.\n"
                "With no options, prints what was detected and what is installed.\n\n"
                "examples:\n"
                "  %(prog)s                      show detected agents and status\n"
                "  %(prog)s --install            install into every detected agent\n"
                "  %(prog)s --install claude-code gemini\n"
                "  %(prog)s --install --all      install even where no agent was detected\n"
                "  %(prog)s --uninstall\n"
                "  %(prog)s --print local-tts-update\n") % {"prog": "%s skills" % PROG},
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("agents", nargs="*", metavar="AGENT",
                        help="limit to these agents (default: every detected one); one of: "
                             + ", ".join(sorted(skills.AGENTS)))
    parser.add_argument("--install", action="store_true", help="write the skills")
    parser.add_argument("--uninstall", action="store_true", help="remove them again")
    parser.add_argument("--all", action="store_true",
                        help="with --install, also target agents that were not detected")
    parser.add_argument("--dry-run", action="store_true", help="show what would be written")
    parser.add_argument("--print", dest="print_skill", metavar="SKILL",
                        help="print one bundled skill's current content to stdout, "
                             "straight from this install -- not the copy in any agent's "
                             "skill directory, which may be stale. For a host that won't "
                             "reliably re-read a skill file mid-session: print it, follow "
                             "the printed text as the instructions for the rest of this "
                             "task, then tell the user to restart so a fresh session picks "
                             "it up normally next time. One of: "
                             + ", ".join(skills.SKILLS))
    args = parser.parse_args(argv)

    if args.print_skill:
        if args.install or args.uninstall:
            raise TTSError("--print cannot be combined with --install or --uninstall")
        if args.print_skill not in skills.SKILLS:
            raise TTSError("unknown skill %r (one of: %s)"
                           % (args.print_skill, ", ".join(skills.SKILLS)))
        sys.stdout.write(skills.read_skill(args.print_skill))
        return 0

    if args.install and args.uninstall:
        raise TTSError("choose either --install or --uninstall, not both")

    detected = skills.detect()
    chosen = args.agents or sorted(detected if not args.all else skills.AGENTS)
    unknown = [a for a in chosen if a not in skills.AGENTS]
    if unknown:
        raise TTSError("unknown agent(s): %s" % ", ".join(unknown))

    if not args.install and not args.uninstall:
        print("bundled skills : %s" % ", ".join(skills.SKILLS))
        print("")
        for name in sorted(skills.AGENTS):
            kind, _, label = skills.AGENTS[name]
            installed, detail = skills.status(name)
            mark = "ok" if installed else ("--" if name in detected else "  ")
            print("[%s] %-12s %-16s %-9s %s"
                  % (mark, name, label, "(%s)" % kind,
                     detail if name in detected else "agent not detected"))
        print("")
        if detected:
            print("Install with: %s skills --install" % PROG)
        else:
            print("No coding agents detected. Use --install --all to write them anyway.")
        return 0

    if not chosen:
        raise TTSError("no coding agents detected; pass names explicitly or use --all")

    action = skills.install if args.install else skills.uninstall
    verb = "would write" if args.dry_run else ("wrote" if args.install else "removed")
    total = 0
    for name in chosen:
        try:
            paths = action(name, dry_run=args.dry_run) if args.install else action(name)
        except TTSError as exc:
            print("  %-12s failed: %s" % (name, exc), file=sys.stderr)
            continue
        if not paths:
            print("  %-12s nothing to remove" % name)
            continue
        for path in paths:
            print("  %-12s %s %s" % (name, verb, path))
            total += 1
    if args.install and not args.dry_run and total:
        print("\nRestart your agent (or start a new session) so it picks the skills up.")
    return 0


def config_command(argv):
    parser = argparse.ArgumentParser(
        prog="%s config" % PROG,
        description="Inspect or change the configuration.",
        epilog=("examples:\n"
                "  %(prog)s --show\n"
                "  %(prog)s --set provider=piper\n"
                "  %(prog)s --detect-migrations\n") % {"prog": "%s config" % PROG},
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--show", action="store_true", help="print the effective configuration")
    parser.add_argument("--path", action="store_true", help="print the config file path")
    parser.add_argument("--init", action="store_true", help="write a config file containing the defaults")
    parser.add_argument("--set", dest="assignments", action="append", default=[], metavar="KEY=VALUE",
                        help="set provider, play, player, or <provider>.<key> (repeatable)")
    parser.add_argument("--detect-migrations", action="store_true",
                        help="check the command.template for a tool local-tts now "
                             "supports as a real provider (e.g. it used to be the only "
                             "way to drive kokoro-tts); prints the tts config --set "
                             "commands that would switch to it. Never applies them.")
    args = parser.parse_args(argv)

    if args.path:
        print(config.config_path())
        return 0

    if args.detect_migrations:
        found = config.detect_migrations(config.load())
        if not found:
            print("nothing to migrate: command.template doesn't match a natively "
                  "supported provider.")
            return 0
        for migration in found:
            print("command.template runs something %s now supports natively as `%s`:"
                  % (PROG, migration["provider"]))
            print("  %s" % migration["reason"])
            for key, value in migration["sets"].items():
                print("  %s config --set %s=%s" % (PROG, key, value))
            if migration["was_default"]:
                print("  %s config --set provider=%s   # command is your current default"
                      % (PROG, migration["provider"]))
            print("")
        print("Nothing has been changed. Run the commands above to switch, or ask "
              "your agent to do it after confirming.")
        return 0

    if args.init:
        path = config.config_path()
        if path.exists():
            raise TTSError("%s already exists" % path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config.DEFAULTS, indent=2) + "\n", encoding="utf-8")
        print("wrote %s" % path)
        return 0

    if args.assignments:
        config.set_values(args.assignments)
        print("updated %s" % config.config_path())
        if not args.show:
            return 0

    print(json.dumps(config.load(), indent=2))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Bare `tts` at a prompt is someone asking what the tool can do, not an empty
    # request. Piped input (`echo hi | tts`) still has no argv, so check the tty.
    if not argv and sys.stdin.isatty():
        _speak_parser().print_help()
        return 0
    try:
        if argv and argv[0] in SUBCOMMANDS:
            handler = {
                "config": config_command,
                "providers": list_providers,
                "check": check,
                "languages": languages,
                "skills": skills_command,
                "hooks": hooks_command,
                "playback": playback_command,
            }.get(argv[0])
            if handler is None:      # stop / pause / resume are shortcuts
                return playback_command(argv[1:], action=argv[0])
            return handler(argv[1:])
        return speak(argv)
    except TTSError as exc:
        print("%s: error: %s" % (PROG, exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
