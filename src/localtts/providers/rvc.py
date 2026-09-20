"""RVC backend: retrieval-based voice conversion over another provider's output.

rvc-python (https://github.com/daswer123/rvc-python) does audio-to-audio conversion
only -- confirmed against its own README, it has no text input at all. So this provider
composes another one: synthesize with a normal TTS backend first, then run the result
through rvc-python to convert it to the target voice.

Not installed automatically, and never will be by this file -- it pulls in torch and is
sizable. Config points at wherever the user (or their agent, after asking) installed it
into its own venv, the same pattern piper and kokoro use for keeping heavy dependencies
out of local-tts's own install.
"""

import os
import tempfile

from localtts.errors import TTSError
from localtts.providers.base import Provider


#: Delivery knobs, with the values used when a language names none. Pauses are in
#: milliseconds because that is the unit anyone reasoning about speech rhythm uses; the
#: previous behavior was a hardcoded 350 ms between every fragment, which reads as a
#: stall rather than a breath.
DELIVERY_DEFAULTS = {
    "speed": 1.0,
    "pause_ms": 45,             # between fragments delivered the same way
    "pause_tone_ms": 130,       # when the tone changes -- the breath
    "emphasis_lengthen": 0,     # IPA length marks on the stressed vowel (kokoro only)
    "trim_ms": 10,              # silence left at each fragment edge before the pause
}


class RvcProvider(Provider):
    name = "rvc"
    default_format = "wav"
    #: rvc splits tone segments itself -- see synthesize(). Converting one merged wav
    #: would give every segment the same flat delivery, which is the whole thing tags
    #: exist to avoid.
    handles_tone_segments = True

    #: rvc does honor <tag>s, just not by passing them to anything: synthesize() splits
    #: on them and shapes each converted span. Declaring this keeps synthesize_chunked()
    #: from stripping the markup before rvc ever gets to see where the spans are.
    supports_tone_tags = True
    #: Neither dimension is realized by *rvc itself* -- conversion has no text, tone or
    #: rate input at all. Speed is nonetheless realized at synthesis time whenever the
    #: base provider has a real rate control, by handing it Provider.speed_settings()
    #: (see _synthesize_one); only what the base cannot do falls through to audiofx.
    #: These stay False because they describe this backend's own hooks, and
    #: _synthesize_with_audiofx() reads them to decide what is left over.
    realizes_speed = False
    realizes_volume = False

    def _python(self):
        python = os.path.expanduser(self.settings.get("python") or "")
        if not python:
            raise TTSError(
                "rvc needs the python interpreter from the venv rvc-python is installed "
                "in: `tts config --set rvc.python=/path/to/rvc-venv/bin/python` -- "
                "rvc-python is not installed automatically, see the local-tts-configure skill"
            )
        if not os.path.exists(python):
            raise TTSError("rvc.python not found: %s" % python)
        return python

    def _model(self):
        model = os.path.expanduser(self.settings.get("model") or "")
        if not model:
            raise TTSError("rvc needs a voice model: `tts config --set rvc.model=/path/to/model.pth`")
        if not os.path.exists(model):
            raise TTSError("rvc voice model not found: %s" % model)
        return model

    def server_model_name(self):
        """Which resident voice to ask a multi-model server for, or "" for whatever it
        loaded first. Language-specific mapping wins over the flat default, and an exact
        tag beats its base language (es-MX before es), matching how the language memory
        itself resolves. Public because `tts check` and --dry-run both report it."""
        return self.for_language(self.settings.get("language_models") or {},
                                 self.settings.get("server_model") or "")

    def _base_name(self):
        return self.settings.get("base_provider") or (self.cfg or {}).get("provider") or "piper"

    @property
    def supports_phonetics(self):
        """Inherited from whatever speaks underneath: rvc converts a voice, it does not
        read text, so the dictionary's IPA is the base provider's business."""
        try:
            return bool(getattr(self.base_provider_instance(), "supports_phonetics", False))
        except Exception:
            return False

    def phonemizer_provider(self):
        """The phonemes are the base provider's business, and so is the vocabulary
        they have to fit into: asking rvc's own server about them would ask the one
        process in the chain that never sees a word."""
        try:
            return self.base_provider_instance()
        except Exception:
            return self

    def base_provider_instance(self, lang=None):
        base_name = self._base_name()
        if base_name == self.name:
            raise TTSError("rvc.base_provider cannot be rvc itself")
        if self.cfg is None:
            raise TTSError(
                "rvc was built without the full configuration, so it cannot construct its "
                "base provider (internal error -- report this)"
            )
        from localtts import providers as providers_module   # local: avoids a cycle at import time
        return providers_module.build(base_name, self.cfg, verbose=self.verbose,
                                      lang=self.lang if lang is None else lang)

    def build_command(self, wav_in, out_path):
        """The voice-conversion half only. There is no single command for the whole
        pipeline -- the base synthesis step is a separate provider, possibly chunked --
        so --dry-run shows only this half, with a placeholder input path."""
        python = self._python()
        model = self._model()
        cmd = [python, "-m", "rvc_python", "cli", "-i", wav_in, "-o", out_path, "-mp", model]

        index = os.path.expanduser(self.settings.get("index") or "")
        if index:
            cmd += ["-ip", index]
        device = self.settings.get("device")
        if device:
            cmd += ["-de", str(device)]
        method = self.settings.get("method")
        if method:
            cmd += ["-me", method]
        conversion = self.conversion()
        pitch = conversion.get("pitch")
        if pitch:
            cmd += ["-pi", str(pitch)]
        index_rate = conversion.get("index_rate")
        if index_rate is not None:
            cmd += ["-ir", str(index_rate)]
        protect = conversion.get("protect")
        if protect is not None:
            cmd += ["-pr", str(protect)]
        return cmd + list(self.settings.get("extra_args") or [])

    def conversion(self):
        allowed = {"pitch", "index_rate", "protect"}
        values = {key: self.settings.get(key) for key in ("index_rate", "protect")}
        # A flat zero historically means "use the server startup pitch". A scoped
        # conversion entry can explicitly request zero without changing that default.
        if self.settings.get("pitch"):
            values["pitch"] = self.settings["pitch"]
        mapping = self.settings.get("conversion") or {}
        chosen = self.for_language(mapping, mapping.get("*") or {})
        if not isinstance(chosen, dict):
            raise TTSError("rvc.conversion entries must be objects")
        unknown = set(chosen) - allowed
        if unknown:
            raise TTSError("unknown RVC conversion settings: %s" % ", ".join(sorted(unknown)))
        values.update(chosen)
        return values

    def synthesize(self, text, out_path, voice=None):
        from localtts import audio as audiomod
        from localtts import text as textutil   # local: text.py doesn't import providers,
                                                 # kept local anyway for symmetry with above
        base = self.base_provider_instance()
        auto_tone = bool(base.settings.get("auto_tone")) if hasattr(base, "settings") else False

        # A borrowed word keeps its own sound through the pronunciation dictionary's
        # IPA entries, which the base provider applies while it speaks -- so there is
        # nothing to cut here for language, and tone is the only thing that segments.
        segments = [(chunk, profile, base) for chunk, profile
                    in textutil.resolve_tone_segments(text, auto_tone=auto_tone)]

        if len(segments) == 1 and segments[0][1] is None:
            self._synthesize_one(segments[0][2], segments[0][0], out_path, voice, profile=None)
            return out_path

        # Each tagged span makes its own trip through the converter: rvc works on audio,
        # so a span's delivery has to already be in the wav it receives.
        from localtts import audiofx

        delivery = self.delivery()
        work = tempfile.mkdtemp(prefix="local-tts-rvc-tone-")
        parts = [os.path.join(work, "%04d.wav" % index) for index in range(len(segments))]
        try:
            for index, ((chunk, profile, span_base), part) in enumerate(zip(segments, parts)):
                self._synthesize_one(span_base, chunk, part, voice, profile)
                # Before padding: otherwise the real gap is this fragment's own dead air
                # plus the configured pause, and the setting controls neither.
                audiofx.trim_silence(part, delivery["trim_ms"] / 1000.0)
                if index < len(segments) - 1:
                    # A change of tone gets a longer gap than an ordinary one -- the
                    # breath a speaker takes when the delivery shifts. Padded onto the
                    # fragment rather than inserted while joining, so the streamed and
                    # the saved audio are the same sound.
                    changing = profile != segments[index + 1][1]
                    gap = delivery["pause_tone_ms"] if changing else delivery["pause_ms"]
                    audiofx.append_silence(part, gap / 1000.0)
                self.emit_part(part, final=part == parts[-1])
            audiomod.concat_wavs(parts, out_path, gap_seconds=0)
        finally:
            for part in parts:
                if os.path.exists(part):
                    os.unlink(part)
            if os.path.isdir(work):
                os.rmdir(work)
        return out_path

    def delivery(self):
        """This language's delivery settings, over the built-in defaults.

        Pacing is language-specific in a way a single number cannot cover: Spanish runs
        faster with shorter gaps than English, and both differ from what a <tag> asks for
        on top. Falls back to `delivery["*"]`, then to DELIVERY_DEFAULTS, so a config
        that names only one language still works for the others.
        """
        by_lang = self.settings.get("delivery") or {}
        chosen = self.for_language(by_lang, by_lang.get("*") or {})
        merged = dict(DELIVERY_DEFAULTS)
        if isinstance(chosen, dict):
            merged.update({k: v for k, v in chosen.items() if k in DELIVERY_DEFAULTS})
        return merged

    def _synthesize_one(self, base, chunk, out_path, voice, profile):
        """Base synthesis -> conversion -> whatever tone is still unrealized.

        Speed is pushed down to the base provider whenever it has a real rate control
        (piper's --length-scale, kokoro's -s). Conversion is frame-wise and preserves
        duration exactly, so pacing chosen before it survives it intact -- and asking
        piper to speak slowly is a genuine prosody change, where time-stretching the
        rendered wav afterwards is a lossy pass over every sample that reads as a robotic
        buzz. resolve_tone_segments() has already stripped the markup by this point, so
        the speed has to travel as a setting rather than as a tag the base could see.

        Volume cannot travel the same way: rvc renormalizes amplitude, so a quieter base
        comes back at full level. It stays a post-conversion step, which costs nothing --
        apply_volume is exact integer scaling, not a resampling.
        """
        from localtts import audiofx
        from localtts import text as textutil

        speed = (profile["speed"] if profile else 1.0) * float(self.delivery()["speed"])
        volume = profile["volume"] if profile else 1.0
        residual_speed = speed
        marks = int(self.delivery()["emphasis_lengthen"] or 0)
        if marks > 0:
            # Harmless on a base that has no such setting -- it simply never reads it.
            base = base.with_settings({"emphasis_lengthen": marks})
        if abs(speed - 1.0) >= audiofx.EPSILON:
            # getattr rather than a direct call: the base is duck-typed here (see the
            # auto_tone lookup in synthesize()), so anything without the hook simply
            # keeps the old audiofx path.
            speed_settings = getattr(base, "speed_settings", None)
            overrides = speed_settings(speed) if speed_settings else None
            if overrides:
                base = base.with_settings(overrides)
                residual_speed = 1.0

        handle, base_wav = tempfile.mkstemp(prefix="local-tts-rvc-base-", suffix=".wav")
        os.close(handle)
        try:
            textutil.synthesize_chunked(base, chunk, base_wav, voice=voice)
            server_url = self.settings.get("server_url")
            if server_url:
                self._convert_via_server(server_url, base_wav, out_path)
            else:
                self.run(self.build_command(base_wav, out_path))
        finally:
            if os.path.exists(base_wav):
                os.unlink(base_wav)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("rvc-python wrote no audio to %s" % out_path)

        if profile:
            audiofx.apply_profile(out_path, speed=residual_speed, volume=volume)
        return out_path


    def _convert_via_server(self, server_url, wav_in, out_path, model=None):
        """Talk to a persistent server that already has the model (and torch) loaded,
        instead of paying that cost on every call. Which voices exist is fixed at server
        startup -- rvc.model/rvc.index configure the CLI fallback above, not a running
        server -- but a server started with several `--model name=path` pairs keeps them
        all resident, and `rvc.language_models`/`rvc.server_model` pick one per request.
        Adding a voice still means restarting the server with an extra pair."""
        self.ensure_server(server_url, self.settings.get("server_start"),
                           float(self.settings.get("server_timeout") or 60))
        payload = {"input_path": os.path.abspath(wav_in)}
        model_name = model or self.server_model_name()
        if model_name:
            # A single-model server (or an older one) simply ignores this key, so
            # naming a voice is always safe to send.
            payload["model"] = model_name
        conversion = self.conversion()
        advanced = any(conversion.get(key) is not None for key in ("index_rate", "protect"))
        if advanced or self.settings.get("conversion"):
            capabilities = getattr(self, "_conversion_capabilities", None)
            if capabilities is None:
                capabilities = self.server_capabilities(server_url) or {}
                self._conversion_capabilities = capabilities
            if not capabilities.get("conversion_parameters"):
                raise TTSError("RVC server cannot apply conversion overrides; run `tts servers --refresh`")
            payload.update({key: value for key, value in conversion.items() if value is not None})
        elif conversion.get("pitch"):
            payload["pitch"] = conversion["pitch"]
        device = self.settings.get("device")
        if device:
            payload["device"] = str(device)
        audio = self.post_for_audio(server_url, "/convert", payload)
        with open(out_path, "wb") as fh:
            fh.write(audio)

    def check(self):
        server_url = self.settings.get("server_url")
        if server_url:
            # A quick probe only -- tts check must not have the side effect of starting
            # a server (a torch model load is exactly the cost this mode exists to avoid
            # paying casually).
            if self.server_alive(server_url):
                detail = "server at %s (already running)" % server_url
            elif self.settings.get("server_start"):
                detail = "server at %s (not running yet -- starts automatically on first use)" % server_url
            else:
                return False, "server at %s is not running, and no server_start is configured" % server_url
            voices = self.settings.get("server_models") or {}
            by_lang = self.settings.get("language_models") or {}
            if voices:
                detail += ", %d voice%s resident (%s)" % (
                    len(voices), "" if len(voices) == 1 else "s", ", ".join(sorted(voices)))
            if by_lang:
                detail += "; " + ", ".join("%s->%s" % (k, by_lang[k]) for k in sorted(by_lang))
            return True, "%s, base voice from %s" % (detail, self._base_name())

        try:
            python = self._python()
        except TTSError as exc:
            return False, str(exc)
        try:
            model = self._model()
        except TTSError as exc:
            return False, "%s (%s)" % (python, exc)
        return True, "%s -> %s, base voice from %s" % (python, model, self._base_name())
