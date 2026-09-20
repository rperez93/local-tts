"""Kokoro backend: small, fast, open-weight neural TTS (Kokoro-82M).

Targets a minimal `kokoro-tts` CLI wrapper -- `-o/--output`, `-v/--voice`, `-l/--lang`,
`-s/--speed`, text as trailing positional argument(s) -- the shape a simple wrapper
around the `kokoro`/`kokoro-onnx` Python package naturally takes, and the one
`local-tts-configure` sets up. If your own `kokoro-tts` differs, `extra_args` and
`binary` still let you point this provider at it.
"""

import os
import tempfile

from localtts import audio as audiomod
from localtts import text as textutil
from localtts.errors import TTSError
from localtts.providers.base import Provider

#: Only checked when kokoro.model_dir is set. Some kokoro CLIs (nazdridoy/kokoro-tts)
#: resolve these two files relative to their own working directory rather than a flag;
#: a wrapper around the kokoro/kokoro-onnx package directly usually manages its own
#: model path internally and needs neither this setting nor these files checked here.
MODEL_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")

#: Kokoro names every voice <language letter><gender>_<name> (af_heart, ef_dora,
#: bm_george), so the voice itself says which language it speaks. Used to pick the
#: phonemizer language when a voice was chosen per language -- otherwise a Spanish voice
#: could be handed English phonetics, which is exactly the failure the language memory
#: exists to prevent. `kokoro.lang` still wins when set and no per-language voice applies.
VOICE_LANGS = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}


class KokoroProvider(Provider):
    name = "kokoro"
    default_format = "wav"
    #: -s/speed is a real flag (both the subprocess CLI and the server's payload) -- see
    #: kokoro_onnx.Kokoro.create()'s own signature, verified this session. No volume or
    #: pitch knob exists anywhere in kokoro/kokoro_onnx, so a <tag>'s volume multiplier is
    #: simply not realized here (honest silence, not a fabricated flag).
    supports_tone_tags = True
    realizes_speed = True    # -s is real; volume is applied to the rendered
    realizes_volume = False  # segment in synthesize(), since nothing else will

    #: Cached answer from the server's own health endpoint. None means "not asked yet".
    _phonetics_claim = None

    @property
    def supports_phonetics(self):
        """Only through the persistent server, which is where the phonemizer lives --
        the per-call CLI wrapper takes text and has nowhere to put a transcription.

        Asks the server rather than trusting that `server_url` is set. A URL says one
        was written down, and a process answering says nothing about whether it is the
        script this version of the skill installs: an older copy answers /health and
        drops a `phonetics` table it never learned to read, silently. Reporting that as
        working is the exact failure this feature exists to remove.
        """
        url = self.settings.get("server_url")
        if not url:
            return False
        # Asked once per instance. The answer cannot change mid-utterance, and this is
        # read once per fragment while building the payload -- an uncached round trip
        # there would put a call that blocks for up to 2s on the hot path of the very
        # feature that exists to remove per-call overhead.
        if self._phonetics_claim is None:
            claimed = self.server_capabilities(url)
            self._phonetics_claim = bool(claimed and claimed.get("phonetics"))
        return self._phonetics_claim

    def _model_dir(self):
        model_dir = os.path.expanduser(self.settings.get("model_dir") or "")
        if not model_dir:
            return None
        missing = [f for f in MODEL_FILES if not os.path.exists(os.path.join(model_dir, f))]
        if missing:
            raise TTSError("kokoro.model_dir is missing %s (looked in %s)"
                           % (", ".join(missing), model_dir))
        return model_dir

    def resolved_voice(self, voice=None):
        """The voice for this call: an explicit --voice, else this call's language entry
        from `language_voices`, else the flat `voice` setting."""
        if voice:
            return voice
        return (self.for_language(self.settings.get("language_voices") or {})
                or self.settings.get("voice") or "")

    def resolved_lang(self, voice=None):
        """The phonemizer language for this call.

        Derived from the voice's own prefix when the voice came from `language_voices`,
        because picking a per-language voice and then leaving a stale flat `lang` in
        place is how a Spanish voice ends up reading English phonetics. A flat `lang`
        still applies whenever no per-language voice was chosen.
        """
        per_language = self.for_language(self.settings.get("language_voices") or {})
        chosen = voice or per_language
        if chosen and (per_language or not self.settings.get("lang")):
            derived = VOICE_LANGS.get(chosen[:1].lower())
            if derived:
                return derived
        return self.settings.get("lang") or ""

    def speed_settings(self, speed):
        """kokoro's speed is a direct rate multiplier, so it just compounds."""
        return {"speed": float(self.settings.get("speed") or 1.0) * speed}

    def _effective_speed(self, profile):
        """Base `speed` setting times a <tag> profile's own speed multiplier, or just the
        base setting for a plain/neutral segment -- None if that comes out to 1.0 (kokoro's
        own default, flag omitted, exactly today's behavior)."""
        speed = float(self.settings.get("speed") or 1.0) * (profile["speed"] if profile else 1.0)
        return speed if speed != 1.0 else None

    def build_command(self, text, out_path, voice=None, speed=None):
        """`speed`, if given, overrides the configured `speed` setting for this one call
        (a <tag>-adjusted segment); leave it out for the plain, unadjusted case (also what
        --dry-run calls with)."""
        exe = self.resolve_binary("binary", "kokoro-tts")
        cmd = [exe, "-o", out_path]

        chosen_voice = self.resolved_voice(voice)
        if chosen_voice:
            cmd += ["-v", chosen_voice]
        lang = self.resolved_lang(voice)
        if lang:
            cmd += ["-l", lang]
        effective_speed = speed if speed is not None else self.settings.get("speed")
        if effective_speed and float(effective_speed) != 1.0:
            cmd += ["-s", str(effective_speed)]
        cmd += list(self.settings.get("extra_args") or [])
        cmd.append(text)   # positional, last -- matches the CLI's `nargs="*"` text arg
        return cmd

    def synthesize(self, text, out_path, voice=None):
        from localtts import audiofx
        segments = textutil.resolve_tone_segments(text, auto_tone=bool(self.settings.get("auto_tone")))
        if len(segments) == 1 and segments[0][1] is None:
            self._run_one(segments[0][0], out_path, voice, None)
            return out_path

        work = tempfile.mkdtemp(prefix="local-tts-tone-")
        parts = [os.path.join(work, "%04d.wav" % index) for index in range(len(segments))]
        try:
            for (chunk, profile), part in zip(segments, parts):
                self._run_one(chunk, part, voice, self._effective_speed(profile))
                # kokoro has no volume knob of its own, and declaring supports_tone_tags
                # keeps synthesize_chunked()'s audiofx pass from ever running for us --
                # so a tag's volume has to be applied right here or it is silently lost,
                # which made <whisper> merely slow instead of quiet.
                if profile and profile["volume"] != 1.0:
                    audiofx.apply_profile(part, speed=1.0, volume=profile["volume"])
                self.emit_part(part, final=part == parts[-1])
            audiomod.concat_wavs(parts, out_path)
        finally:
            for part in parts:
                if os.path.exists(part):
                    os.unlink(part)
            if os.path.isdir(work):
                os.rmdir(work)
        return out_path

    def _run_one(self, text, out_path, voice, speed):
        server_url = self.settings.get("server_url")
        if server_url:
            return self._synthesize_via_server(server_url, text, out_path, voice, speed)

        model_dir = self._model_dir()
        cmd = self.build_command(text, out_path, voice, speed=speed)
        self.run(cmd, cwd=model_dir)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("kokoro-tts wrote no audio to %s" % out_path)
        return out_path

    def _synthesize_via_server(self, server_url, text, out_path, voice, speed=None):
        """Talk to a persistent server that already has the model loaded, instead of
        spawning kokoro-tts fresh (and reloading its model) for this one call. voice/lang
        travel per request, same as the subprocess path -- the server holding the model
        resident doesn't fix them to whatever it started with."""
        self.ensure_server(server_url, self.settings.get("server_start"),
                           float(self.settings.get("server_timeout") or 30))
        payload = {"text": text}
        chosen_voice = self.resolved_voice(voice)
        if chosen_voice:
            payload["voice"] = chosen_voice
        lang = self.resolved_lang(voice)
        if lang:
            payload["lang"] = lang
        effective_speed = speed if speed is not None else self.settings.get("speed")
        if effective_speed and float(effective_speed) != 1.0:
            payload["speed"] = float(effective_speed)
        # Server-only: the server has the phonemizer and kokoro's own pause arguments.
        # A server that predates these simply ignores unknown keys, so sending them is
        # always safe; the subprocess CLI wrapper has no equivalent flags.
        # Only to a server that says it understands them. An older copy of the script
        # accepts the key and drops it without a word, and reporting that as working is
        # the exact silent no-op this feature exists to remove.
        phonetics = self.phonetics_table(text) if self.supports_phonetics else {}
        if phonetics:
            # The server holds the phonemizer, so it is the one that can transcribe the
            # sentence and drop these in. Sending the table rather than a pre-built
            # string keeps local-tts dependency-free.
            payload["phonetics"] = phonetics
        marks = int(self.settings.get("emphasis_lengthen") or 0)
        if marks > 0:
            payload["emphasis_lengthen"] = marks
        for key in ("sentence_pause", "clause_pause"):
            value = self.settings.get(key)
            if value not in (None, ""):
                payload[key] = float(value)
        audio_bytes = self.post_for_audio(server_url, "/synthesize", payload)
        with open(out_path, "wb") as fh:
            fh.write(audio_bytes)
        return out_path

    def check(self):
        server_url = self.settings.get("server_url")
        if server_url:
            # A quick probe only -- tts check must not have the side effect of starting
            # a server, that's what actually speaking (or a deliberate warmup) does.
            if self.server_alive(server_url):
                return True, "server at %s (already running)" % server_url
            if self.settings.get("server_start"):
                return True, "server at %s (not running yet -- starts automatically on first use)" % server_url
            return False, "server at %s is not running, and no server_start is configured" % server_url

        try:
            exe = self.resolve_binary("binary", "kokoro-tts")
        except TTSError as exc:
            return False, str(exc)
        try:
            model_dir = self._model_dir()
        except TTSError as exc:
            return False, "%s (%s)" % (exe, exc)
        return True, "%s%s" % (exe, " -> %s" % model_dir if model_dir else "")
