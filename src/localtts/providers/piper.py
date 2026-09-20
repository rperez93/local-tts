"""Piper backend: fast, tiny, fully offline ONNX voices."""

import os
import tempfile

from localtts import audio as audiomod
from localtts import text as textutil
from localtts.errors import TTSError
from localtts.providers.base import Provider


class PiperProvider(Provider):
    name = "piper"
    default_format = "wav"
    #: --length-scale (inverse of rate) and --volume are real piper flags -- verified
    #: against `piper --help` -- so a <tag>'s speed/volume can be genuinely realized here,
    #: not just its instructions phrase (which piper has no hook for at all).
    supports_tone_tags = True
    realizes_speed = True
    realizes_volume = True

    def build_command(self, text, out_path, voice=None, overrides=None):
        """The text is piped on stdin, so it does not appear in the argv. `overrides`
        temporarily replaces settings (length_scale/volume) for one call, without
        mutating self.settings -- used for a <tag>-adjusted segment; leave it out for the
        plain, unadjusted case (also what --dry-run calls with)."""
        settings = dict(self.settings, **(overrides or {})) if overrides else self.settings
        exe = self.resolve_binary("binary", "piper")
        model = os.path.expanduser(self.resolved_model(voice) or "")
        if not model:
            raise TTSError(
                "piper needs a voice model: `tts config --set piper.model=/path/to/voice.onnx` "
                "(download voices from https://huggingface.co/rhasspy/piper-voices)"
            )
        if not os.path.exists(model):
            raise TTSError("piper voice not found: %s" % model)

        cmd = [exe, "--model", model, "--output_file", out_path]
        speaker = settings.get("speaker")
        if speaker is not None:
            cmd += ["--speaker", str(speaker)]
        length_scale = settings.get("length_scale")
        if length_scale:
            cmd += ["--length-scale", str(length_scale)]
        volume = settings.get("volume")
        if volume:
            cmd += ["--volume", str(volume)]
        return cmd + list(settings.get("extra_args") or [])

    def resolved_model(self, voice=None):
        """The .onnx for this call: an explicit --voice, else this call's language
        entry from `language_models`, else the flat `model` setting. A piper voice *is*
        a language, so speaking two means having two files."""
        if voice:
            return voice
        return (self.for_language(self.settings.get("language_models") or {})
                or self.settings.get("model") or "")

    def speed_settings(self, speed):
        """length_scale is the inverse of rate (piper convention), so a >1 speed
        multiplier divides it."""
        return {"length_scale": float(self.settings.get("length_scale") or 1.0) / speed}

    def _prosody_overrides(self, profile):
        """Fold a <tag> profile's speed/volume multiplier into this call's *effective*
        length_scale/volume, on top of whatever base value is already configured --
        length_scale is the inverse of rate (piper convention), so a >1 speed multiplier
        divides it."""
        if profile is None:
            return None
        overrides = {}
        if profile["speed"] != 1.0:
            overrides.update(self.speed_settings(profile["speed"]))
        if profile["volume"] != 1.0:
            overrides["volume"] = float(self.settings.get("volume") or 1.0) * profile["volume"]
        return overrides or None

    def _run_one(self, text, out_path, voice, overrides):
        cmd = self.build_command(text, out_path, voice, overrides=overrides)
        self.run(cmd, stdin_text=text + "\n")
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("piper wrote no audio to %s" % out_path)

    def synthesize(self, text, out_path, voice=None):
        segments = textutil.resolve_tone_segments(text, auto_tone=bool(self.settings.get("auto_tone")))
        if len(segments) == 1 and segments[0][1] is None:
            # segments[0][0], not `text`: a tag that resolves to a neutral profile (e.g.
            # <assertion>..</assertion>) still needs its markup stripped even though there
            # is nothing to adjust -- resolve_tone_segments() already did that.
            self._run_one(segments[0][0], out_path, voice, None)
            return out_path

        work = tempfile.mkdtemp(prefix="local-tts-tone-")
        parts = [os.path.join(work, "%04d.wav" % index) for index in range(len(segments))]
        try:
            for (chunk, profile), part in zip(segments, parts):
                self._run_one(chunk, part, voice, self._prosody_overrides(profile))
                self.emit_part(part, final=part == parts[-1])
            audiomod.concat_wavs(parts, out_path)
        finally:
            for part in parts:
                if os.path.exists(part):
                    os.unlink(part)
            if os.path.isdir(work):
                os.rmdir(work)
        return out_path

    def check(self):
        try:
            exe = self.resolve_binary("binary", "piper")
        except TTSError as exc:
            return False, str(exc)
        model = os.path.expanduser(self.settings.get("model") or "")
        if not model:
            return False, "%s (no voice model configured)" % exe
        if not os.path.exists(model):
            return False, "%s (voice missing: %s)" % (exe, model)
        return True, "%s -> %s" % (exe, model)
