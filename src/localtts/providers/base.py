"""Provider contract plus small helpers shared by the subprocess-based backends."""

import hashlib
import json
import os
import http.client
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from localtts import lock
from localtts.errors import TTSError


class Provider:
    name = "base"
    #: Container format this backend writes. Used to name temp files.
    default_format = "wav"
    #: Whether this backend can act on `<tag>` tone tags (text.tone_segments()) itself.
    #: False for every backend without a real, verified tone/emotion hook -- text.
    #: synthesize_chunked() strips the tags before a provider that can't use them ever
    #: sees the literal brackets, rather than have it try to pronounce them.
    supports_tone_tags = False

    #: Whether this backend can be handed IPA for individual words, which is what makes
    #: the pronunciation dictionary's `/…/` entries reach the model. Only a backend with
    #: a phonemizer in the loop can: local-tts has no runtime dependencies and cannot
    #: transcribe anything itself, so it passes the table along rather than applying it.
    #: A backend that says False here simply says the word its own way, and `tts check`
    #: reports that rather than leaving the user guessing why an entry did nothing.
    supports_phonetics = False
    #: Which dimensions of a tone profile this backend applies *itself*. Anything left
    #: False is realized after synthesis instead (audiofx.apply_profile), so an emotion
    #: still sounds different on a backend with no such flag. A provider that varies
    #: tone only through free-text instructions (openai) still sets both, because its
    #: own request carries the speed and the model performs the loudness.
    realizes_speed = False
    realizes_volume = False
    #: True for a provider that splits tone segments itself (rvc, which must convert each
    #: one separately or they all come out with a single flat tone). text.
    #: synthesize_chunked() then leaves the segmenting to it instead of doing its own.
    handles_tone_segments = False
    #: Sink for streamed playback (audio.play_stream_detached): called as on_part(path)
    #: with each finished fragment, in order, while later ones are still being
    #: synthesized. None disables streaming and nothing changes. Set by cli.py on the
    #: top-level provider only -- a provider used as somebody else's base (rvc's) gets a
    #: fresh instance, so its pre-conversion pieces never reach the stream.
    on_part = None
    #: Whether anything this backend renders may be reshaped by audiofx afterwards.
    #: True everywhere local-tts knows what the backend can and cannot do itself. False
    #: for a backend driven by a command nobody here wrote, where "no tone hook" is an
    #: assumption rather than a verified fact -- reshaping its output would then be
    #: fighting a script that may already be handling the tags.
    allow_audio_fx = True

    @property
    def max_words(self):
        """Words per synthesis call; 0 means the backend handles long text itself."""
        return int(self.settings.get("max_words") or 0)

    @property
    def max_workers(self):
        """How many chunks to synthesize concurrently when text.chunks() splits the input.

        Each chunk is a separate subprocess call paying its own fixed startup cost
        (process spawn, model load), so overlapping them is most of the win for a
        backend that has to chunk. Defaults to 1 (sequential, today's behavior)
        unless a provider sets its own default or the user overrides it.
        """
        return max(1, int(self.settings.get("max_workers") or 1))

    def __init__(self, settings, verbose=False, cfg=None, lang=""):
        self.settings = settings
        self.verbose = verbose
        #: The --lang tag this call resolved to ("" when none was given). Only
        #: providers that vary per language need it -- rvc picks which resident
        #: voice model to ask a multi-model server for.
        self.lang = lang or ""
        #: The full loaded configuration, not just this provider's own settings sub-dict.
        #: None unless the caller passed one via providers.build(). Only providers that
        #: compose another provider (rvc, chaining a base TTS backend) need this; most
        #: never touch it.
        self.cfg = cfg

    def synthesize(self, text, out_path, voice=None):
        raise NotImplementedError

    def emit_part(self, path, final=False):
        """Publish one finished, in-order fragment to the streaming sink, if any.

        Called by providers that render ordered parts internally (their own tone
        segments). Doing it from inside the loop is the whole point: the fragment is
        playable long before the ones after it exist.
        """
        sink = self.on_part
        if sink:
            (getattr(sink, "final", sink) if final else sink)(path)

    def for_language(self, mapping, default=""):
        """This call's entry from a {language tag: value} map, or `default`.

        An exact tag beats its base language (es-MX before es), matching how the language
        memory itself resolves, so a general Spanish entry can sit alongside a Mexican
        one. Shared because more than one provider maps per-language settings this way:
        rvc picks a resident voice model, kokoro picks a voice.
        """
        from localtts.config import normalize_language
        tag, base = normalize_language(self.lang)
        if not tag or not mapping:
            return default
        for candidate in (tag, base):
            for stored, value in mapping.items():
                if normalize_language(stored)[0] == candidate:
                    return value
        return default

    def speed_settings(self, speed):
        """Settings that make this backend synthesize at `speed` times its normal rate,
        or None if it has no rate control of its own.

        Exists so a provider that *composes* another one (rvc) can have the base realize
        a <tag>'s pacing at synthesis time -- a genuine prosody change -- instead of
        time-stretching the rendered wav afterwards, which is a lossy DSP pass over every
        sample. Returning None means "I have no such knob"; the caller then falls back to
        audiofx.
        """
        return None

    def with_settings(self, overrides):
        """A copy of this provider with `overrides` merged over its settings, leaving
        this instance untouched -- the settings dict is shared with the loaded config,
        so mutating it in place would leak into every later call."""
        return type(self)(dict(self.settings, **overrides), verbose=self.verbose,
                          cfg=self.cfg, lang=self.lang)


    def check(self):
        """Return (ok, message) describing whether this backend can run."""
        return True, "ready"

    # -- helpers -------------------------------------------------------

    def get(self, key, default=None):
        value = self.settings.get(key, default)
        return default if value in (None, "") and default is not None else value

    def path(self, key):
        raw = self.settings.get(key) or ""
        return os.path.expanduser(raw) if raw else ""

    def resolve_binary(self, key="binary", fallback=""):
        name = os.path.expanduser(self.settings.get(key) or fallback)
        found = shutil.which(name) or (name if os.path.isfile(name) and os.access(name, os.X_OK) else None)
        if not found:
            raise TTSError(
                "%s: %r not found on PATH. Install it, or point at it with "
                "`tts config --set %s.%s=/path/to/%s`." % (self.name, name, self.name, key, name)
            )
        return found

    def run(self, cmd, stdin_text=None, cwd=None):
        if self.verbose:
            print("+ %s%s" % ("cd %s && " % cwd if cwd else "", " ".join(cmd)), file=sys.stderr)
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_text,
                text=True,
                cwd=cwd,
                stdout=None if self.verbose else subprocess.PIPE,
                stderr=None if self.verbose else subprocess.PIPE,
            )
        except FileNotFoundError:
            raise TTSError("%s: command not found: %s" % (self.name, cmd[0]))
        except PermissionError:
            raise TTSError("%s: not executable: %s" % (self.name, cmd[0]))
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            tail = "\n".join(detail.splitlines()[-15:])
            raise TTSError(
                "%s failed (exit %d)%s" % (os.path.basename(cmd[0]), proc.returncode, "\n" + tail if tail else "")
            )
        return proc

    # -- optional persistent-server client -------------------------------------
    #
    # A backend that reloads a heavy model (an onnx graph, a torch checkpoint) on every
    # single call pays that cost per call. A provider can offer a "talk to a server that
    # keeps the model loaded" mode instead: this tool never runs that server itself --
    # it's a plain script the local-tts-configure skill writes into the backend's own
    # venv (the same pattern as its subprocess CLI wrapper) -- but any provider can reuse
    # this client-side plumbing to reach one and auto-start it if it isn't already up.

    def phonemizer_provider(self):
        """Whichever provider actually holds the phonemizer -- itself, for anything
        that synthesizes its own audio. Only rvc differs, and only because it does
        not read text at all."""
        return self

    def phonetics_table(self, text):
        """The `/IPA/` entries this call should carry, after the user's hooks.

        Resolved here rather than in each provider so a backend that accepts phonemes
        gets the same table, and so hooks run once per request no matter who asks. Hooks
        run even when the dictionary is empty: adding a transcription for a word nobody
        wrote down is the interesting half of what they are for.
        """
        from localtts import phonetics, text as textutil
        table = textutil.phonetic_entries((self.cfg or {}).get("pronunciations") or {},
                                          self.lang)
        return phonetics.run_hooks(table, text=text, lang=self.lang, provider=self.name,
                                   cfg=self.cfg or {})

    def server_alive(self, url, health_path="/health"):
        try:
            with urllib.request.urlopen(url.rstrip("/") + health_path, timeout=2) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def server_capabilities(self, url, health_path="/health"):
        """What a persistent server says it can do, from its own health endpoint.

        Three outcomes, and the difference matters: `None` means nothing answered,
        `{}` means something answered but did not say (an older server whose health
        check predates this, and which would drop a capability it never learned to
        read), and a dict is what it claims.

        Asking is the only way to tell. A configured `server_url` says a URL was
        written down, not that a process is listening, and a running process does not
        say whether it is the script this version of the skill installs -- a copy from
        an earlier version answers /health perfectly well and silently ignores what it
        does not understand, which is exactly the failure this reporting exists to
        make loud.
        """
        try:
            with urllib.request.urlopen(url.rstrip("/") + health_path, timeout=2) as response:
                if not 200 <= response.status < 300:
                    return None
                body = response.read(4096)
        except (urllib.error.URLError, OSError, ValueError):
            return None
        try:
            claimed = json.loads(body)
        except (ValueError, TypeError):
            return {}                       # answering, but with a plain-text "ok"
        return claimed if isinstance(claimed, dict) else {}

    def ensure_server(self, url, start_command, timeout, health_path="/health"):
        """If `url` isn't already answering `health_path`, launch `start_command` in the
        background and poll until it does, or raise after `timeout` seconds. A model
        load can genuinely take that long on first start, so this is meant to be patient,
        not a quick liveness probe.

        Multiple local-tts processes (separate agent sessions, a chunked synthesis
        run's concurrent workers) can hit this for the same server at once. Without
        coordination, every one of them would see it down and spawn a duplicate --
        racing for the same port. A single lock file per server URL, held with a real
        OS-level advisory lock (flock/msvcrt.locking rather than a "file exists"
        marker), serializes that: whoever gets the lock first is the one that actually
        starts it, and it's released automatically if that process dies for any reason,
        so there's no stale-lock cleanup to worry about. Everyone else blocks on the
        lock, then re-checks health once they get it -- by then the first one has
        usually already finished starting it, so they just proceed.
        """
        if self.server_alive(url, health_path):
            return

        lock_path = os.path.join(
            tempfile.gettempdir(),
            "local-tts-server-%s.lock" % hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        )
        with open(lock_path, "a+") as lock_handle:
            lock.acquire(lock_handle)
            try:
                if self.server_alive(url, health_path):   # someone else started it while we waited
                    return
                self._start_and_wait(url, start_command, timeout, health_path)
            finally:
                lock.release(lock_handle)

    def _start_and_wait(self, url, start_command, timeout, health_path):
        if not start_command:
            raise TTSError(
                "%s: nothing answering %s, and no server_start command configured. "
                "Either start the server yourself, or set "
                "`tts config --set %s.server_start=...` (see the local-tts-configure skill)."
                % (self.name, url, self.name)
            )
        try:
            cmd = shlex.split(start_command)
        except ValueError as exc:
            raise TTSError("%s.server_start is not valid shell syntax: %s" % (self.name, exc))
        if self.verbose:
            print("+ %s &" % " ".join(cmd), file=sys.stderr)
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                start_new_session=(sys.platform != "win32"),
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise TTSError("%s: could not start the server (%r): %s" % (self.name, start_command, exc))

        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            if self.server_alive(url, health_path):
                return
            time.sleep(0.5)
        raise TTSError(
            "%s: started %r but nothing answered %s within %ss -- check it can actually "
            "run (missing model files, wrong port, a crash on startup)."
            % (self.name, start_command, url, timeout)
        )

    def post_for_audio(self, url, path, payload, timeout=120):
        """POST JSON to `url` + `path`, return the raw audio bytes from the response body."""
        request = urllib.request.Request(
            url.rstrip("/") + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                audio = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace").strip()
            raise TTSError("%s server error (HTTP %s) from %s%s: %s"
                           % (self.name, exc.code, url, path, body[:500]))
        except urllib.error.URLError as exc:
            raise TTSError("%s: could not reach %s%s: %s" % (self.name, url, path, exc.reason))
        except (OSError, http.client.HTTPException) as exc:
            raise TTSError("%s: connection to %s%s interrupted: %s" % (self.name, url, path, exc))
        if not audio:
            raise TTSError("%s server returned an empty response" % self.name)
        return audio
