"""Stdlib-only tests: python -m unittest discover -s tests"""

import http.server
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
import wave
from pathlib import Path

from localtts import audio, audiofx, config, g2p, hooks, providers, skills, text as textutil
from localtts.g2p import en as g2p_en, es as g2p_es
from localtts.cli import _resolve_session, _synthesize, main
from localtts.errors import TTSError
from localtts.providers.base import Provider
from localtts.providers.command import CommandProvider
from localtts.providers.kokoro import KokoroProvider
from localtts.providers.llamacpp import LlamaCppProvider
from localtts.providers.openai import OpenAIProvider
from localtts.providers.piper import PiperProvider
from localtts.providers.rvc import RvcProvider


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        os.environ["LOCALTTS_CONFIG"] = str(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "LOCALTTS_CONFIG", None)

    def test_defaults_when_no_file(self):
        cfg = config.load()
        self.assertEqual(cfg["provider"], "kokoro")
        self.assertTrue(cfg["play"])

    def test_file_overrides_defaults_without_dropping_siblings(self):
        self.path.write_text(json.dumps({"providers": {"llamacpp": {"threads": 8}}}))
        cfg = config.load()
        self.assertEqual(cfg["providers"]["llamacpp"]["threads"], 8)
        self.assertEqual(cfg["providers"]["llamacpp"]["binary"], "llama-tts")

    def test_env_beats_file(self):
        self.path.write_text(json.dumps({"provider": "piper"}))
        os.environ["LOCALTTS_PROVIDER"] = "openai"
        self.addCleanup(os.environ.pop, "LOCALTTS_PROVIDER", None)
        self.assertEqual(config.load()["provider"], "openai")

    def test_env_coerces_types(self):
        os.environ["LOCALTTS_LLAMACPP_THREADS"] = "12"
        os.environ["LOCALTTS_PLAY"] = "false"
        self.addCleanup(os.environ.pop, "LOCALTTS_LLAMACPP_THREADS", None)
        self.addCleanup(os.environ.pop, "LOCALTTS_PLAY", None)
        cfg = config.load()
        self.assertEqual(cfg["providers"]["llamacpp"]["threads"], 12)
        self.assertIs(cfg["play"], False)

    def test_set_values_rejects_unknown_keys(self):
        with self.assertRaises(TTSError):
            config.set_values(["llamacpp.bogus=1"])
        with self.assertRaises(TTSError):
            config.set_values(["bogus=1"])

    def test_set_values_persists(self):
        config.set_values(["provider=piper", "llamacpp.threads=4"])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["provider"], "piper")
        self.assertEqual(saved["providers"]["llamacpp"]["threads"], 4)

    def test_llamacpp_chunks_run_concurrently_by_default(self):
        # See the comment above "max_workers" in DEFAULTS for the numbers behind 2:
        # each llama-tts call pays several seconds of fixed startup cost regardless
        # of chunk size, so this is where most of the real-world win comes from.
        self.assertEqual(config.DEFAULTS["providers"]["llamacpp"]["max_workers"], 2)


class MigrationDetectionTest(unittest.TestCase):
    def cfg(self, template, provider="llamacpp"):
        merged = {"provider": provider, "providers": dict(config.DEFAULTS["providers"])}
        merged["providers"] = {k: dict(v) for k, v in merged["providers"].items()}
        merged["providers"]["command"] = {"template": template}
        return merged

    def test_no_command_template_means_nothing_to_migrate(self):
        cfg = self.cfg("")
        self.assertEqual(config.detect_migrations(cfg), [])

    def test_unrelated_template_is_not_flagged(self):
        cfg = self.cfg("espeak-ng -w {output} {text}")
        self.assertEqual(config.detect_migrations(cfg), [])

    def test_the_real_installed_kokoro_wrapper_is_detected(self):
        # The exact template this project's own setup produces.
        cfg = self.cfg("kokoro-tts -o {output} -v ef_dora -l es {text}")
        found = config.detect_migrations(cfg)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["provider"], "kokoro")
        self.assertEqual(found[0]["sets"], {"kokoro.voice": "ef_dora", "kokoro.lang": "es"})

    def test_placeholder_tokens_are_never_captured_as_flag_values(self):
        # A template like `kokoro-tts -v {text} -o {output}` (voice accidentally left as
        # the placeholder) must not migrate kokoro.voice to the literal string "{text}".
        cfg = self.cfg("kokoro-tts -o {output} -v {text}")
        found = config.detect_migrations(cfg)
        self.assertNotIn("kokoro.voice", found[0]["sets"])

    def test_rvc_template_is_detected(self):
        cfg = self.cfg("/venv/bin/python -m rvc_python cli -i {text} -o {output} "
                       "-mp /models/jarvis.pth -de cuda:0")
        found = config.detect_migrations(cfg)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["provider"], "rvc")
        self.assertEqual(found[0]["sets"]["rvc.model"], "/models/jarvis.pth")
        self.assertEqual(found[0]["sets"]["rvc.device"], "cuda:0")

    def test_was_default_flags_only_when_command_is_the_active_provider(self):
        active = self.cfg("kokoro-tts -o {output} -v x {text}", provider="command")
        inactive = self.cfg("kokoro-tts -o {output} -v x {text}", provider="llamacpp")
        self.assertTrue(config.detect_migrations(active)[0]["was_default"])
        self.assertFalse(config.detect_migrations(inactive)[0]["was_default"])

    def test_malformed_template_does_not_raise(self):
        cfg = self.cfg("kokoro-tts 'unterminated")
        self.assertEqual(config.detect_migrations(cfg), [])


class LlamaCppTest(unittest.TestCase):
    def build(self, **settings):
        merged = dict(config.DEFAULTS["providers"]["llamacpp"])
        merged.update(settings)
        provider = LlamaCppProvider(merged)
        provider.resolve_binary = lambda *a, **k: "/usr/bin/llama-tts"
        return provider

    def test_defaults_to_bundled_oute_weights(self):
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertIn("--tts-oute-default", cmd)
        self.assertEqual(cmd[-4:], ["-p", "hi", "-o", "/tmp/a.wav"])

    def test_model_requires_vocoder(self):
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model:
            with self.assertRaises(TTSError):
                self.build(model=model.name).build_command("hi", "/tmp/a.wav")

    def test_model_and_vocoder_are_passed_through(self):
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model, \
                tempfile.NamedTemporaryFile(suffix=".gguf") as vocoder:
            cmd = self.build(model=model.name, vocoder=vocoder.name).build_command("hi", "/tmp/a.wav")
        self.assertIn("-m", cmd)
        self.assertIn("-mv", cmd)
        self.assertNotIn("--tts-oute-default", cmd)

    def test_missing_model_file_is_reported(self):
        with self.assertRaises(TTSError):
            self.build(model="/definitely/not/here.gguf", vocoder="/nope.gguf").build_command("hi", "/tmp/a.wav")

    def test_max_workers_defaults_to_two_and_is_overridable(self):
        self.assertEqual(self.build().max_workers, 2)
        self.assertEqual(self.build(max_workers=5).max_workers, 5)
        self.assertEqual(self.build(max_workers=0).max_workers, 1)  # clamped to at least 1


class _FakeAudioServer:
    """A minimal real HTTP server (stdlib http.server, its own thread) for testing the
    server-mode client path: GET /health, and one POST route that records the JSON body
    it received and returns canned bytes. Used instead of mocking urllib so the actual
    request/response wire format is exercised, not just the call site."""

    def __init__(self, route, audio_bytes=b"RIFF....WAVEfake", status=200, healthy=True,
                 capabilities=None):
        self.route = route
        self.audio_bytes = audio_bytes
        self.status = status
        self.healthy = healthy
        #: What /health claims. None keeps the plain-text "ok" an older server sends,
        #: which is the case worth testing: it answers, and understands nothing new.
        self.capabilities = capabilities
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/health" and outer.healthy:
                    body = (json.dumps(outer.capabilities).encode("utf-8")
                            if outer.capabilities is not None else b"ok")
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(503)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                outer.requests.append(json.loads(body) if body else {})
                if self.path == outer.route:
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "audio/wav")
                    self.end_headers()
                    if outer.status < 300:
                        self.wfile.write(outer.audio_bytes)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class ServerModeTest(unittest.TestCase):
    """Provider.ensure_server()/server_alive()/post_for_audio() -- the shared client-side
    plumbing kokoro and rvc both use for their optional persistent-server mode."""

    def build(self):
        return Provider({}, verbose=False)

    def test_server_alive_true_for_a_real_reachable_server(self):
        server = _FakeAudioServer("/x")
        self.addCleanup(server.stop)
        self.assertTrue(self.build().server_alive(server.url))

    def test_server_alive_false_when_nothing_is_listening(self):
        self.assertFalse(self.build().server_alive("http://127.0.0.1:1"))

    def test_server_alive_false_when_health_reports_unhealthy(self):
        server = _FakeAudioServer("/x", healthy=False)
        self.addCleanup(server.stop)
        self.assertFalse(self.build().server_alive(server.url))

    def test_ensure_server_is_a_noop_when_already_alive(self):
        server = _FakeAudioServer("/x")
        self.addCleanup(server.stop)
        self.build().ensure_server(server.url, start_command="", timeout=5)   # must not raise

    def test_ensure_server_raises_when_unreachable_and_no_start_command(self):
        with self.assertRaises(TTSError) as caught:
            self.build().ensure_server("http://127.0.0.1:1", start_command="", timeout=1)
        self.assertIn("server_start", str(caught.exception))

    def test_ensure_server_auto_starts_a_real_subprocess_and_waits_for_it(self):
        # A genuine end-to-end exercise of the auto-start path: a real subprocess that
        # starts listening only after a short delay, proving ensure_server() actually
        # polls rather than checking once and giving up. The handler must return 2xx --
        # a bare BaseHTTPRequestHandler 501s on GET, which server_alive() correctly
        # treats as down, so this builds one that answers /health properly.
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        script = (
            "import time, http.server; time.sleep(0.4); "
            "H = type('H', (http.server.BaseHTTPRequestHandler,), "
            "{'do_GET': lambda self: (self.send_response(200), self.end_headers())}); "
            "h = http.server.HTTPServer(('127.0.0.1', %d), H); "
            "h.timeout = 5; h.handle_request()" % port
        )
        start_command = "%s -c \"%s\"" % (sys.executable, script)
        provider = self.build()
        provider.ensure_server("http://127.0.0.1:%d" % port, start_command, timeout=10)

    def test_ensure_server_raises_if_the_start_command_never_comes_up(self):
        with self.assertRaises(TTSError) as caught:
            self.build().ensure_server("http://127.0.0.1:%d" % 1,
                                       start_command="%s -c \"pass\"" % sys.executable,
                                       timeout=1)
        self.assertIn("nothing answered", str(caught.exception))

    def test_ensure_server_rejects_unparseable_start_command(self):
        with self.assertRaises(TTSError):
            self.build().ensure_server("http://127.0.0.1:1", start_command="unterminated '",
                                       timeout=1)

    def test_concurrent_ensure_server_only_spawns_it_once(self):
        # The real scenario this guards against: several separate local-tts processes
        # (different agent sessions) all find the server down at once and each try to
        # start it, racing for the same port. Genuinely separate OS processes here, not
        # threads, since the lock is a cross-process file lock (flock/msvcrt.locking) --
        # a threads-only test wouldn't exercise that code path meaningfully.
        import socket
        import subprocess
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        url = "http://127.0.0.1:%d" % port

        with tempfile.TemporaryDirectory() as tmp:
            start_log = os.path.join(tmp, "starts.log")
            # Deliberately slow to start (0.5s) so several racing processes are very
            # likely to all observe "down" before the winner's server comes up --
            # otherwise the test could pass by accident (each one serialized late
            # enough to see the previous one already alive) rather than by the lock.
            start_script = (
                "import http.server, time; "
                "open(%r, 'a').write('x\\n'); "
                "time.sleep(0.5); "
                "H = type('H', (http.server.BaseHTTPRequestHandler,), "
                "{'do_GET': lambda self: (self.send_response(200), self.end_headers())}); "
                "h = http.server.HTTPServer(('127.0.0.1', %d), H); "
                "h.serve_forever()" % (start_log, port)
            )
            start_command = "%s -c \"%s\"" % (sys.executable, start_script)

            worker_script = (
                "import sys; sys.path.insert(0, %r); "
                "from localtts.providers.base import Provider; "
                "Provider({}, verbose=False).ensure_server(%r, %r, 15)"
                % (os.path.join(os.path.dirname(__file__), "..", "src"), url, start_command)
            )
            workers = [
                subprocess.Popen([sys.executable, "-c", worker_script],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(5)
            ]
            try:
                results = [w.wait(timeout=25) for w in workers]
                self.assertEqual(results, [0] * 5, "every concurrent caller must succeed")
                with open(start_log) as fh:
                    spawns = fh.read().count("x")
                self.assertEqual(spawns, 1, "exactly one process must have actually spawned the server")
            finally:
                # ensure_server() starts the fake server detached (start_new_session=True)
                # so it outlives every worker process; it would otherwise leak past the test.
                subprocess.run(["pkill", "-f", start_log], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_post_for_audio_returns_the_response_body(self):
        server = _FakeAudioServer("/synthesize", audio_bytes=b"the-audio-bytes")
        self.addCleanup(server.stop)
        audio = self.build().post_for_audio(server.url, "/synthesize", {"text": "hi"})
        self.assertEqual(audio, b"the-audio-bytes")
        self.assertEqual(server.requests, [{"text": "hi"}])

    def test_post_for_audio_raises_on_empty_response(self):
        server = _FakeAudioServer("/synthesize", audio_bytes=b"")
        self.addCleanup(server.stop)
        with self.assertRaises(TTSError):
            self.build().post_for_audio(server.url, "/synthesize", {})

    def test_post_for_audio_raises_with_server_body_on_http_error(self):
        server = _FakeAudioServer("/synthesize", status=500)
        self.addCleanup(server.stop)
        with self.assertRaises(TTSError) as caught:
            self.build().post_for_audio(server.url, "/synthesize", {})
        self.assertIn("500", str(caught.exception))


class KokoroProviderTest(unittest.TestCase):
    """Targets the real interface: -o/-v/-l/-s flags, text as a trailing positional arg
    (not stdin) -- confirmed against an actual installed `kokoro-tts` wrapper."""

    def build(self, **settings):
        merged = dict(config.DEFAULTS["providers"]["kokoro"])
        merged.update(settings)
        provider = KokoroProvider(merged)
        provider.resolve_binary = lambda *a, **k: "/usr/bin/kokoro-tts"
        return provider

    def test_model_dir_is_optional_by_default(self):
        # Most kokoro CLIs manage their own model location internally; only some
        # (e.g. nazdridoy/kokoro-tts) resolve model files via a working directory.
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertNotIn("model_dir", " ".join(cmd))

    def test_missing_model_files_are_reported_by_name_when_model_dir_is_set(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(TTSError) as caught:
                self.build(model_dir=empty).synthesize("hi", "/tmp/a.wav")
        self.assertIn("kokoro-v1.0.onnx", str(caught.exception))
        self.assertIn("voices-v1.0.bin", str(caught.exception))

    def test_text_is_the_trailing_positional_argument(self):
        cmd = self.build().build_command("hello there", "/tmp/a.wav")
        self.assertEqual(cmd[0], "/usr/bin/kokoro-tts")
        self.assertEqual(cmd[-1], "hello there")

    def test_output_flag(self):
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertIn("-o", cmd)
        self.assertEqual(cmd[cmd.index("-o") + 1], "/tmp/a.wav")

    def test_voice_and_language_flags(self):
        cmd = self.build(voice="ef_dora", lang="es").build_command("hi", "/tmp/a.wav")
        self.assertIn("-v", cmd)
        self.assertIn("ef_dora", cmd)
        self.assertIn("-l", cmd)
        self.assertIn("es", cmd)

    def test_no_voice_or_lang_flags_when_unconfigured(self):
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertNotIn("-v", cmd)
        self.assertNotIn("-l", cmd)

    def test_explicit_voice_argument_overrides_the_configured_one(self):
        cmd = self.build(voice="ef_dora").build_command("hi", "/tmp/a.wav", voice="am_adam")
        self.assertIn("am_adam", cmd)
        self.assertNotIn("ef_dora", cmd)

    def test_default_speed_is_omitted_non_default_is_passed(self):
        default_cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertNotIn("-s", default_cmd)
        fast_cmd = self.build(speed=1.3).build_command("hi", "/tmp/a.wav")
        self.assertIn("-s", fast_cmd)
        self.assertIn("1.3", fast_cmd)

    def test_check_ok_without_a_model_dir_configured(self):
        ok, message = self.build().check()
        self.assertTrue(ok)

    def test_check_reports_a_bad_model_dir_when_one_is_configured(self):
        ok, message = self.build(model_dir="/definitely/not/here").check()
        self.assertFalse(ok)
        self.assertIn("model_dir", message)

    def test_real_installed_kokoro_wrapper_matches_this_shape(self):
        # The exact command this project's own `local-tts-configure` sets up, and what
        # was previously wired through the generic `command` provider before this
        # provider existed -- see MigrationTest for the detection side of that.
        cmd = self.build(voice="ef_dora", lang="es").build_command("hola", "/tmp/a.wav")
        self.assertEqual(cmd, ["/usr/bin/kokoro-tts", "-o", "/tmp/a.wav",
                              "-v", "ef_dora", "-l", "es", "hola"])

    def test_server_mode_posts_text_voice_lang_speed_and_writes_the_response(self):
        server = _FakeAudioServer("/synthesize", audio_bytes=b"kokoro-server-audio")
        self.addCleanup(server.stop)
        provider = self.build(voice="ef_dora", lang="es", speed=1.3, server_url=server.url)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            provider.synthesize("hola", out)
            self.assertEqual(open(out, "rb").read(), b"kokoro-server-audio")
        self.assertEqual(server.requests, [{"text": "hola", "voice": "ef_dora",
                                            "lang": "es", "speed": 1.3}])

    def test_server_mode_never_spawns_the_subprocess_binary(self):
        # build()'s resolve_binary stub points at a path that doesn't exist on disk, so
        # if synthesize() mistakenly took the subprocess route despite server_url being
        # set, self.run() would raise "command not found" here.
        server = _FakeAudioServer("/synthesize")
        self.addCleanup(server.stop)
        provider = self.build(server_url=server.url)
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("hola", os.path.join(tmp, "out.wav"))   # must not raise

    def test_server_mode_auto_starts_when_configured_and_unreachable(self):
        provider = self.build(server_url="http://127.0.0.1:1", server_start="")
        with self.assertRaises(TTSError) as caught:
            provider.synthesize("hola", "/tmp/x.wav")
        self.assertIn("server_start", str(caught.exception))

    def test_check_reports_server_reachable(self):
        server = _FakeAudioServer("/synthesize")
        self.addCleanup(server.stop)
        ok, message = self.build(server_url=server.url).check()
        self.assertTrue(ok)
        self.assertIn("already running", message)

    def test_check_reports_server_will_autostart(self):
        ok, message = self.build(server_url="http://127.0.0.1:1",
                                 server_start="echo hi").check()
        self.assertTrue(ok)
        self.assertIn("starts automatically", message)

    def test_check_reports_server_down_with_no_start_command(self):
        ok, message = self.build(server_url="http://127.0.0.1:1").check()
        self.assertFalse(ok)


class RvcProviderTest(unittest.TestCase):
    def build(self, cfg=None, lang="", **settings):
        merged = dict(config.DEFAULTS["providers"]["rvc"])
        merged.update(settings)
        cfg = cfg if cfg is not None else {"provider": "piper", "providers": config.DEFAULTS["providers"]}
        provider = RvcProvider(merged, cfg=cfg, lang=lang)
        provider._python = lambda: "/usr/bin/python3"
        return provider

    # -- multi-model servers ------------------------------------------------
    # One server can hold several voices resident and pick one per request, so a
    # second language costs a dict entry rather than a second copy of torch.

    def test_no_model_name_when_nothing_is_configured(self):
        # Back-compat: a single-model server must keep receiving no "model" key.
        self.assertEqual(self.build().server_model_name(), "")

    def test_language_models_pick_the_voice_for_this_call(self):
        provider = self.build(lang="es", language_models={"es": "cortana-es", "en": "jarvis"})
        self.assertEqual(provider.server_model_name(), "cortana-es")

    def test_exact_language_tag_beats_the_base_language(self):
        provider = self.build(lang="es-MX", language_models={"es": "cortana-es", "es-MX": "mex"})
        self.assertEqual(provider.server_model_name(), "mex")

    def test_base_language_is_used_when_the_exact_tag_is_unknown(self):
        provider = self.build(lang="es-AR", language_models={"es": "cortana-es"})
        self.assertEqual(provider.server_model_name(), "cortana-es")

    def test_server_model_is_the_fallback_without_a_lang(self):
        provider = self.build(server_model="jarvis", language_models={"es": "cortana-es"})
        self.assertEqual(provider.server_model_name(), "jarvis")

    def test_language_models_win_over_the_flat_default(self):
        provider = self.build(lang="es", server_model="jarvis",
                              language_models={"es": "cortana-es"})
        self.assertEqual(provider.server_model_name(), "cortana-es")

    def test_python_interpreter_is_required(self):
        provider = RvcProvider(dict(config.DEFAULTS["providers"]["rvc"]), cfg={})
        with self.assertRaises(TTSError) as caught:
            provider.build_command("in.wav", "out.wav")
        self.assertIn("rvc.python", str(caught.exception))

    def test_model_is_required(self):
        with self.assertRaises(TTSError) as caught:
            self.build().build_command("in.wav", "out.wav")
        self.assertIn("rvc.model", str(caught.exception))

    def test_missing_model_file_is_reported(self):
        with self.assertRaises(TTSError):
            self.build(model="/definitely/not/here.pth").build_command("in.wav", "out.wav")

    def test_full_command_shape(self):
        with tempfile.NamedTemporaryFile(suffix=".pth") as model, \
                tempfile.NamedTemporaryFile(suffix=".index") as index:
            cmd = self.build(model=model.name, index=index.name, device="cuda:0",
                             pitch=2).build_command("in.wav", "out.wav")
        self.assertEqual(cmd[:4], ["/usr/bin/python3", "-m", "rvc_python", "cli"])
        self.assertIn("-i", cmd)
        self.assertIn("in.wav", cmd)
        self.assertIn("-o", cmd)
        self.assertIn("out.wav", cmd)
        self.assertIn("-mp", cmd)
        self.assertIn(model.name, cmd)
        self.assertIn("-ip", cmd)
        self.assertIn(index.name, cmd)
        self.assertIn("-de", cmd)
        self.assertIn("cuda:0", cmd)
        self.assertIn("-pi", cmd)
        self.assertIn("2", cmd)

    def test_default_base_provider_is_the_cfgs_own_default(self):
        cfg = {"provider": "openai", "providers": config.DEFAULTS["providers"]}
        provider = RvcProvider(dict(config.DEFAULTS["providers"]["rvc"]), cfg=cfg)
        self.assertEqual(provider._base_name(), "openai")

    def test_explicit_base_provider_overrides_the_default(self):
        cfg = {"provider": "openai", "providers": config.DEFAULTS["providers"]}
        settings = dict(config.DEFAULTS["providers"]["rvc"])
        settings["base_provider"] = "llamacpp"
        provider = RvcProvider(settings, cfg=cfg)
        self.assertEqual(provider._base_name(), "llamacpp")

    def test_base_provider_cannot_be_rvc_itself(self):
        provider = self.build(base_provider="rvc")
        with self.assertRaises(TTSError) as caught:
            provider.base_provider_instance()
        self.assertIn("itself", str(caught.exception))

    def test_synthesize_chains_the_base_provider_then_converts(self):
        calls = []

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1

            def synthesize(self, text, out_path, voice=None):
                calls.append(("base", text, out_path))
                with open(out_path, "wb") as fh:
                    fh.write(b"RIFF....WAVEfake")
                return out_path

        with tempfile.NamedTemporaryFile(suffix=".pth") as model:
            provider = self.build(model=model.name)
            provider.base_provider_instance = lambda: FakeBase()

            def fake_run(cmd, stdin_text=None, cwd=None):
                calls.append(("convert", cmd))
                with open(cmd[cmd.index("-o") + 1], "wb") as fh:
                    fh.write(b"converted")
                return None
            provider.run = fake_run

            provider.synthesize("hello", "/tmp/rvc-out.wav")
        os.unlink("/tmp/rvc-out.wav")

        self.assertEqual(calls[0][0], "base")
        self.assertEqual(calls[1][0], "convert")
        # the temp base wav must not survive the call
        base_wav_path = calls[0][2]
        self.assertFalse(os.path.exists(base_wav_path))

    def test_check_reports_missing_python(self):
        provider = RvcProvider(dict(config.DEFAULTS["providers"]["rvc"]), cfg={})
        ok, message = provider.check()
        self.assertFalse(ok)
        self.assertIn("rvc.python", message)

    def test_server_mode_converts_the_base_wav_via_the_server(self):
        server = _FakeAudioServer("/convert", audio_bytes=b"rvc-server-audio")
        self.addCleanup(server.stop)

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1

            def synthesize(self, text, out_path, voice=None):
                with open(out_path, "wb") as fh:
                    fh.write(b"RIFF....WAVEfake")
                return out_path

        provider = self.build(server_url=server.url, pitch=3, device="cuda:0")
        provider.base_provider_instance = lambda: FakeBase()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            provider.synthesize("hello", out)
            self.assertEqual(open(out, "rb").read(), b"rvc-server-audio")
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(server.requests[0]["pitch"], 3)
        # nothing configured -> no "model" key at all, so an older single-model
        # server sees byte-for-byte the request it always did
        self.assertNotIn("model", server.requests[0])
        self.assertEqual(server.requests[0]["device"], "cuda:0")
        self.assertTrue(server.requests[0]["input_path"].endswith(".wav"))

    def test_server_mode_names_the_voice_for_this_language(self):
        server = _FakeAudioServer("/convert", audio_bytes=b"es-audio")
        self.addCleanup(server.stop)

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1

            def synthesize(self, text, out_path, voice=None):
                with open(out_path, "wb") as fh:
                    fh.write(b"RIFF....WAVEfake")
                return out_path

        provider = self.build(server_url=server.url, lang="es",
                              language_models={"es": "cortana-es", "en": "jarvis"})
        provider.base_provider_instance = lambda: FakeBase()
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("hola", os.path.join(tmp, "out.wav"))
        self.assertEqual(server.requests[0]["model"], "cortana-es")

    def test_each_tone_segment_is_converted_separately(self):
        # rvc never sees text, so if the whole utterance were converted as one wav every
        # segment would come out with one flat tone. Each tagged span must make its own
        # trip through the converter, and the pieces are joined into a single file.
        server = _FakeAudioServer("/convert", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1
            supports_tone_tags = False
            settings = {}

            def synthesize(self, text, out_path, voice=None):
                with open(out_path, "wb") as fh:
                    fh.write(_wav_bytes(1))
                return out_path

        provider = self.build(server_url=server.url)
        provider.base_provider_instance = lambda: FakeBase()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            textutil.synthesize_chunked(
                provider, "<happy>Great news!</happy> <sad>But also this.</sad>", out)
            self.assertTrue(os.path.getsize(out) > 0)
        self.assertEqual(len(server.requests), 2, "one conversion per tone segment")

    def test_tag_speed_is_realized_by_the_base_not_by_stretching(self):
        """A tag's pacing is handed to the base provider's own rate control instead of
        being time-stretched onto the converted wav afterwards.

        Conversion is frame-wise and preserves duration, so speed chosen before it
        survives it -- and asking piper to speak slowly is a real prosody change, where
        stretching the rendered audio is a lossy pass over every sample. Volume cannot go
        the same way: rvc renormalizes amplitude, so it stays a post-conversion step.
        """
        server = _FakeAudioServer("/convert", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        seen = []

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1
            supports_tone_tags = False

            def __init__(self, settings=None):
                self.settings = settings or {}
                self.verbose = False
                self.cfg = None
                self.lang = ""

            def speed_settings(self, speed):
                return {"length_scale": 1.0 / speed}

            def with_settings(self, overrides):
                return FakeBase(dict(self.settings, **overrides))

            def synthesize(self, text, out_path, voice=None):
                seen.append(self.settings.get("length_scale"))
                with open(out_path, "wb") as fh:
                    fh.write(_wav_bytes(1))
                return out_path

        provider = self.build(server_url=server.url)
        provider.base_provider_instance = lambda: FakeBase()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            textutil.synthesize_chunked(
                provider, "<tired>Slow.</tired> <urgent>Fast!</urgent> Plain.", out)

        tired = textutil.tag_profile("tired")["speed"]
        urgent = textutil.tag_profile("urgent")["speed"]
        self.assertEqual(len(seen), 3)
        self.assertAlmostEqual(seen[0], 1.0 / tired, places=6)
        self.assertAlmostEqual(seen[1], 1.0 / urgent, places=6)
        self.assertIsNone(seen[2], "untagged text must not be re-rated")

    def test_a_base_without_a_rate_control_still_falls_back_to_stretching(self):
        # The base is duck-typed, so anything without speed_settings keeps the old path
        # rather than losing the tag entirely.
        server = _FakeAudioServer("/convert", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        applied = []

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1
            supports_tone_tags = False
            settings = {}

            def synthesize(self, text, out_path, voice=None):
                with open(out_path, "wb") as fh:
                    fh.write(_wav_bytes(1))
                return out_path

        provider = self.build(server_url=server.url)
        provider.base_provider_instance = lambda: FakeBase()
        with unittest.mock.patch.object(
                audiofx, "apply_profile",
                side_effect=lambda path, speed=1.0, volume=1.0: applied.append(speed)):
            with tempfile.TemporaryDirectory() as tmp:
                textutil.synthesize_chunked(
                    provider, "<tired>Slow.</tired>", os.path.join(tmp, "out.wav"))
        self.assertEqual(applied, [textutil.tag_profile("tired")["speed"]])

    def test_server_mode_does_not_require_python_or_model_configured(self):
        # Server mode is a fully separate path from the subprocess CLI fallback -- the
        # model is fixed by whatever the server was started with, not rvc.model/rvc.python.
        server = _FakeAudioServer("/convert")
        self.addCleanup(server.stop)

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1

            def synthesize(self, text, out_path, voice=None):
                open(out_path, "wb").write(b"fake")
                return out_path

        settings = {k: v for k, v in config.DEFAULTS["providers"]["rvc"].items()}
        settings["server_url"] = server.url
        cfg = {"provider": "piper", "providers": config.DEFAULTS["providers"]}
        provider = RvcProvider(settings, cfg=cfg)
        provider.base_provider_instance = lambda: FakeBase()
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("hello", os.path.join(tmp, "out.wav"))   # must not raise

    def test_check_reports_server_reachable(self):
        server = _FakeAudioServer("/convert")
        self.addCleanup(server.stop)
        ok, message = self.build(server_url=server.url).check()
        self.assertTrue(ok)
        self.assertIn("already running", message)
        self.assertIn("base voice from", message)


def _wav_bytes(marker_byte, frames=50):
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(bytes([marker_byte, 0]) * frames)
    return buf.getvalue()


class OpenAIProviderTest(unittest.TestCase):
    def build(self, server, **overrides):
        settings = dict(config.DEFAULTS["providers"]["openai"])
        settings["base_url"] = server.url
        settings.update(overrides)
        return OpenAIProvider(settings)

    def test_plain_call_sends_no_instructions_field(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server)
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("hello there", os.path.join(tmp, "out.wav"))
        self.assertEqual(len(server.requests), 1)
        self.assertNotIn("instructions", server.requests[0])
        self.assertEqual(server.requests[0]["input"], "hello there")

    def test_flat_tone_setting_is_sent_as_instructions(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server, model="gpt-4o-mini-tts", tone="speak cheerfully")
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("hello there", os.path.join(tmp, "out.wav"))
        self.assertEqual(server.requests[0]["instructions"], "speak cheerfully")

    def test_tone_is_rejected_for_a_model_that_does_not_support_it(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server, model="tts-1", tone="speak cheerfully")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TTSError):
                provider.synthesize("hello there", os.path.join(tmp, "out.wav"))
        self.assertEqual(server.requests, [], "must not call the API with a field it rejects")

    def test_explicit_tag_makes_one_call_per_segment(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server, model="gpt-4o-mini-tts")
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "out.wav")
            provider.synthesize("<anger>Stop that!</anger> Then I calmed down.", out_path)
            self.assertEqual(len(server.requests), 2)
            anger = textutil.tag_profile("anger")
            self.assertEqual(server.requests[0]["input"], "Stop that!")
            self.assertEqual(server.requests[0]["instructions"], anger["instructions"])
            self.assertAlmostEqual(server.requests[0]["speed"], anger["speed"])
            self.assertEqual(server.requests[1]["input"], "Then I calmed down.")
            self.assertNotIn("instructions", server.requests[1])
            self.assertNotIn("speed", server.requests[1])
            # The joined result must be real, playable wav -- not just "some bytes".
            self.assertGreater(audio.duration(out_path), 0)

    def test_response_format_is_forced_to_wav_for_multi_segment_joins(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server, model="gpt-4o-mini-tts")
        with tempfile.TemporaryDirectory() as tmp:
            # .mp3 extension -- the multi-segment path must still request wav internally
            # (concat_wavs can only join real wav) and write valid wav bytes regardless.
            out_path = os.path.join(tmp, "out.mp3")
            provider.synthesize("<anger>Stop!</anger> Calm now.", out_path)
        for request in server.requests:
            self.assertEqual(request["response_format"], "wav")

    def test_single_segment_uses_the_requested_response_format(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server)
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("hello there", os.path.join(tmp, "out.mp3"))
        self.assertEqual(server.requests[0]["response_format"], "mp3")

    def test_auto_tone_applies_when_enabled(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server, model="gpt-4o-mini-tts", auto_tone=True)
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("Is this on?", os.path.join(tmp, "out.wav"))
        self.assertEqual(server.requests[0]["instructions"],
                         textutil.tag_profile("question")["instructions"])

    def test_auto_tone_off_by_default(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server, model="gpt-4o-mini-tts")
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("Is this on?", os.path.join(tmp, "out.wav"))
        self.assertNotIn("instructions", server.requests[0])

    def test_malformed_tag_raises_before_any_request(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        provider = self.build(server, model="gpt-4o-mini-tts")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(textutil.ToneTagError):
                provider.synthesize("<anger>oops", os.path.join(tmp, "out.wav"))
        self.assertEqual(server.requests, [])

    def test_dry_run_shows_segments_without_calling_the_api(self):
        server = _FakeAudioServer("/audio/speech", audio_bytes=_wav_bytes(1))
        self.addCleanup(server.stop)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "provider": "openai", "play": False, "player": "",
                "providers": {**config.DEFAULTS["providers"],
                              "openai": {**config.DEFAULTS["providers"]["openai"],
                                        "base_url": server.url, "model": "gpt-4o-mini-tts"}},
            }
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as fh:
                json.dump(cfg, fh)
            os.environ["LOCALTTS_CONFIG"] = path
            try:
                self.assertEqual(main(["--dry-run", "<anger>Stop!</anger> Calm now."]), 0)
            finally:
                os.environ.pop("LOCALTTS_CONFIG", None)
        self.assertEqual(server.requests, [])


class SupportsToneTagsTest(unittest.TestCase):
    """A provider with no real tone/emotion hook must never see a literal <tag> -- see
    text.synthesize_chunked() and Provider.supports_tone_tags."""

    def test_expected_support_per_provider(self):
        expected = {
            "llamacpp": False,   # no speed/style flag exists at all (verified via --help)
            "openai": True,      # instructions field [+ speed], gpt-4o-mini-tts only
            "piper": True,       # --length-scale / --volume are real flags
            "kokoro": True,      # -s/speed is real; no volume/pitch knob exists
            "rvc": True,         # splits on tags itself, shapes each converted span (audiofx)
            "command": False,    # user's call via command.tone_tags, default "strip"
        }
        self.assertEqual(set(expected), set(providers.names()), "update this test too")
        for name, want in expected.items():
            provider = providers.build(name, config.DEFAULTS)
            self.assertEqual(provider.supports_tone_tags, want, name)

    def test_kokoro_never_sees_a_literal_tag(self):
        provider = KokoroProvider(dict(config.DEFAULTS["providers"]["kokoro"], binary="kokoro-tts"))
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd)
            with open(cmd[cmd.index("-o") + 1], "wb") as fh:
                fh.write(_wav_bytes(1))
            return None

        provider.run = fake_run
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "out.wav")
            textutil.synthesize_chunked(provider, "<anger>Stop that!</anger> Calm now.", out_path)
        # <anger> has a real speed preset for kokoro, so this is two separate calls
        # (one per segment), each with the plain words and no literal markup in either.
        self.assertEqual(len(seen), 2)
        joined_texts = " ".join(cmd[-1] for cmd in seen)
        self.assertNotIn("<anger>", joined_texts)
        self.assertNotIn("</anger>", joined_texts)
        self.assertIn("Stop that!", joined_texts)
        self.assertIn("Calm now.", joined_texts)
        self.assertIn("-s", seen[0])   # the <anger>-adjusted segment carries a speed flag
        self.assertNotIn("-s", seen[1])   # the plain "Calm now." segment does not

    def test_command_strips_tags_by_default(self):
        provider = CommandProvider({"template": "fake-cmd -w {output} {text}"})
        self.assertFalse(provider.supports_tone_tags)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            with open(cmd[cmd.index("-w") + 1], "wb") as fh:
                fh.write(_wav_bytes(1))
            return None

        provider.run = fake_run
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "out.wav")
            textutil.synthesize_chunked(provider, "<anger>Stop that!</anger>", out_path)
        self.assertNotIn("<anger>", seen["cmd"])
        self.assertIn("Stop that!", seen["cmd"])

    def test_command_tone_tags_pass_through_when_configured(self):
        provider = CommandProvider({"template": "fake-cmd -w {output} {text}",
                                    "tone_tags": "pass"})
        self.assertTrue(provider.supports_tone_tags)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            with open(cmd[cmd.index("-w") + 1], "wb") as fh:
                fh.write(_wav_bytes(1))
            return None

        provider.run = fake_run
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "out.wav")
            textutil.synthesize_chunked(provider, "<anger>Stop that!</anger>", out_path)
        self.assertIn("<anger>Stop that!</anger>", seen["cmd"])

    def test_command_default_tone_tags_setting_is_strip(self):
        self.assertEqual(config.DEFAULTS["providers"]["command"]["tone_tags"], "strip")


class CommandProviderTest(unittest.TestCase):
    def test_text_cannot_inject_extra_arguments(self):
        provider = CommandProvider({"template": "espeak-ng -w {output} {text}"})
        cmd = provider.build_command("hi; rm -rf /", "/tmp/a.wav")
        self.assertEqual(cmd, ["espeak-ng", "-w", "/tmp/a.wav", "hi; rm -rf /"])

    def test_check_rejects_a_binary_that_is_not_installed(self):
        provider = CommandProvider({"template": "definitely-not-installed-xyz -w {output} {text}"})
        ok, message = provider.check()
        self.assertFalse(ok)
        self.assertIn("not on PATH", message)

    def test_missing_binary_is_a_clean_error_not_a_traceback(self):
        provider = CommandProvider({"template": "definitely-not-installed-xyz -w {output} {text}"})
        with self.assertRaises(TTSError) as caught:
            provider.synthesize("hola", "/tmp/nope.wav")
        self.assertIn("command not found", str(caught.exception))

    def test_template_must_have_placeholders(self):
        with self.assertRaises(TTSError):
            CommandProvider({"template": "espeak-ng -w {output}"}).build_command("hi", "/tmp/a.wav")


class MarkdownTest(unittest.TestCase):
    def strip(self, raw):
        return textutil.strip_markdown(raw)

    def test_code_fences_are_dropped_entirely(self):
        out = self.strip("Antes.\n\n```python\nprint('no leer')\n```\n\nDespués.")
        self.assertNotIn("print", out)
        self.assertIn("Antes.", out)
        self.assertIn("Después.", out)

    def test_links_keep_their_label_and_lose_the_url(self):
        out = self.strip("Ver [la documentación](https://example.com/x) aquí.")
        self.assertIn("la documentación", out)
        self.assertNotIn("example.com", out)

    def test_emphasis_and_headings_lose_their_markers(self):
        out = self.strip("## Título\n\nCon **negrita** y *cursiva* y `código`.")
        for marker in ("#", "**", "`"):
            self.assertNotIn(marker, out)
        self.assertIn("negrita", out)
        self.assertIn("cursiva", out)

    def test_bullets_and_quotes_lose_their_markers(self):
        out = self.strip("- uno\n- dos\n\n> citado")
        self.assertNotIn("-", out)
        self.assertNotIn(">", out)
        self.assertIn("uno", out)
        self.assertIn("citado", out)

    def test_plain_prose_survives_unchanged(self):
        prose = "Una frase normal, con comas y puntos. Y otra."
        self.assertEqual(self.strip(prose), prose)

    def test_markdown_detection_by_suffix(self):
        self.assertTrue(textutil.looks_like_markdown("/tmp/notes.MD"))
        self.assertFalse(textutil.looks_like_markdown("/tmp/notes.txt"))


class ToneSegmentsTest(unittest.TestCase):
    def profile(self, name):
        return textutil.tag_profile(name)

    def test_plain_text_is_a_single_untagged_segment(self):
        self.assertEqual(textutil.resolve_tone_segments("Hello there."), [("Hello there.", None)])

    def test_explicit_tag_isolates_its_span(self):
        segments = textutil.resolve_tone_segments("<anger>Stop that!</anger> Then I calmed down.")
        self.assertEqual(segments, [
            ("Stop that!", self.profile("anger")),
            ("Then I calmed down.", None),
        ])

    def test_unknown_tag_gets_a_generic_instructions_only_profile(self):
        segments = textutil.resolve_tone_segments("<zorbaxian>Odd word.</zorbaxian>")
        self.assertEqual(segments, [
            ("Odd word.", {"instructions": "Speak in a tone that conveys zorbaxian.",
                          "speed": 1.0, "volume": 1.0}),
        ])

    def test_nested_tags_combine_phrases_and_multiply_speed_volume(self):
        segments = textutil.resolve_tone_segments("<serious><question>Are you sure?</question></serious>")
        self.assertEqual(len(segments), 1)
        chunk, profile = segments[0]
        self.assertEqual(chunk, "Are you sure?")
        serious, question = self.profile("serious"), self.profile("question")
        self.assertEqual(profile["instructions"], serious["instructions"] + " " + question["instructions"])
        self.assertAlmostEqual(profile["speed"], serious["speed"] * question["speed"])
        self.assertAlmostEqual(profile["volume"], serious["volume"] * question["volume"])

    def test_auto_tone_classifies_by_trailing_punctuation(self):
        segments = textutil.resolve_tone_segments(
            "This is a statement. Is this a question? Wow, exciting!", auto_tone=True)
        self.assertEqual(segments, [
            ("This is a statement.", None),   # "assertion" is the built-in neutral profile
            ("Is this a question?", self.profile("question")),
            ("Wow, exciting!", self.profile("exclamation")),
        ])

    def test_auto_tone_off_by_default_leaves_punctuation_alone(self):
        segments = textutil.resolve_tone_segments("This is a statement. Is this a question?")
        self.assertEqual(segments, [("This is a statement. Is this a question?", None)])

    def test_explicit_tag_wins_over_auto_tone_inside_its_span(self):
        segments = textutil.resolve_tone_segments(
            "<whisper>Is this quiet? So quiet!</whisper> Loud now.", auto_tone=True)
        self.assertEqual(segments, [
            ("Is this quiet? So quiet!", self.profile("whisper")),
            ("Loud now.", None),
        ])

    def test_tag_name_matching_an_auto_tone_category_reuses_its_phrase(self):
        segments = textutil.resolve_tone_segments("<question>Are you sure?</question>")
        self.assertEqual(segments, [("Are you sure?", self.profile("question"))])

    def test_escaped_angle_brackets_are_taken_literally(self):
        segments = textutil.resolve_tone_segments(r"Please use \<anger\> like this.")
        self.assertEqual(segments, [("Please use <anger> like this.", None)])

    def test_escaped_backslash(self):
        segments = textutil.resolve_tone_segments(r"A literal backslash: \\ there.")
        self.assertEqual(segments, [("A literal backslash: \\ there.", None)])

    def test_unclosed_tag_raises(self):
        with self.assertRaises(textutil.ToneTagError):
            textutil.resolve_tone_segments("<anger>oops")

    def test_mismatched_close_tag_raises(self):
        with self.assertRaises(textutil.ToneTagError):
            textutil.resolve_tone_segments("<anger>oops</question>")

    def test_tone_tag_error_is_a_tts_error(self):
        self.assertTrue(issubclass(textutil.ToneTagError, TTSError))

    def test_strip_tone_tags_keeps_words_drops_markup(self):
        out = textutil.strip_tone_tags("<anger>Stop that!</anger> Then I calmed down.")
        self.assertEqual(out, "Stop that! Then I calmed down.")

    def test_strip_tone_tags_unescapes_literal_brackets(self):
        out = textutil.strip_tone_tags(r"Please use \<anger\> like this.")
        self.assertEqual(out, "Please use <anger> like this.")

    def test_strip_tone_tags_also_raises_on_malformed_markup(self):
        with self.assertRaises(textutil.ToneTagError):
            textutil.strip_tone_tags("<anger>oops")


class ChunkTest(unittest.TestCase):
    def test_zero_limit_means_one_piece(self):
        self.assertEqual(textutil.chunks("a b c d", 0), ["a b c d"])

    def test_every_piece_respects_the_limit(self):
        prose = " ".join("palabra%d" % i for i in range(200)) + "."
        for piece in textutil.chunks(prose, 25):
            self.assertLessEqual(len(piece.split()), 25)

    def test_no_words_are_lost(self):
        prose = ("Primera frase corta. Segunda frase un poco más larga, con comas. "
                 "Tercera.\n\nOtro párrafo distinto.")
        self.assertEqual(" ".join(textutil.chunks(prose, 6)).split(), prose.split())

    def test_sentences_are_kept_whole_when_they_fit(self):
        pieces = textutil.chunks("Uno dos tres. Cuatro cinco seis. Siete ocho nueve.", 3)
        self.assertEqual(pieces, ["Uno dos tres.", "Cuatro cinco seis.", "Siete ocho nueve."])


class ConcatTest(unittest.TestCase):
    def make(self, path, seconds):
        import wave
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(b"\x01\x00" * int(24000 * seconds))
        return path

    def test_joined_duration_includes_the_gaps(self):
        with tempfile.TemporaryDirectory() as work:
            parts = [self.make(os.path.join(work, "%d.wav" % i), 1.0) for i in range(3)]
            out = os.path.join(work, "joined.wav")
            audio.concat_wavs(parts, out, gap_seconds=0.5)
            self.assertAlmostEqual(audio.duration(out), 3 * 1.0 + 2 * 0.5, places=2)

    def test_joining_nothing_is_an_error(self):
        with self.assertRaises(TTSError):
            audio.concat_wavs([], "/tmp/never.wav")


def _write_wav(path, marker_byte, frames=50):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(bytes([marker_byte, 0]) * frames)


class SynthesizeConcurrencyTest(unittest.TestCase):
    """Exercises cli._synthesize's chunk-and-join path directly, without a real backend."""

    def _run(self, provider, text):
        args = types.SimpleNamespace(voice=None)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            _synthesize(provider, text, out, args)
            with wave.open(out, "rb") as handle:
                return handle.readframes(handle.getnframes())

    def test_single_piece_text_skips_the_chunking_machinery(self):
        calls = []

        class RecordingProvider(Provider):
            name = "recording"

            def synthesize(self, text, out_path, voice=None):
                calls.append((text, out_path))
                _write_wav(out_path, 0)
                return out_path

        provider = RecordingProvider({"max_words": 100})
        args = types.SimpleNamespace(voice=None)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            _synthesize(provider, "short text", out, args)
        self.assertEqual(calls, [("short text", out)])

    def test_chunks_run_concurrently_up_to_max_workers(self):
        active, peak = [], [0]
        lock = threading.Lock()

        class SlowProvider(Provider):
            name = "slow"

            def synthesize(self, text, out_path, voice=None):
                with lock:
                    active.append(1)
                    peak[0] = max(peak[0], len(active))
                time.sleep(0.1)
                _write_wav(out_path, 0)
                with lock:
                    active.pop()
                return out_path

        provider = SlowProvider({"max_words": 3, "max_workers": 2})
        text = " ".join("w%d" % i for i in range(12))  # -> 4 chunks of 3 words
        self._run(provider, text)
        self.assertGreater(peak[0], 1, "chunks ran serially -- concurrency isn't happening")
        self.assertLessEqual(peak[0], 2, "max_workers=2 was not respected as a concurrency cap")

    def test_output_order_matches_chunk_order_even_when_finished_out_of_order(self):
        # Chunk 0 is the slow one and finishes last; the join must still put its
        # audio first, because output order is decided by chunk index, not by
        # which worker happens to finish first.
        delays = {0: 0.15, 1: 0.0}

        class ReorderingProvider(Provider):
            name = "reorder"

            def synthesize(self, text, out_path, voice=None):
                index = int(text.split()[0][1:])
                time.sleep(delays.get(index, 0))
                _write_wav(out_path, index)
                return out_path

        provider = ReorderingProvider({"max_words": 3, "max_workers": 2})
        data = self._run(provider, "w0 a b w1 c d")
        self.assertEqual(data[0], 0)

    def test_a_chunk_failure_raises_and_pending_work_is_not_started(self):
        started = []
        lock = threading.Lock()

        class FlakyProvider(Provider):
            name = "flaky"

            def synthesize(self, text, out_path, voice=None):
                index = int(text.split()[0][1:])
                with lock:
                    started.append(index)
                if index == 0:
                    raise TTSError("boom")
                _write_wav(out_path, index)
                return out_path

        # max_workers=1 makes this deterministic: chunks run strictly in order,
        # so the failure on chunk 0 must stop chunk 1 from ever starting.
        provider = FlakyProvider({"max_words": 1, "max_workers": 1})
        with self.assertRaises(TTSError):
            self._run(provider, "w0 w1")
        self.assertEqual(started, [0])


class PlaybackControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "playback.json")
        self.original = audio.STATE_FILE
        audio.STATE_FILE = self.state
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)

    def test_idle_reports_nothing_playing(self):
        for call in (audio.playback_status, audio.stop_playback,
                     audio.pause_playback, audio.resume_playback):
            ok, message = call()
            self.assertFalse(ok, call.__name__)
            self.assertIn("nothing is playing", message)

    def test_stale_pid_is_reported_as_idle_and_cleared(self):
        with open(self.state, "w") as fh:
            json.dump({"pid": 2 ** 30, "path": "/tmp/gone.wav"}, fh)
        ok, message = audio.playback_status()
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.state))

    def test_control_a_real_background_process(self):
        import subprocess as sp
        # start_new_session=True mirrors play_detached()'s own spawn: it's what makes the
        # pid a process-group leader, which pause/resume/stop now rely on (they signal the
        # whole group so a runner's player child is reached too, see audio.play_detached()).
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                        stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                        start_new_session=(sys.platform != "win32"))
        self.addCleanup(proc.kill)
        audio._write_state(proc.pid, "/tmp/x.wav")

        ok, message = audio.playback_status()
        self.assertTrue(ok)
        self.assertIn("playing", message)

        if hasattr(signal, "SIGSTOP"):
            self.assertTrue(audio.pause_playback()[0])
            self.assertIn("paused", audio.playback_status()[1])
            self.assertTrue(audio.resume_playback()[0])
            self.assertIn("playing", audio.playback_status()[1])

        ok, message = audio.stop_playback()
        self.assertTrue(ok)
        self.assertIn("stopped", message)
        self.assertFalse(os.path.exists(self.state))
        proc.wait(timeout=5)

    def test_starting_playback_stops_the_previous_one(self):
        import subprocess as sp
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                        stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                        start_new_session=(sys.platform != "win32"))
        self.addCleanup(proc.kill)
        audio._write_state(proc.pid, "/tmp/old.wav")
        audio.stop_previous()
        proc.wait(timeout=5)
        self.assertFalse(os.path.exists(self.state))

    def test_status_reports_elapsed_and_total_duration(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0, elapsed=4.0,
                           segment_start=None)
        ok, message = audio.playback_status()
        self.assertTrue(ok)
        self.assertIn("0:04 / 0:12", message)

    def test_two_sessions_never_play_at_the_same_time(self):
        # The real scenario this guards against: two agent sessions both call `tts -b`
        # around the same moment. Without a machine-wide lock, both players would run
        # concurrently and their audio would overlap. Uses a real fake "player"
        # executable (not a mock) so play_detached()'s actual runner subprocess and
        # locking path are exercised end to end.
        script_path = os.path.join(self.tmp.name, "fake_player.py")
        log_path = os.path.join(self.tmp.name, "plays.log")
        with open(script_path, "w") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import os, time\n"
                "with open(os.environ['PLAYBACK_TEST_LOG'], 'a') as fh:\n"
                "    fh.write('start %r\\n' % time.time())\n"
                "time.sleep(0.6)\n"
                "with open(os.environ['PLAYBACK_TEST_LOG'], 'a') as fh:\n"
                "    fh.write('end %r\\n' % time.time())\n"
            )
        os.chmod(script_path, 0o755)
        os.environ["PLAYBACK_TEST_LOG"] = log_path
        self.addCleanup(os.environ.pop, "PLAYBACK_TEST_LOG", None)

        wav_path = os.path.join(self.tmp.name, "does-not-need-to-be-real.wav")
        pid_a, _ = audio.play_detached(wav_path, preferred=script_path, session="session-a")
        pid_b, _ = audio.play_detached(wav_path, preferred=script_path, session="session-b")
        self.assertIsNotNone(pid_a)
        self.assertIsNotNone(pid_b)

        # Not polling is_running(): this test process is the runners' parent and never
        # reaps them, so a finished-but-unreaped (zombie) child still answers kill(pid, 0)
        # -- a test-only artifact, since real `tts -b` usage has the CLI process exit
        # right after play_detached() returns, letting the OS reap orphans normally.
        # Both fake players together take ~1.2s serialized; wait comfortably past that.
        time.sleep(3)

        with open(log_path) as fh:
            lines = [line.split() for line in fh if line.strip()]
        self.assertEqual(len(lines), 4, "both fake players must have started and finished: %r" % lines)
        events = sorted((float(value), kind) for kind, value in lines)
        # Strictly alternating start/end/start/end proves no overlap -- two starts in a
        # row (or a start before the previous end) would mean simultaneous playback.
        self.assertEqual([kind for _, kind in events], ["start", "end", "start", "end"])

    def test_elapsed_advances_while_running(self):
        import time as _time
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0,
                           segment_start=_time.time() - 3.0)
        state = audio.read_state()
        self.assertAlmostEqual(audio._elapsed(state), 3.0, delta=0.5)

    def test_pause_freezes_elapsed_time(self):
        import time as _time
        proc_pid = os.getpid()   # a real, currently-running pid; no signal is actually sent to it here
        audio._write_state(proc_pid, "/tmp/x.wav", duration_seconds=12.0,
                           segment_start=_time.time() - 2.0)
        state = audio.read_state()
        before = audio._elapsed(state)
        # Simulate what pause_playback() records, without sending a real SIGSTOP to this test process.
        audio._write_state(proc_pid, "/tmp/x.wav", duration_seconds=12.0, paused=True,
                           elapsed=before, segment_start=None)
        _time.sleep(0.2)
        after = audio._elapsed(audio.read_state())
        self.assertAlmostEqual(before, after, delta=0.05)


class ProgressBarTest(unittest.TestCase):
    def test_format_time(self):
        self.assertEqual(audio.format_time(0), "0:00")
        self.assertEqual(audio.format_time(65), "1:05")
        self.assertEqual(audio.format_time(-3), "0:00")

    def test_bar_fills_proportionally(self):
        empty = audio.progress_bar(0, 10, width=10)
        half = audio.progress_bar(5, 10, width=10)
        full = audio.progress_bar(10, 10, width=10)
        self.assertEqual(empty.count("#"), 0)
        self.assertEqual(half.count("#"), 5)
        self.assertEqual(full.count("#"), 10)

    def test_bar_never_exceeds_full_past_the_end(self):
        over = audio.progress_bar(999, 10, width=10)
        self.assertEqual(over.count("#"), 10)

    def test_zero_duration_is_handled_without_dividing_by_zero(self):
        bar = audio.progress_bar(3, 0, width=10)
        self.assertIn("0:03", bar)

    def test_elapsed_label_is_clamped_to_the_total_not_just_the_bar_fill(self):
        # Real elapsed time can briefly exceed the file's duration between refreshes --
        # the displayed "X / Y" must never show X > Y, e.g. "0:11 / 0:09".
        bar = audio.progress_bar(11, 9, width=10)
        self.assertEqual(bar, "[##########] 0:09 / 0:09")


class RegistryTest(unittest.TestCase):
    def test_every_registered_provider_has_defaults(self):
        for name in providers.names():
            self.assertIn(name, config.DEFAULTS["providers"], name)
            self.assertIn(name, providers.DESCRIPTIONS, name)

    def test_unknown_provider_raises(self):
        with self.assertRaises(TTSError):
            providers.build("nope", config.DEFAULTS)


class LanguageMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LOCALTTS_CONFIG"] = os.path.join(self.tmp.name, "config.json")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "LOCALTTS_CONFIG", None)

    def test_record_provider_and_voice(self):
        config.set_values(["languages.es=piper:/voices/es_MX.onnx"])
        entry = config.language_entry(config.load(), "es")
        self.assertEqual(entry, {"provider": "piper", "voice": "/voices/es_MX.onnx"})

    def test_specific_region_wins_over_base(self):
        config.set_values(["languages.es=piper:/generic.onnx",
                           "languages.es-MX=piper:/mexican.onnx"])
        cfg = config.load()
        self.assertEqual(config.language_entry(cfg, "es-MX")["voice"], "/mexican.onnx")
        self.assertEqual(config.language_entry(cfg, "es_mx")["voice"], "/mexican.onnx")
        self.assertEqual(config.language_entry(cfg, "es")["voice"], "/generic.onnx")

    def test_unknown_language_is_absent_not_guessed(self):
        config.set_values(["languages.es=piper"])
        self.assertIsNone(config.language_entry(config.load(), "fr"))

    def test_forget_removes_the_entry(self):
        config.set_values(["languages.es=piper"])
        config.set_values(["languages.es="])
        self.assertEqual(config.load()["languages"], {})

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(TTSError):
            config.set_values(["languages.es=notaprovider"])

    def test_env_can_record_a_language(self):
        os.environ["LOCALTTS_LANG_DE"] = "piper:/voices/de.onnx"
        self.addCleanup(os.environ.pop, "LOCALTTS_LANG_DE", None)
        self.assertEqual(config.language_entry(config.load(), "de")["provider"], "piper")

    def test_lang_flag_selects_the_recorded_backend(self):
        config.set_values(["languages.es=piper:/voices/es.onnx"])
        self.assertEqual(main(["--lang", "es", "--dry-run", "hola"]), 1)   # piper voice missing -> clean error

    def test_lang_flag_without_a_record_is_a_clean_error(self):
        self.assertEqual(main(["--lang", "xx", "hola"]), 1)

    def test_explicit_provider_beats_the_recorded_one(self):
        config.set_values(["languages.es=piper:/voices/es.onnx"])
        self.assertEqual(main(["--lang", "es", "-p", "llamacpp", "--dry-run", "hola"]), 0)


SAMPLE_RULES = "# Mine\n\nAlways use tabs.\n"


class CompactStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = audio.STATE_FILE
        audio.STATE_FILE = os.path.join(self.tmp.name, "playback.json")
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)

    def test_empty_when_idle(self):
        self.assertEqual(audio.compact_status(), "")

    def test_non_empty_while_playing(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0, segment_start=0.0)
        status = audio.compact_status()
        self.assertIn("🔊", status)
        self.assertNotIn("\n", status)

    def test_paused_uses_a_different_icon(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0, paused=True,
                           elapsed=3.0, segment_start=None)
        self.assertIn("⏸", audio.compact_status())

    def test_elapsed_label_is_clamped_to_the_total(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=9.0,
                           elapsed=11.0, segment_start=None)
        status = audio.compact_status()
        self.assertIn("0:09/0:09", status)
        self.assertNotIn("0:11", status)


class SessionScopingTest(unittest.TestCase):
    """State files, and therefore playback control, are isolated per session so two
    concurrent sessions (two terminals, two agents) don't stop or read each other's audio."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = audio.STATE_FILE
        audio.STATE_FILE = os.path.join(self.tmp.name, "playback.json")
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)

    def test_no_session_uses_the_original_global_file(self):
        self.assertEqual(audio.state_path(), audio.STATE_FILE)
        self.assertEqual(audio.state_path(None), audio.STATE_FILE)

    def test_different_sessions_get_different_files(self):
        a = audio.state_path("session-a")
        b = audio.state_path("session-b")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, audio.STATE_FILE)

    def test_same_session_string_is_deterministic(self):
        self.assertEqual(audio.state_path("same"), audio.state_path("same"))

    def test_session_path_is_sanitized_and_bounded(self):
        path = audio.state_path("weird/../id with spaces\x00" + "x" * 500)
        self.assertNotIn("/", os.path.basename(path))
        self.assertLess(len(os.path.basename(path)), 120)

    def test_a_second_sessions_playback_does_not_stop_the_firsts(self):
        import subprocess as sp
        proc_a = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                          stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self.addCleanup(proc_a.kill)
        audio._write_state(proc_a.pid, "/tmp/a.wav", session="session-a")

        # Starting session B's playback must not touch session A's state or process.
        audio.stop_previous(session="session-b")

        self.assertTrue(audio.is_running(proc_a.pid))
        ok, _ = audio.playback_status(session="session-a")
        self.assertTrue(ok)

    def test_stop_only_affects_its_own_session(self):
        import subprocess as sp
        proc_a = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                          stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                          start_new_session=(sys.platform != "win32"))
        self.addCleanup(proc_a.kill)
        audio._write_state(proc_a.pid, "/tmp/a.wav", session="session-a")

        ok, message = audio.stop_playback(session="session-b")
        self.assertFalse(ok)
        self.assertIn("nothing is playing", message)
        self.assertTrue(audio.is_running(proc_a.pid))

        ok, _ = audio.stop_playback(session="session-a")
        self.assertTrue(ok)
        proc_a.wait(timeout=5)

    def test_compact_status_is_isolated_per_session(self):
        audio._write_state(os.getpid(), "/tmp/a.wav", duration_seconds=10.0,
                           segment_start=0.0, session="session-a")
        self.assertNotEqual(audio.compact_status(session="session-a"), "")
        self.assertEqual(audio.compact_status(session="session-b"), "")
        self.assertEqual(audio.compact_status(), "")   # the global slot is untouched


class SessionResolutionTest(unittest.TestCase):
    """_resolve_session() is where env-var auto-detection lives -- audio.py itself never
    reads the environment, so these tests must not let a real ambient session id (this
    suite runs inside an actual Claude Code session) leak into assertions about the
    no-session fallback."""

    def setUp(self):
        self.saved = {name: os.environ.pop(name, None) for name in
                      ("CLAUDE_CODE_SESSION_ID",)}
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self.saved.items():
            if value is not None:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)

    def test_no_signal_at_all_resolves_to_none(self):
        self.assertIsNone(_resolve_session(None))

    def test_explicit_flag_wins(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        self.assertEqual(_resolve_session("from-flag"), "from-flag")

    def test_env_var_is_used_when_no_flag(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        self.assertEqual(_resolve_session(None), "from-env")

    def test_stdin_json_session_id_beats_env(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        stdin = json.dumps({"session_id": "from-json"})
        self.assertEqual(_resolve_session(None, stdin_json=stdin), "from-json")

    def test_stdin_json_sessionId_camelcase_is_also_recognized(self):
        stdin = json.dumps({"sessionId": "from-json-camel"})
        self.assertEqual(_resolve_session(None, stdin_json=stdin), "from-json-camel")

    def test_malformed_stdin_json_falls_back_to_env(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        self.assertEqual(_resolve_session(None, stdin_json="not json"), "from-env")

    def test_explicit_flag_beats_stdin_json_too(self):
        stdin = json.dumps({"session_id": "from-json"})
        self.assertEqual(_resolve_session("from-flag", stdin_json=stdin), "from-flag")


class BackgroundSessionCliTest(unittest.TestCase):
    """End-to-end through main(): starting and controlling playback for two sessions
    without either one's global env leaking into the other's isolation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = audio.STATE_FILE
        audio.STATE_FILE = os.path.join(self.tmp.name, "playback.json")
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)
        self.saved_env = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.saved_env is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self.saved_env

    def test_compact_reads_session_from_piped_json(self):
        import io
        from unittest import mock
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=10.0,
                           segment_start=0.0, session="from-hook-json")
        stdin = io.StringIO(json.dumps({"session_id": "from-hook-json"}))
        stdin.isatty = lambda: False
        captured = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", captured):
            self.assertEqual(main(["playback", "--compact"]), 0)
        self.assertIn("🔊", captured.getvalue())

    def test_compact_is_empty_for_a_session_with_no_state(self):
        import io
        from unittest import mock
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=10.0,
                           segment_start=0.0, session="playing-session")
        stdin = io.StringIO(json.dumps({"session_id": "some-other-session"}))
        stdin.isatty = lambda: False
        captured = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", captured):
            self.assertEqual(main(["playback", "--compact"]), 0)
        self.assertEqual(captured.getvalue(), "")

    def test_explicit_session_flag_on_stop(self):
        import subprocess as sp
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                        stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                        start_new_session=(sys.platform != "win32"))
        self.addCleanup(proc.kill)
        audio._write_state(proc.pid, "/tmp/x.wav", session="explicit-session")
        self.assertEqual(main(["stop", "--session", "explicit-session"]), 0)
        proc.wait(timeout=5)


class HookInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        os.makedirs(os.path.join(self.base, ".claude"))
        self.addCleanup(self.tmp.cleanup)

    def _settings(self):
        with open(hooks.settings_path("claude-code", self.base)) as fh:
            return json.load(fh)

    def _existing_script(self, name="other-tool.sh", body="#!/usr/bin/env bash\necho hi\n"):
        path = Path(self.base) / ".claude" / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    def test_unsupported_agent_reports_why(self):
        with self.assertRaises(TTSError) as caught:
            hooks.install("gemini", base=self.base)
        self.assertIn("footer", str(caught.exception))

    def test_fresh_install_with_nothing_configured_writes_a_standalone_wrapper(self):
        result = hooks.install("claude-code", base=self.base)
        self.assertEqual(result["mode"], "standalone")
        settings = self._settings()
        self.assertEqual(settings["statusLine"]["command"], str(result["wrapper_path"]))
        self.assertEqual(settings["statusLine"]["refreshInterval"], 2)
        self.assertTrue(result["wrapper_path"].exists())

    # --- the regression this class exists for -------------------------------------
    #
    # A real Boost installation broke because install() used to REPLACE
    # statusLine.command with our own wrapper and remember the old command as a string
    # to chain via `eval`. When Boost's own reinstall regenerated its script at the same
    # path, our saved reference still pointed at the right path in principle, but Boost's
    # own installer no longer recognized statusLine as its own (something else -- us --
    # now owned it) and declined to re-register itself, leaving the whole status line
    # broken until the user manually reinstalled Boost. The fix: never touch
    # statusLine.command when one is already configured; append into the file it already
    # points to instead, so the original tool never stops owning its own slot.

    def test_existing_status_line_command_is_never_rewritten(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "otherSetting": "keep-me",
            "statusLine": {"type": "command", "command": str(script), "padding": 0},
        }))
        before = self._settings()["statusLine"]
        hooks.install("claude-code", base=self.base)
        after = self._settings()["statusLine"]
        self.assertEqual(before, after)   # byte-for-byte: command, padding, everything
        self.assertEqual(self._settings()["otherSetting"], "keep-me")

    def test_existing_script_file_gets_our_block_appended(self):
        script = self._existing_script(body="#!/usr/bin/env bash\nprintf 'BOOST'\n")
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        result = hooks.install("claude-code", base=self.base)
        self.assertEqual(result["mode"], "appended")
        content = script.read_text()
        self.assertIn("printf 'BOOST'", content)          # original untouched
        self.assertIn(hooks.HOOK_BEGIN, content)
        self.assertIn("tts playback --compact", content)

    def test_appended_output_concatenates_with_the_original_at_runtime(self):
        import subprocess as sp
        script = self._existing_script(body="#!/usr/bin/env bash\nprintf 'BOOST-OUTPUT'\n")
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base)
        out = sp.run(["bash", str(script)], input="{}", capture_output=True, text=True)
        self.assertEqual(out.stdout, "BOOST-OUTPUT")   # idle: appended block adds nothing

    def test_reinstall_does_not_duplicate_the_appended_block(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base)
        hooks.install("claude-code", base=self.base)
        self.assertEqual(script.read_text().count(hooks.HOOK_BEGIN), 1)

    def test_uninstall_removes_only_our_block_from_the_appended_file(self):
        script = self._existing_script(body="#!/usr/bin/env bash\necho original\n")
        original = script.read_text()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base)
        result = hooks.uninstall("claude-code", base=self.base)
        self.assertTrue(result["removed"])
        self.assertEqual(script.read_text(), original)
        # and the settings.json command was never touched in the first place
        self.assertEqual(self._settings()["statusLine"]["command"], str(script))

    def test_unappendable_command_requires_force(self):
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": "node ~/status.js --flag"},
        }))
        with self.assertRaises(TTSError) as caught:
            hooks.install("claude-code", base=self.base)
        self.assertIn("--force", str(caught.exception))
        # and it must not have touched anything on the refusal path
        self.assertEqual(self._settings()["statusLine"]["command"], "node ~/status.js --flag")

    def test_force_replaces_an_unappendable_command_and_chains_it(self):
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": "node ~/status.js --flag"},
        }))
        result = hooks.install("claude-code", base=self.base, force=True)
        self.assertEqual(result["mode"], "forced")
        self.assertEqual(result["chained_from"], "node ~/status.js --flag")
        self.assertEqual(self._settings()["statusLine"]["command"], str(result["wrapper_path"]))
        self.assertIn("PREV_CMD='node ~/status.js --flag'", result["wrapper_path"].read_text())

    def test_missing_script_file_also_requires_force(self):
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": str(Path(self.base) / ".claude" / "gone.sh")},
        }))
        with self.assertRaises(TTSError):
            hooks.install("claude-code", base=self.base)

    def test_dry_run_writes_nothing_standalone(self):
        result = hooks.install("claude-code", base=self.base, dry_run=True)
        self.assertFalse(result["wrapper_path"].exists())
        self.assertFalse(hooks.settings_path("claude-code", self.base).exists())

    def test_dry_run_writes_nothing_appended(self):
        script = self._existing_script()
        original = script.read_text()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base, dry_run=True)
        self.assertEqual(script.read_text(), original)

    def test_appended_mode_leaves_refresh_interval_alone_by_default(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        result = hooks.install("claude-code", base=self.base)
        self.assertIsNone(result.get("refresh_interval"))
        self.assertNotIn("refreshInterval", self._settings()["statusLine"])

    def test_appended_mode_sets_refresh_interval_only_when_explicitly_asked(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "otherSetting": "keep-me",
            "statusLine": {"type": "command", "command": str(script)},
        }))
        result = hooks.install("claude-code", base=self.base, refresh_interval=5)
        self.assertEqual(result["refresh_interval"], 5)
        settings = self._settings()
        self.assertEqual(settings["statusLine"]["refreshInterval"], 5)
        self.assertEqual(settings["statusLine"]["command"], str(script))   # command still untouched
        self.assertEqual(settings["otherSetting"], "keep-me")

    def test_explicit_refresh_interval_dry_run_writes_nothing(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base, refresh_interval=5, dry_run=True)
        self.assertNotIn("refreshInterval", self._settings()["statusLine"])

    def test_zero_means_explicitly_event_based_in_appended_mode(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": str(script), "refreshInterval": 5},
        }))
        result = hooks.install("claude-code", base=self.base, refresh_interval=0)
        self.assertEqual(result["refresh_interval"], 0)
        self.assertTrue(result["settings_changed"])
        settings = self._settings()
        self.assertNotIn("refreshInterval", settings["statusLine"])
        self.assertEqual(settings["statusLine"]["command"], str(script))   # command untouched

    def test_zero_on_an_already_event_based_config_makes_no_write(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        mtime_before = path.stat().st_mtime_ns
        result = hooks.install("claude-code", base=self.base, refresh_interval=0)
        self.assertEqual(result["refresh_interval"], 0)
        self.assertFalse(result["settings_changed"])
        self.assertEqual(path.stat().st_mtime_ns, mtime_before)   # never even opened for write

    def test_zero_means_explicitly_event_based_in_standalone_mode(self):
        result = hooks.install("claude-code", base=self.base, refresh_interval=0)
        self.assertEqual(result["refresh_interval"], 0)
        self.assertNotIn("refreshInterval", self._settings()["statusLine"])

    def test_no_flag_still_defaults_standalone_to_two_seconds(self):
        result = hooks.install("claude-code", base=self.base)
        self.assertEqual(result["refresh_interval"], 2)
        self.assertEqual(self._settings()["statusLine"]["refreshInterval"], 2)

    def test_uninstall_drops_the_key_when_there_was_nothing_before(self):
        hooks.install("claude-code", base=self.base)
        hooks.uninstall("claude-code", base=self.base)
        self.assertNotIn("statusLine", self._settings())

    def test_uninstall_refuses_to_touch_a_hook_it_did_not_install(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        result = hooks.uninstall("claude-code", base=self.base)
        self.assertFalse(result["removed"])
        self.assertEqual(self._settings()["statusLine"]["command"], str(script))

    def test_is_installed_true_only_for_our_own_wrapper_or_block(self):
        self.assertFalse(hooks.is_installed("claude-code", base=self.base))
        hooks.install("claude-code", base=self.base)
        self.assertTrue(hooks.is_installed("claude-code", base=self.base))

    def test_is_installed_true_for_appended_mode_too(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        self.assertFalse(hooks.is_installed("claude-code", base=self.base))
        hooks.install("claude-code", base=self.base)
        self.assertTrue(hooks.is_installed("claude-code", base=self.base))

    def test_qwen_uses_its_own_nested_key_and_settings_file(self):
        os.makedirs(os.path.join(self.base, ".qwen"))
        hooks.install("qwen", base=self.base)
        with open(hooks.settings_path("qwen", self.base)) as fh:
            settings = json.load(fh)
        self.assertIn("command", settings["ui"]["statusLine"])

    def test_detect_only_returns_agents_actually_present(self):
        self.assertEqual(hooks.detect(base=self.base), {"claude-code": Path(self.base) / ".claude"})


class HooksCliFlagTest(unittest.TestCase):
    def test_refresh_interval_out_of_range_is_rejected(self):
        self.assertEqual(main(["hooks", "--install", "--refresh-interval", "61"]), 1)
        self.assertEqual(main(["hooks", "--install", "--refresh-interval", "-1"]), 1)

    def test_zero_is_a_valid_refresh_interval(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, ".claude"))
            from unittest import mock
            with mock.patch.object(hooks, "home", lambda b=None: Path(base)):
                self.assertEqual(
                    main(["hooks", "--install", "claude-code", "--refresh-interval", "0"]), 0)
                with open(hooks.settings_path("claude-code")) as fh:
                    settings = json.load(fh)
                self.assertNotIn("refreshInterval", settings["statusLine"])


class HookLivenessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        os.makedirs(os.path.join(self.base, ".claude"))
        self.addCleanup(self.tmp.cleanup)

    def test_inactive_with_no_heartbeat(self):
        self.assertFalse(hooks.is_active("claude-code", base=self.base))
        self.assertFalse(hooks.any_active(base=self.base))

    def test_active_with_a_fresh_heartbeat(self):
        hooks.install("claude-code", base=self.base)
        hb = hooks.heartbeat_path("claude-code", base=self.base)
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.write_text(str(int(__import__("time").time())))
        self.assertTrue(hooks.is_active("claude-code", base=self.base))
        self.assertTrue(hooks.any_active(base=self.base))

    def test_inactive_with_a_stale_heartbeat(self):
        hooks.install("claude-code", base=self.base)
        hb = hooks.heartbeat_path("claude-code", base=self.base)
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.write_text(str(int(__import__("time").time()) - 999))
        self.assertFalse(hooks.is_active("claude-code", base=self.base))

    def test_installed_but_never_run_is_not_active(self):
        hooks.install("claude-code", base=self.base)   # config written, wrapper never executed
        self.assertTrue(hooks.is_installed("claude-code", base=self.base))
        self.assertFalse(hooks.is_active("claude-code", base=self.base))


class SkillInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_bundled_skills_have_name_and_description(self):
        for name in skills.SKILLS:
            meta, prose = skills.split_frontmatter(skills.read_skill(name))
            self.assertEqual(meta.get("name"), name)
            self.assertTrue(meta.get("description"), name)
            self.assertTrue(prose.strip(), name)

    def test_detection_only_reports_directories_that_exist(self):
        self.assertEqual(skills.detect(self.base), {})
        os.makedirs(os.path.join(self.base, ".claude"))
        self.assertEqual(sorted(skills.detect(self.base)), ["claude-code"])

    def test_skill_shaped_agent_gets_one_file_per_skill(self):
        os.makedirs(os.path.join(self.base, ".claude"))
        written = skills.install("claude-code", base=self.base)
        self.assertEqual(len(written), len(skills.SKILLS))
        for path in written:
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "SKILL.md")
        self.assertEqual(skills.status("claude-code", base=self.base)[0], True)

    def test_doc_shaped_agent_preserves_existing_content(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        target = os.path.join(self.base, ".codex", "AGENTS.md")
        with open(target, "w") as fh:
            fh.write(SAMPLE_RULES)
        skills.install("codex", base=self.base)
        body = open(target).read()
        self.assertIn("Always use tabs.", body)
        self.assertIn(skills.BEGIN, body)

    def test_reinstall_does_not_duplicate_the_section(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        skills.install("codex", base=self.base)
        first = open(os.path.join(self.base, ".codex", "AGENTS.md")).read()
        skills.install("codex", base=self.base)
        second = open(os.path.join(self.base, ".codex", "AGENTS.md")).read()
        self.assertEqual(first, second)
        self.assertEqual(second.count(skills.BEGIN), 1)

    def test_uninstall_restores_the_original_file(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        target = os.path.join(self.base, ".codex", "AGENTS.md")
        with open(target, "w") as fh:
            fh.write(SAMPLE_RULES)
        skills.install("codex", base=self.base)
        skills.uninstall("codex", base=self.base)
        self.assertEqual(open(target).read().strip(), SAMPLE_RULES.strip())

    def test_uninstall_removes_a_file_it_created_alone(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        skills.install("codex", base=self.base)
        skills.uninstall("codex", base=self.base)
        self.assertFalse(os.path.exists(os.path.join(self.base, ".codex", "AGENTS.md")))

    def test_dry_run_writes_nothing(self):
        os.makedirs(os.path.join(self.base, ".claude"))
        written = skills.install("claude-code", base=self.base, dry_run=True)
        self.assertTrue(written)
        for path in written:
            self.assertFalse(path.exists())

    def test_unknown_agent_raises(self):
        with self.assertRaises(TTSError):
            skills.install("notanagent", base=self.base)

    def test_config_root_follows_the_platform(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/xdg"}, clear=False):
            self.assertEqual(str(config.config_root()), "/xdg")
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        env["APPDATA"] = os.path.join("C:", "Users", "x", "AppData", "Roaming")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(str(config.config_root()), env["APPDATA"])
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "platform", "linux"):
            self.assertTrue(str(config.config_root()).endswith(".config"))

    def test_skills_and_cli_agree_on_the_config_root(self):
        self.assertEqual(skills.config_root(), config.config_root())

    def test_config_placeholder_is_expanded(self):
        path = skills.resolve("${CONFIG}/opencode", self.base)
        self.assertEqual(path, Path(self.base) / ".config" / "opencode")

    def test_every_agent_has_a_marker_and_a_label(self):
        self.assertEqual(sorted(skills.AGENTS), sorted(skills.MARKERS))
        for name, (kind, relative, label) in skills.AGENTS.items():
            self.assertIn(kind, ("skill", "doc"))
            self.assertTrue(relative and label, name)


class CliTest(unittest.TestCase):
    def test_no_arguments_at_a_prompt_prints_the_help(self):
        import contextlib, io
        from unittest import mock
        captured = io.StringIO()
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                contextlib.redirect_stdout(captured):
            self.assertEqual(main([]), 0)
        printed = captured.getvalue()
        self.assertIn("usage:", printed)
        for flag in ("--provider", "--output", "--markdown", "--dry-run"):
            self.assertIn(flag, printed)

    def test_piped_input_still_works_without_arguments(self):
        import io
        from unittest import mock
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            self.assertEqual(main([]), 1)   # empty pipe -> error, not help

    def test_empty_input_exits_nonzero(self):
        self.assertEqual(main(["--file", "/dev/null"]), 1)

    def test_providers_subcommand(self):
        self.assertEqual(main(["providers"]), 0)

    def test_skills_subcommand_reports_status(self):
        self.assertEqual(main(["skills"]), 0)

    def test_skills_print_outputs_the_bundled_skill_verbatim(self):
        import io
        from unittest import mock
        captured = io.StringIO()
        with mock.patch.object(sys, "stdout", captured):
            self.assertEqual(main(["skills", "--print", "local-tts-update"]), 0)
        self.assertEqual(captured.getvalue(), skills.read_skill("local-tts-update"))

    def test_skills_print_rejects_an_unknown_name(self):
        self.assertEqual(main(["skills", "--print", "not-a-real-skill"]), 1)

    def test_skills_print_cannot_combine_with_install(self):
        self.assertEqual(main(["skills", "--print", "local-tts-update", "--install"]), 1)

    def test_languages_subcommand(self):
        self.assertEqual(main(["languages"]), 0)



class AudioFxTest(unittest.TestCase):
    """Speed/volume applied to rendered audio, for backends with no such flag of their
    own. Dependency-free by design, so these must pass without ffmpeg installed."""

    def tone(self, seconds=1.0, rate=22050, amplitude=8000):
        import array, math
        path = os.path.join(tempfile.mkdtemp(), "t.wav")
        samples = array.array("h", [int(amplitude * math.sin(2 * math.pi * 220 * i / rate))
                                    for i in range(int(rate * seconds))])
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(samples.tobytes())
        return path

    def peak(self, path):
        import array
        with wave.open(path, "rb") as w:
            data = array.array("h"); data.frombytes(w.readframes(w.getnframes()))
        return max(abs(x) for x in data)

    def seconds(self, path):
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())

    def test_volume_scales_amplitude(self):
        path = self.tone()
        self.assertTrue(audiofx.apply_volume(path, 1.5))
        self.assertAlmostEqual(self.peak(path), 12000, delta=60)

    def test_volume_clamps_instead_of_wrapping(self):
        # int16 overflow is the difference between "louder" and a burst of noise.
        path = self.tone()
        audiofx.apply_volume(path, 20.0)
        self.assertLessEqual(self.peak(path), 32768)

    def test_volume_of_one_is_a_noop(self):
        self.assertFalse(audiofx.apply_volume(self.tone(), 1.0))

    def test_speed_shortens_without_ffmpeg(self):
        path = self.tone(seconds=1.0)
        self.assertTrue(audiofx.apply_speed(path, 1.5))
        self.assertAlmostEqual(self.seconds(path), 1 / 1.5, delta=0.06)

    def test_speed_below_one_lengthens(self):
        path = self.tone(seconds=1.0)
        self.assertTrue(audiofx.apply_speed(path, 0.75))
        self.assertAlmostEqual(self.seconds(path), 1 / 0.75, delta=0.08)

    def test_speed_of_one_is_a_noop(self):
        self.assertFalse(audiofx.apply_speed(self.tone(), 1.0))

    def test_apply_profile_never_raises_on_a_bad_file(self):
        broken = os.path.join(tempfile.mkdtemp(), "x.wav")
        with open(broken, "wb") as fh:
            fh.write(b"not a wav")
        self.assertFalse(audiofx.apply_profile(broken, speed=1.5, volume=2.0))

    def fundamental_share(self, path, freq=220.0):
        """Fraction of the signal's energy still at `freq`. A clean pitch-preserving
        stretch of a pure tone returns a pure tone; a blind fixed-hop overlap-add
        returns mostly phase-cancellation noise, which is what "robotic" sounds like."""
        import array
        import math
        with wave.open(path, "rb") as w:
            data = array.array("h"); data.frombytes(w.readframes(w.getnframes()))
            rate = w.getframerate()
        total = math.sqrt(sum(float(v) * v for v in data))
        if not total:
            return 0.0
        coeff = 2 * math.cos(2 * math.pi * freq / rate)
        s1 = s2 = 0.0
        for value in data:
            s1, s2 = value + coeff * s1 - s2, s1
        magnitude = math.hypot(s1 - s2 * math.cos(2 * math.pi * freq / rate),
                               s2 * math.sin(2 * math.pi * freq / rate))
        return (magnitude * math.sqrt(2.0 / len(data))) / total

    def test_fallback_stretch_preserves_the_waveform(self):
        """Regression guard. The fallback used to advance by a blind fixed hop, so every
        cross-fade summed the same harmonic at a different phase: a 220 Hz tone came back
        with ~3% of its energy still at its own frequency and the rest as buzz. WSOLA aligns each
        window to what was already emitted, which is what keeps speech sounding like
        speech on a machine with no ffmpeg."""
        self.assertGreater(self.fundamental_share(self.tone(seconds=1.0)), 0.99,
                           "control: an untouched tone must read as pure")
        for factor in (0.85, 0.95, 1.10):
            path = self.tone(seconds=1.0)
            self.assertTrue(audiofx._ola_tempo(path, factor))
            self.assertGreater(self.fundamental_share(path), 0.90,
                               "x%.2f lost the fundamental" % factor)


class ToneRealizationTest(unittest.TestCase):
    """What each backend actually does with a <tag>, checked against the audio."""

    def peak(self, path):
        import array
        with wave.open(path, "rb") as w:
            data = array.array("h"); data.frombytes(w.readframes(w.getnframes()))
        return max(abs(x) for x in data)

    def loud_wav(self, path, amplitude=10000, frames=4000):
        import array
        import math
        samples = array.array("h", [int(amplitude * math.sin(2 * math.pi * 220 * i / 22050))
                                    for i in range(frames)])
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(samples.tobytes())

    def test_kokoro_realizes_a_tag_volume(self):
        """kokoro has a real speed flag but no volume knob anywhere, and declaring
        supports_tone_tags keeps synthesize_chunked()'s audiofx pass from running for it
        -- so a tag's volume has to be applied in its own loop. It used to be dropped
        silently, which made <whisper> merely slow rather than quiet."""
        provider = KokoroProvider(dict(config.DEFAULTS["providers"]["kokoro"],
                                       binary="kokoro-tts"))
        provider.run = lambda cmd, **kw: self.loud_wav(cmd[cmd.index("-o") + 1])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            textutil.synthesize_chunked(provider, "<whisper>quiet please</whisper>", out)
            expected = 10000 * textutil.tag_profile("whisper")["volume"]
            self.assertAlmostEqual(self.peak(out), expected, delta=120)

    def test_command_output_is_not_reshaped_unless_asked(self):
        """Every other backend's capabilities are known here, so leftovers are safely
        ours to apply. A command template is somebody else's script and may already be
        acting on the tone, so its audio is left exactly as rendered by default."""
        for audio_fx, reshaped in ((False, False), (True, True)):
            provider = CommandProvider(dict(config.DEFAULTS["providers"]["command"],
                                            audio_fx=audio_fx))
            provider.run = lambda cmd, **kw: self.loud_wav(cmd[cmd.index("-w") + 1])
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out.wav")
                textutil.synthesize_chunked(provider, "<whisper>quiet</whisper>", out)
                quieter = self.peak(out) < 9000
                self.assertEqual(quieter, reshaped, "audio_fx=%s" % audio_fx)

    def test_command_audio_fx_defaults_to_off(self):
        self.assertIs(config.DEFAULTS["providers"]["command"]["audio_fx"], False)


class LanguageTagGuardTest(unittest.TestCase):
    """<en>...</en> inside another language's text (text.split_language_spans)."""

    KNOWN = ("es", "en")

    def test_an_unhandled_language_tag_never_becomes_a_tone(self):
        """The bug this guards: with language tags off, <en> was read as an unknown tone
        tag -- three segments instead of one, and a fabricated
        "Speak in a tone that conveys en." that a backend with a free-text style hook
        would genuinely send to the model."""
        for text in ("Sube el <en>pull request</en> ya",
                     "Un <it>ciao</it> aqui",
                     "a <lang:en>b</lang:en> c"):
            segments = textutil.resolve_tone_segments(text)
            self.assertEqual(len(segments), 1, "%r split for nothing" % text)
            chunk, profile = segments[0]
            self.assertIsNone(profile, "%r invented a tone" % text)
            self.assertNotIn("<", chunk, "%r left markup to be spoken" % text)
            self.assertNotIn("  ", chunk, "%r left a double space where the tag was" % text)

    def test_three_letter_tone_tags_are_not_mistaken_for_languages(self):
        """<sad> and <joy> are language-code shaped; TAG_PROFILES is what saves them."""
        for name in ("sad", "joy"):
            segments = textutil.resolve_tone_segments("<%s>x</%s>" % (name, name))
            self.assertIsNotNone(segments[0][1], name)
        # ...while an unknown *tone* tag is long enough not to look like a language, and
        # still carries its free-text instructions.
        self.assertIsNotNone(
            textutil.resolve_tone_segments("<mysterious>x</mysterious>")[0][1])

    def test_a_language_tag_inside_a_tone_tag_leaves_the_tone_alone(self):
        segments = textutil.resolve_tone_segments("<calm>a <en>b</en> c</calm>")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0][0], "a b c")
        self.assertEqual(segments[0][1]["speed"], textutil.tag_profile("calm")["speed"])

class PronunciationTest(unittest.TestCase):
    """Word respellings applied before synthesis (text.apply_pronunciations)."""

    ENTRIES = {"jarvis": "JAR-viss", "es:jarvis": "yarvis", "happy": "HAP-ee",
               "new york": "noo york"}

    def say(self, text, lang="en"):
        return textutil.apply_pronunciations(text, self.ENTRIES, lang)

    def test_bare_keys_apply_everywhere_and_scoped_keys_win(self):
        self.assertEqual(self.say("Hello Jarvis", "en"), "Hello JAR-viss")
        self.assertEqual(self.say("Hola Jarvis", "es"), "Hola yarvis")

    def test_tone_markup_is_never_rewritten(self):
        """An entry for "happy" must not turn <happy> into markup the tag parser no
        longer recognizes -- which would change what is spoken, not just how."""
        self.assertEqual(self.say("<happy>Fine</happy>"), "<happy>Fine</happy>")
        self.assertEqual(self.say("<happy>happy</happy>"), "<happy>HAP-ee</happy>")

    def test_escapes_survive(self):
        self.assertEqual(self.say(r"a \<b\> jarvis"), r"a \<b\> JAR-viss")

    def test_matching_is_whole_word_and_case_insensitive(self):
        self.assertEqual(self.say("jarvisson JARVIS"), "jarvisson JAR-viss")

    def test_longest_key_wins(self):
        self.assertEqual(textutil.apply_pronunciations("new york", self.ENTRIES, "en"),
                         "noo york")

    def test_no_entries_is_a_noop(self):
        self.assertEqual(textutil.apply_pronunciations("plain", {}, "en"), "plain")
        self.assertEqual(textutil.apply_pronunciations("plain", None, "en"), "plain")


class KokoroLanguageVoiceTest(unittest.TestCase):
    """Kokoro names voices by language, so one flat `voice` cannot serve two."""

    def build(self, call_lang, **overrides):
        """`call_lang` is the --lang of the call; `lang=` in overrides is the *flat*
        kokoro.lang setting -- the whole point of several of these tests is that those
        two are not the same thing."""
        settings = dict(config.DEFAULTS["providers"]["kokoro"],
                        language_voices={"es": "ef_dora", "en": "bm_george"}, **overrides)
        return KokoroProvider(settings, lang=call_lang)

    def test_voice_and_phonemizer_language_follow_the_call(self):
        self.assertEqual(self.build("es").resolved_voice(), "ef_dora")
        self.assertEqual(self.build("es").resolved_lang(), "es")
        self.assertEqual(self.build("en").resolved_voice(), "bm_george")
        # en-gb, not en-us: bm_ is a British voice, and the prefix is what says so.
        self.assertEqual(self.build("en").resolved_lang(), "en-gb")

    def test_a_stale_flat_lang_does_not_override_the_chosen_voice(self):
        """Picking a per-language voice and leaving `lang: es` in place is exactly how
        English gets read with Spanish phonetics."""
        self.assertEqual(self.build("en", lang="es").resolved_lang(), "en-gb")

    def test_exact_tag_beats_the_base_language(self):
        provider = self.build("es-MX")
        provider.settings["language_voices"] = {"es": "ef_dora", "es-MX": "em_alex"}
        self.assertEqual(provider.resolved_voice(), "em_alex")

    def test_unmapped_language_falls_back_to_the_flat_setting(self):
        self.assertEqual(self.build("de", voice="af_heart").resolved_voice(), "af_heart")


class DeliveryTest(unittest.TestCase):
    """Per-language pacing, over the built-in defaults."""

    def build(self, lang, delivery=None):
        settings = dict(config.DEFAULTS["providers"]["rvc"])
        if delivery is not None:
            settings["delivery"] = delivery
        return RvcProvider(settings, lang=lang)

    def test_named_language_wins_then_star_then_defaults(self):
        from localtts.providers.rvc import DELIVERY_DEFAULTS
        provider = self.build("es", {"es": {"pause_ms": 10}, "*": {"pause_ms": 99}})
        self.assertEqual(provider.delivery()["pause_ms"], 10)
        self.assertEqual(self.build("de", {"*": {"pause_ms": 99}}).delivery()["pause_ms"], 99)
        self.assertEqual(self.build("de", {}).delivery(), DELIVERY_DEFAULTS)

    def test_a_partial_entry_keeps_the_other_defaults(self):
        from localtts.providers.rvc import DELIVERY_DEFAULTS
        delivery = self.build("es", {"es": {"pause_ms": 10}}).delivery()
        self.assertEqual(delivery["pause_ms"], 10)
        self.assertEqual(delivery["pause_tone_ms"], DELIVERY_DEFAULTS["pause_tone_ms"])


    def test_silence_is_padded_onto_the_fragment(self):
        """Padding the fragment rather than inserting silence while joining is what
        keeps streamed playback and the saved file identical."""
        path = os.path.join(tempfile.mkdtemp(), "x.wav")
        with open(path, "wb") as fh:
            fh.write(_wav_bytes(1, frames=24000))
        before = audio.duration(path)
        self.assertTrue(audiofx.append_silence(path, 0.13))
        self.assertAlmostEqual(audio.duration(path), before + 0.13, delta=0.01)
        self.assertFalse(audiofx.append_silence(path, 0))


class PlayerSelectionTest(unittest.TestCase):
    """Which player autodetect picks, and how it can be overridden."""

    def test_windows_player_is_selectable_by_name(self):
        """`player=powershell.exe` used to resolve through the generic branch to
        `powershell.exe <file.wav>`, which runs the wav as a script instead of playing
        it -- so there was no way to ask for the Windows player at all."""
        with unittest.mock.patch.object(audio, "_powershell_exe", return_value="/ps"), \
             unittest.mock.patch.object(audio, "_is_wsl", return_value=False):
            for name in ("windows", "powershell", "PowerShell.exe"):
                command = audio.find_player("/tmp/x.wav", name)
                self.assertEqual(command[0], "/ps", name)
                self.assertIn("SoundPlayer", " ".join(command), name)

    def test_wsl_prefers_the_windows_player_over_an_installed_ffplay(self):
        """Installing ffmpeg -- which local-tts recommends for tone shaping -- must not
        silently demote a working player. WSL's Linux audio bridge is frequently the
        noisier of the two, so the Windows player stays the default there."""
        exe = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        with unittest.mock.patch.object(audio, "_is_wsl", return_value=True), \
             unittest.mock.patch.object(audio, "_powershell_exe", return_value=exe), \
             unittest.mock.patch.object(audio.shutil, "which",
                                        side_effect=lambda n: "/usr/bin/" + n):
            self.assertEqual(audio.find_player("/tmp/x.wav")[0], exe)
            self.assertIn("powershell.exe", audio.available_players()[0])
            # ...but only as a default: naming a Linux player still selects it.
            self.assertEqual(audio.find_player("/tmp/x.wav", "ffplay")[0], "ffplay")

    def test_player_args_are_inserted_before_the_file(self):
        """Audio stacks differ per machine and the fix is nearly always a flag, not a
        code change -- so extra player arguments are configuration. They go just before
        the file, which every builder in PLAYERS puts last."""
        with unittest.mock.patch.object(
                audio, "_player_tuning",
                return_value=({"ffplay": ["-af", "aresample=48000"]}, {})), \
             unittest.mock.patch.object(audio.shutil, "which",
                                        side_effect=lambda n: "/usr/bin/" + n):
            command = audio.find_player("/tmp/x.wav", "ffplay")
        self.assertEqual(command[-3:], ["-af", "aresample=48000", "/tmp/x.wav"])

    def test_player_env_is_applied_only_when_configured(self):
        with unittest.mock.patch.object(audio, "_player_tuning", return_value=({}, {})):
            self.assertIsNone(audio.player_environment(),
                              "no config means the child just inherits, as before")
        with unittest.mock.patch.object(
                audio, "_player_tuning",
                return_value=({}, {"SDL_AUDIODRIVER": "pulseaudio"})):
            self.assertEqual(audio.player_environment()["SDL_AUDIODRIVER"], "pulseaudio")

    def test_player_maps_are_set_and_cleared_one_entry_at_a_time(self):
        tmp = tempfile.mkdtemp()
        os.environ["LOCALTTS_CONFIG"] = os.path.join(tmp, "config.json")
        self.addCleanup(os.environ.pop, "LOCALTTS_CONFIG", None)
        config.set_values(["player_args.ffplay=-af aresample=48000"])
        config.set_values(["player_env.SDL_AUDIODRIVER=pulseaudio"])
        cfg = config.load()
        self.assertEqual(cfg["player_args"]["ffplay"], ["-af", "aresample=48000"])
        self.assertEqual(cfg["player_env"]["SDL_AUDIODRIVER"], "pulseaudio")
        config.set_values(["player_args.ffplay="])       # empty removes the entry
        self.assertEqual(config.load()["player_args"], {})

    def test_plain_linux_still_prefers_a_linux_player(self):
        with unittest.mock.patch.object(audio, "_is_wsl", return_value=False), \
             unittest.mock.patch.object(audio.sys, "platform", "linux"), \
             unittest.mock.patch.object(audio.shutil, "which",
                                        side_effect=lambda n: "/usr/bin/" + n):
            self.assertEqual(audio.find_player("/tmp/x.wav")[0], "ffplay")


class StreamPublishingTest(unittest.TestCase):
    """Fragments are published as they are rendered, so playback can start on the first
    one instead of waiting for the whole text (audio.play_stream_detached)."""

    def writer(self, flag):
        """A fake `run` that writes a tiny wav to the output named after `flag`."""
        def run(cmd, **kwargs):
            with open(cmd[cmd.index(flag) + 1], "wb") as handle:
                handle.write(_wav_bytes(1))
        return run

    def test_parts_are_published_in_order_as_they_render(self):
        provider = KokoroProvider(dict(config.DEFAULTS["providers"]["kokoro"],
                                       binary="kokoro-tts"))
        published = []
        provider.run = self.writer("-o")
        provider.on_part = lambda path: published.append(
            (len(published), os.path.exists(path)))
        with tempfile.TemporaryDirectory() as tmp:
            textutil.synthesize_chunked(
                provider, "<happy>One.</happy> <sad>Two.</sad> <urgent>Three.</urgent>",
                os.path.join(tmp, "out.wav"))
        self.assertEqual(published, [(0, True), (1, True), (2, True)])

    def test_chunked_synthesis_publishes_in_order_despite_finishing_out_of_order(self):
        """Chunks are synthesized concurrently and finish in any order, but they must be
        *heard* in order -- so a chunk is published only once every chunk before it has
        been."""
        order = []

        class SlowFirst(Provider):
            name = "slow"
            default_format = "wav"

            @property
            def max_words(self):
                return 1

            @property
            def max_workers(self):
                return 3

            def synthesize(self, text, out_path, voice=None):
                # The first word finishes last, so naive emission would invert the audio.
                time.sleep(0.25 if text == "one" else 0.01)
                with open(out_path, "wb") as fh:
                    fh.write(_wav_bytes(1))
                return out_path

        provider = SlowFirst({})
        provider.on_part = lambda path: order.append(os.path.basename(path))
        with tempfile.TemporaryDirectory() as tmp:
            textutil.synthesize_chunked(provider, "one two three",
                                        os.path.join(tmp, "out.wav"))
        self.assertEqual(order, sorted(order), "published out of order: %r" % (order,))
        self.assertEqual(len(order), 3)

    def test_a_provider_that_segments_internally_does_not_double_publish(self):
        """Whoever owns the outermost loop owns the ordering, and so owns the sink --
        otherwise the same audio is published twice and the stream stutters."""
        provider = CommandProvider(dict(config.DEFAULTS["providers"]["command"],
                                        audio_fx=True))
        provider.run = self.writer("-w")
        published = []
        provider.on_part = published.append
        with tempfile.TemporaryDirectory() as tmp:
            textutil.synthesize_chunked(provider, "<happy>One.</happy> <sad>Two.</sad>",
                                        os.path.join(tmp, "out.wav"))
        self.assertEqual(len(published), 2, "one publish per tone segment, not two")

    def test_stream_directory_round_trip(self):
        directory = audio.stream_new()
        self.addCleanup(audio.stream_cleanup, directory)
        self.assertIsNone(audio.stream_count(directory), "not finished yet")
        source = os.path.join(directory, "src.wav")
        with open(source, "wb") as fh:
            fh.write(_wav_bytes(1, frames=24000))
        audio.stream_add(directory, 0, source)
        self.assertTrue(os.path.exists(audio.stream_part_path(directory, 0)))
        self.assertGreater(audio.stream_known_duration(directory), 0.0)
        audio.stream_finish(directory, 1)
        self.assertEqual(audio.stream_count(directory), 1)

    def test_stream_setting_defaults_to_on(self):
        self.assertIs(config.DEFAULTS["stream"], True)
        self.assertIn("stream", config.TOP_LEVEL_KEYS)


class ToneFallbackTest(unittest.TestCase):
    """A backend with no tone hook still sounds different per segment, because the
    speed/volume half of a profile is applied to its rendered audio afterwards."""

    class Recorder:
        name = "rec"
        default_format = "wav"
        max_words = 0
        max_workers = 1
        supports_tone_tags = False
        realizes_speed = False
        realizes_volume = False
        settings = {}

        def __init__(self):
            self.seen = []

        def synthesize(self, text, out_path, voice=None):
            self.seen.append(text)
            with open(out_path, "wb") as fh:
                fh.write(_wav_bytes(1))
            return out_path

    def test_each_tagged_span_is_rendered_separately(self):
        provider = self.Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            textutil.synthesize_chunked(
                provider, "<happy>Yes!</happy> <sad>No.</sad>", out)
        self.assertEqual(provider.seen, ["Yes!", "No."])

    def test_no_tags_still_takes_the_single_call_path(self):
        provider = self.Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            textutil.synthesize_chunked(provider, "just plain text", os.path.join(tmp, "o.wav"))
        self.assertEqual(provider.seen, ["just plain text"])

    def test_a_provider_that_realizes_everything_is_left_alone(self):
        provider = self.Recorder()
        provider.realizes_speed = True
        provider.realizes_volume = True
        with tempfile.TemporaryDirectory() as tmp:
            textutil.synthesize_chunked(provider, "<happy>Yes!</happy> <sad>No.</sad>",
                                        os.path.join(tmp, "o.wav"))
        # nothing left for audiofx -> the old strip-and-render-once path
        self.assertEqual(len(provider.seen), 1)



class NestedConfigSetTest(unittest.TestCase):
    """`rvc.language_models.es=cortana-es` -- one entry of a dict-valued setting, so a
    per-language voice map doesn't have to be written as raw JSON."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        patcher = unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sets_one_entry(self):
        config.set_values(["rvc.language_models.es=cortana-es"])
        cfg = config.load()
        self.assertEqual(cfg["providers"]["rvc"]["language_models"], {"es": "cortana-es"})

    def test_keeps_existing_entries(self):
        config.set_values(["rvc.language_models.es=cortana-es"])
        config.set_values(["rvc.language_models.en=jarvis"])
        self.assertEqual(config.load()["providers"]["rvc"]["language_models"],
                         {"es": "cortana-es", "en": "jarvis"})

    def test_empty_value_removes_the_entry(self):
        config.set_values(["rvc.language_models.es=cortana-es"])
        config.set_values(["rvc.language_models.es="])
        self.assertEqual(config.load()["providers"]["rvc"]["language_models"], {})

    def test_unknown_key_still_errors(self):
        with self.assertRaises(TTSError):
            config.set_values(["rvc.nope.x=1"])

    def test_nesting_under_a_non_dict_setting_errors(self):
        with self.assertRaises(TTSError):
            config.set_values(["rvc.device.extra=cuda"])


if __name__ == "__main__":
    unittest.main()


class PhoneticDictionaryTest(unittest.TestCase):
    """IPA entries in the pronunciation dictionary: how a borrowed word keeps its own
    sound now that the sentence is no longer cut into language spans."""

    ENTRIES = {
        "kubectl": "kube control",
        "pull request": "/p\u02c8\u028al \u0279\u1d3ckw\u02c8\u025bst/",
        "es:croissant": "/\u02c8k\u0281was\u0251\u0303/",
        "en:jarvis": "/d\u0292\u02c8\u0251\u02d0\u0279v\u026as/",
    }

    def test_slashes_mark_ipa_a_bare_value_is_a_respelling(self):
        self.assertTrue(textutil.is_phonetic("/p\u02c8\u028al/"))
        self.assertFalse(textutil.is_phonetic("kube control"))
        self.assertFalse(textutil.is_phonetic("and/or"))     # slashes inside, not around

    def test_the_two_kinds_are_kept_apart(self):
        self.assertEqual(textutil.respelling_entries(self.ENTRIES), {"kubectl": "kube control"})
        self.assertIn("pull request", textutil.phonetic_entries(self.ENTRIES))

    def test_ipa_is_unwrapped_not_spliced_into_the_text(self):
        """A transcription is phonemes for a backend, not something to paste into a
        sentence: leaving it in the text would have the model spell out the slashes."""
        said = textutil.apply_pronunciations("run kubectl on the pull request", self.ENTRIES)
        self.assertEqual(said, "run kube control on the pull request")
        self.assertEqual(textutil.phonetic_entries(self.ENTRIES)["pull request"],
                         "p\u02c8\u028al \u0279\u1d3ckw\u02c8\u025bst")

    def test_any_language_not_just_english(self):
        """IPA is not tied to one language: a French word inside Spanish is the same
        mechanism. What limits it is the backend's phoneme vocabulary, not this table."""
        spanish = textutil.phonetic_entries(self.ENTRIES, "es")
        self.assertIn("croissant", spanish)
        self.assertNotIn("jarvis", spanish)          # scoped to en
        self.assertIn("jarvis", textutil.phonetic_entries(self.ENTRIES, "en"))

    def test_kokoro_without_a_server_cannot_take_phonemes(self):
        """The per-call CLI wrapper takes text and has nowhere to put a transcription;
        the phonemizer lives in the persistent server.

        Only the no-server half belongs here, because it needs no boundary: with a
        `server_url` set, `supports_phonetics` really connects, and asserting it here
        would pass or fail on whatever happens to be listening on the developer's
        machine. PhoneticsOnTheWireTest covers the served halves against a fake server.
        """
        plain = KokoroProvider(dict(config.DEFAULTS["providers"]["kokoro"]))
        self.assertFalse(plain.supports_phonetics)

    def test_a_backend_without_a_phonemizer_says_so(self):
        self.assertFalse(PiperProvider(dict(config.DEFAULTS["providers"]["piper"])).supports_phonetics)
        self.assertFalse(LlamaCppProvider(dict(config.DEFAULTS["providers"]["llamacpp"])).supports_phonetics)


class PhoneticsOnTheWireTest(unittest.TestCase):
    """The half that makes the feature work: the table has to reach the server, and
    only a server that says it understands it may be told it does."""

    ENTRIES = {"pull request": "/p\u02c8\u028al \u0279\u1d3ckw\u02c8\u025bst/",
               "kubectl": "kube control"}

    def build(self, server, **overrides):
        cfg = {"pronunciations": self.ENTRIES,
               "providers": {"kokoro": dict(config.DEFAULTS["providers"]["kokoro"],
                                            server_url=server.url, **overrides)}}
        return KokoroProvider(cfg["providers"]["kokoro"], cfg=cfg, lang="es")

    def test_a_current_server_is_sent_the_table(self):
        server = _FakeAudioServer("/synthesize", audio_bytes=b"audio",
                                  capabilities={"ok": True, "phonetics": True})
        self.addCleanup(server.stop)
        provider = self.build(server)
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("Ya subí el pull request", os.path.join(tmp, "out.wav"))
        sent = server.requests[0]
        self.assertEqual(sent["phonetics"],
                         {"pull request": "p\u02c8\u028al \u0279\u1d3ckw\u02c8\u025bst"})
        self.assertNotIn("kubectl", sent["phonetics"])   # a respelling is not phonemes

    def test_a_respelling_never_travels_as_phonemes(self):
        """Respellings are rewritten into the text upstream (cli.py, before any provider
        sees it), so a provider must never mistake one for a transcription."""
        server = _FakeAudioServer("/synthesize", audio_bytes=b"audio",
                                  capabilities={"ok": True, "phonetics": True})
        self.addCleanup(server.stop)
        with tempfile.TemporaryDirectory() as tmp:
            self.build(server).synthesize("corre kubectl", os.path.join(tmp, "out.wav"))
        self.assertNotIn("kubectl", server.requests[0].get("phonetics", {}))

    def test_an_older_server_is_not_told_it_understands(self):
        """It answers /health with a plain "ok" and would drop the table without a word,
        so `supports_phonetics` must be False and nothing may be sent."""
        server = _FakeAudioServer("/synthesize", audio_bytes=b"audio")   # plain "ok"
        self.addCleanup(server.stop)
        provider = self.build(server)
        self.assertFalse(provider.supports_phonetics)
        with tempfile.TemporaryDirectory() as tmp:
            provider.synthesize("Ya subí el pull request", os.path.join(tmp, "out.wav"))
        self.assertNotIn("phonetics", server.requests[0])

    def test_an_unreachable_server_does_not_claim_support(self):
        cfg = {"pronunciations": self.ENTRIES,
               "providers": {"kokoro": dict(config.DEFAULTS["providers"]["kokoro"],
                                            server_url="http://127.0.0.1:1")}}
        provider = KokoroProvider(cfg["providers"]["kokoro"], cfg=cfg)
        self.assertFalse(provider.supports_phonetics)

    def test_rvc_inherits_the_answer_from_its_base(self):
        """rvc converts a voice, it does not read text, so the dictionary is whatever
        speaks underneath."""
        server = _FakeAudioServer("/synthesize", audio_bytes=b"audio",
                                  capabilities={"ok": True, "phonetics": True})
        self.addCleanup(server.stop)
        cfg = {"providers": {
            "rvc": dict(config.DEFAULTS["providers"]["rvc"], base_provider="kokoro"),
            "kokoro": dict(config.DEFAULTS["providers"]["kokoro"], server_url=server.url),
        }}
        self.assertTrue(RvcProvider(cfg["providers"]["rvc"], cfg=cfg).supports_phonetics)

        cfg["providers"]["rvc"]["base_provider"] = "piper"
        self.assertFalse(RvcProvider(cfg["providers"]["rvc"], cfg=cfg).supports_phonetics)


class SpanishG2PTest(unittest.TestCase):
    """Grapheme to phoneme in pure Python, checked against espeak's output.

    The expected strings were produced by espeak (through kokoro-onnx's tokenizer) and
    frozen here: the point of this module is to reproduce them *without* it, so the test
    must not import it either -- a test that needs the dependency proves nothing about
    code written to avoid it.
    """

    #: word -> what espeak says. One per rule, plus the cases each rule was written for.
    WORDS = {
        # seseo: es-419 has no θ
        "zapato": "sapˈato", "cinco": "sˈinko", "cerveza": "seɾβˈesa",
        # occlusive after a pause or nasal, fricative elsewhere
        "bambú": "bambˈu", "sobre": "sˈoβɾe", "hidrógeno": "iðɾˈoxeno",
        "admitir": "ˌadmitˈiɾ", "devuelvas": "deβwˈelβas",
        # rising vs falling diphthongs
        "cuando": "kwˈando", "treinta": "tɾˈeɪnta", "cualquier": "kwalkjˈeɾ",
        "canción": "kansjˈon",
        # hiatus: after a liquid cluster, after a dieresis, two strong vowels
        "luego": "luˈeɣo", "gruesa": "ɡɾuˈesa", "cigüeña": "sˌiɣuˈeɲa",
        "paella": "paˈejja", "día": "dˈia",
        # the silent u of que/gui, which used to shift the stress
        "químico": "kˈimiko", "aquella": "akˈejja", "guillermo": "ɡijjˈeɾmo",
        # stress from spelling, and the secondary espeak adds when the primary is far in
        "antes": "ˈantes", "cantidad": "kˌantiðˈad", "azulejos": "ˌasulˈexos",
        "configuración": "kˌonfiɣˌuɾasjˈon",
        # -mente is a compound: two primaries, and the base keeps its open e
        "solamente": "sˈolamˈente", "lentamente": "lˈɛntamˈente",
        "absolutamente": "ˌaβsolˈutamˈente",
        # assorted single rules
        "chaleco": "tʃalˈeko", "llegaron": "ʝeɣˈaɾon", "ingenieros": "ˌiŋxenjˈeɾos",
        "convincentes": "kˌombinsˈɛntes", "captura": "kapːtˈuɾa",
        "psicóloga": "sikˈoloɣa", "xochimilco": "sˌotʃimˈilko", "zinc": "sˈink",
        "cuarenta": "kwaɾˈɛnta", "energía": "ˌeneɾxˈia", "valiosos": "baljˈosos",
    }

    #: Sentence-level behaviour, which word-by-word transcription cannot reach.
    SENTENCES = {
        "El zorro veloz salta sobre el perro perezoso.":
            "el sˈoro βelˈos sˈalta sˌoβɾe el pˈero pˌeɾesˈoso.",
        "La ingeniería aeroespacial exige cálculos precisos.":
            "la ˌiŋxenjeɾˈia ˌaeɾˌoespasjˈal eksˈixe kˈalkulos pɾesˈisos.",
    }

    def test_every_word(self):
        for word, expected in self.WORDS.items():
            self.assertEqual(g2p_es.phonemes_for(word), expected, word)

    def test_sentences_are_not_words_joined(self):
        """Function words lose their stress, and a b/d/g relaxes across the boundary:
        "zorro veloz" is βelˈos though "veloz" alone starts with an occlusive."""
        for sentence, expected in self.SENTENCES.items():
            self.assertEqual(g2p_es.phonemes(sentence).strip(), expected, sentence)

    def test_a_word_alone_keeps_the_stress_it_loses_in_a_sentence(self):
        self.assertEqual(g2p_es.phonemes_for("el"), "ˈel")
        self.assertEqual(g2p_es.phonemes("el perro").strip(), "el pˈero")

    def test_no_third_party_import(self):
        """The whole point: this runs where local-tts runs, which is stdlib only."""
        source = Path(g2p_es.__file__).read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", source, re.MULTILINE)
        self.assertEqual([m for m in imports if m.split(".")[0] not in
                          sys.stdlib_module_names], [])


class EnglishG2PTest(unittest.TestCase):
    """English is not phonemic, and the split between rules and lexicon is the point.

    Rules alone reach about a quarter of running text -- "through" and "thought" share
    four letters and sound nothing alike, and no rule recovers "colonel". The frozen
    lexicon carries those, and together they reach every word in the shipped corpus.
    """

    def test_rules_handle_the_regular_cases(self):
        for word, expected in {"cat": "kˈæt", "ship": "ʃˈɪp", "king": "kˈɪŋ"}.items():
            self.assertEqual(g2p_en.phonemes_for(word), expected, word)

    def test_the_lexicon_carries_what_rules_cannot(self):
        """These are the words the whole two-part design exists for."""
        for word in ("colonel", "wednesday", "through", "island"):
            said = g2p.phonemes_for(word, "en-us")
            self.assertNotEqual(said, g2p_en.phonemes_for(word), word)
            self.assertTrue(said, word)

    def test_a_sentence_is_not_its_words_joined(self):
        """"to" is tuː alone and tə in running speech."""
        self.assertEqual(g2p.phonemes_for("to", "en-us"), "tuː")
        self.assertIn("tə", g2p.phonemes("go to work", "en-us"))

    def test_an_unknown_language_says_so(self):
        """None, not a guess: a caller that gets a string sends it to the model as
        phonemes, and a wrong transcription is worse than falling back to text.

        With the library absent, which is upstream's state and the one this asserts --
        installed, espeak knows French perfectly well and the answer is a string. The
        rules are what has gaps, so the rules are what this pins.
        """
        with unittest.mock.patch.object(g2p.backend, "available", return_value=False):
            self.assertIsNone(g2p.phonemes("bonjour", "fr-fr"))
            self.assertIsNone(g2p.phonemes_for("bonjour", "fr-fr"))

    def test_both_languages_are_shipped(self):
        self.assertIn("es", g2p.supported())
        self.assertIn("en", g2p.supported())

    def test_the_lexicon_is_data_not_code(self):
        table = g2p.lexicon_for("en-us")
        self.assertGreater(len(table), 100)
        self.assertTrue(all(isinstance(v, str) and v for v in table.values()))


class PhonemizerBackendTest(unittest.TestCase):
    """The optional extra. These must pass whether or not it is installed: upstream has
    no dependencies, and a test that needs one would fail there for the wrong reason."""

    def test_the_rules_still_answer_when_the_library_is_absent(self):
        with unittest.mock.patch.object(g2p.backend, "available", return_value=False):
            self.assertEqual(g2p.phonemes_for("zapato", "es-419"), "sapˈato")

    def test_an_unknown_language_is_none_without_the_library(self):
        with unittest.mock.patch.object(g2p.backend, "available", return_value=False):
            self.assertIsNone(g2p.phonemes("bonjour", "fr-fr"))

    def test_the_library_wins_when_present(self):
        """It is what the model was trained against; the rules exist because upstream
        cannot depend on it, not because they are better."""
        with unittest.mock.patch.object(g2p.backend, "phonemes",
                                        return_value="from-the-library"):
            self.assertEqual(g2p.phonemes("zapato", "es-419"), "from-the-library")
            self.assertEqual(g2p.phonemes_for("zapato", "es-419"), "from-the-library")

    def test_the_library_covers_languages_no_rules_exist_for(self):
        with unittest.mock.patch.object(g2p.backend, "phonemes", return_value="bɔ̃ʒˈuʁ"):
            self.assertEqual(g2p.phonemes("bonjour", "fr-fr"), "bɔ̃ʒˈuʁ")

    def test_a_failing_library_falls_back_rather_than_raising(self):
        """A phonemizer that throws must not take synthesis down with it."""
        with unittest.mock.patch.object(g2p.backend, "available", return_value=True), \
             unittest.mock.patch.object(g2p.backend, "_backend",
                                        side_effect=RuntimeError("espeak exploded")):
            self.assertIsNone(g2p.backend.phonemes("hola", "es-419"))
class TerminalTitleTest(unittest.TestCase):
    """El icono va delante del nombre propio de la pestaña."""

    def setUp(self):
        audio._BASE_TITLE.clear()

    def tearDown(self):
        audio._BASE_TITLE.clear()

    def test_prefija_el_titulo_de_la_pestana(self):
        with unittest.mock.patch.object(audio, "read_terminal_title",
                                        return_value="proyecto: local-tts"):
            self.assertEqual(audio.title_for("/tmp/x.wav", 12.0),
                             "%s proyecto: local-tts" % audio.TITLE_ICON)

    def test_icono_primero(self):
        """En una fila de pestañas solo se ven los primeros caracteres."""
        with unittest.mock.patch.object(audio, "read_terminal_title", return_value="algo"):
            self.assertTrue(audio.title_for("/tmp/x.wav").startswith(audio.TITLE_ICON))

    def test_reserva_al_nombre_del_fichero(self):
        """Una terminal que no contesta no es un error: se nombra el fichero."""
        with unittest.mock.patch.object(audio, "read_terminal_title", return_value=""):
            self.assertEqual(audio.title_for("/tmp/x.wav", 12.0),
                             "%s 0:12 x.wav" % audio.TITLE_ICON)

    def test_restaurar_devuelve_el_titulo_propio(self):
        escrito = []
        with unittest.mock.patch.object(audio, "read_terminal_title", return_value="mi pestaña"), \
             unittest.mock.patch.object(audio, "write_terminal_title",
                                        side_effect=lambda t, tty="": escrito.append(t)):
            audio.restore_title()
        self.assertEqual(escrito, ["mi pestaña"])

    def test_restaurar_limpia_si_no_se_supo(self):
        escrito = []
        with unittest.mock.patch.object(audio, "read_terminal_title", return_value=""), \
             unittest.mock.patch.object(audio, "write_terminal_title",
                                        side_effect=lambda t, tty="": escrito.append(t)):
            audio.restore_title()
        self.assertEqual(escrito, [""])

    def test_se_consulta_una_sola_vez_por_tty(self):
        """La consulta cuesta un viaje de ida y vuelta y el nombre no cambia."""
        with unittest.mock.patch.object(audio, "read_terminal_title",
                                        return_value="x") as leer:
            audio.base_title("/dev/pts/9")
            audio.base_title("/dev/pts/9")
            audio.base_title("/dev/pts/9")
        self.assertEqual(leer.call_count, 1)

    def test_leer_no_revienta_sin_terminal(self):
        with unittest.mock.patch.object(audio, "terminal_path", return_value=""):
            self.assertEqual(audio.read_terminal_title(), "")
