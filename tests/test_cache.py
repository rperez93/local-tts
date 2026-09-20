import contextlib
import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from localtts import audio, cache, cli, config
from localtts.providers.base import Provider


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg.update(cache_dir=str(self.root / 'cache'), cache_enabled=True)
        self.now = 1000.
        self.store = cache.AudioCache(self.cfg, clock=lambda: self.now)
        self.source = self.root / 'source.wav'
        self.source.write_bytes(b'a' * 100)
        self.out = self.root / 'out.wav'

    def test_expiration_is_not_extended_by_reads_and_copy_survives_clear(self):
        self.store.put('a' * 64, self.source)
        self.now += 100
        self.assertTrue(self.store.get('a' * 64, self.out))
        self.assertEqual(self.out.read_bytes(), self.source.read_bytes())
        self.now += 36 * 3600
        self.assertFalse(self.store.get('a' * 64, self.out))
        self.assertEqual(self.store.maintain()['entries'], 0)
        self.assertEqual(self.out.read_bytes(), b'a' * 100)

    def test_lfu_budget_includes_headers_and_rejects_oversize(self):
        self.store.limit = 2 * (cache.HEADER + 100)
        for key in ('a', 'b'): self.store.put(key * 64, self.source)
        self.store.get('a' * 64, self.out)
        self.now += 1
        self.store.put('c' * 64, self.source)
        self.assertFalse(self.store.get('b' * 64, self.out))
        self.assertTrue(self.store.get('a' * 64, self.out))
        self.assertLessEqual(self.store.maintain()['bytes'], self.store.limit)
        self.source.write_bytes(b'x' * self.store.limit)
        self.assertFalse(self.store.put('d' * 64, self.source))
        self.assertEqual(self.store.maintain()['entries'], 2)

    def test_corrupt_and_interrupted_entries_are_removed(self):
        self.store.maintain()
        (self.store.directory / ('a' * 64 + '.entry')).write_bytes(b'bad')
        (self.store.directory / ('b' * 64 + '.pending')).write_bytes(b'partial')
        self.assertEqual(self.store.maintain()['entries'], 0)
        self.assertEqual(list(self.store.directory.glob('*.pending')), [])

    def test_fingerprint_tracks_format_voice_assets_and_settings(self):
        p = Provider({'speed': 1}, lang='es')
        p.name = 'kokoro'
        voice = self.root / 'voice.onnx'
        voice.write_bytes(b'model')
        def key(fmt='wav'): return cache.fingerprint(p, 'hola', str(voice), self.cfg, fmt)
        original = key()
        self.assertNotEqual(original, key('mp3'))
        voice.write_bytes(b'new model')
        self.assertNotEqual(original, key())
        original = key()
        p.settings['speed'] = .9
        self.assertNotEqual(original, key())
        original = key()
        self.cfg['player'] = 'windows'
        self.assertEqual(original, key())
        self.cfg['ending_silence_ms'] = 350
        self.assertNotEqual(original, key())

    def test_cli_hit_skips_synthesis_and_does_not_double_pad(self):
        class Fake(Provider):
            name = 'kokoro'
            default_format = 'wav'
            def synthesize(self, sentence, path, voice=None):
                calls.append(sentence)
                with wave.open(path, 'wb') as wav:
                    wav.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(b'\x01\x01' * 100)
                return path
        calls = []
        self.cfg['ending_silence_ms'] = 350
        provider = Fake({}, cfg=self.cfg)
        with patch.object(config, 'load', return_value=self.cfg), patch.object(cli.providers, 'build', return_value=provider), contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2): cli.speak(['--no-play', '-o', str(self.out), 'Hello.'])
            self.assertEqual(calls, ['Hello.'])
            self.assertEqual(audio.duration(str(self.out)), .45)
            cli.speak(['--no-play', '--no-cache', '-o', str(self.out), 'Hello.'])
            self.assertEqual(len(calls), 2)
            self.cfg['phonetics_hooks'] = ['dynamic-hook']
            cli.speak(['--no-play', '-o', str(self.out), 'Hello.'])
            self.assertEqual(len(calls), 3)

    def test_refresh_replaces_existing_entry(self):
        self.store.put('a' * 64, self.source)
        self.source.write_bytes(b'new')
        self.store.put('a' * 64, self.source, replace=True)
        self.store.get('a' * 64, self.out)
        self.assertEqual(self.out.read_bytes(), b'new')

    def test_model_directory_and_voice_sidecar_invalidate(self):
        p = Provider({'model_dir': str(self.root)}, lang='en')
        p.name = 'kokoro'
        model = self.root / 'kokoro-v1.0.onnx'
        model.write_bytes(b'old')
        before = cache.fingerprint(p, 'hello', None, self.cfg)
        model.write_bytes(b'new weights')
        self.assertNotEqual(before, cache.fingerprint(p, 'hello', None, self.cfg))
        voice = self.root / 'voice.onnx'
        voice.write_bytes(b'voice')
        sidecar = Path(str(voice) + '.json')
        sidecar.write_text('{}')
        before = cache.fingerprint(p, 'hello', str(voice), self.cfg)
        sidecar.write_text('{"speaker": 2}')
        self.assertNotEqual(before, cache.fingerprint(p, 'hello', str(voice), self.cfg))

    def test_lru_evicts_oldest_even_if_frequent(self):
        self.store.limit = 2 * (cache.HEADER + 100)
        self.store.policy = 'lru'
        self.store.put('a' * 64, self.source)
        for _ in range(3): self.store.get('a' * 64, self.out)
        self.now += 1
        self.store.put('b' * 64, self.source)
        self.now += 1
        self.store.put('c' * 64, self.source)
        self.assertFalse(self.store.get('a' * 64, self.out))
        self.assertTrue(self.store.get('b' * 64, self.out))

    def test_resident_server_mismatch_and_busy_probe_bypass_cache(self):
        import urllib.error
        options = {'server_url': 'http://localhost:8765', 'server_start': 'python /tmp/server.py --voice new'}
        state = {'cache_state': {'script': '/tmp/server.py', 'argv': ['--voice', 'old'], 'assets': {}}}
        import json
        with patch.object(cache.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(state).encode())):
            self.assertFalse(cache.server_matches(options))
        with patch.object(cache.urllib.request, 'urlopen', side_effect=urllib.error.URLError(TimeoutError())):
            self.assertFalse(cache.server_matches(options))
        with patch.object(cache.urllib.request, 'urlopen', side_effect=urllib.error.URLError(ConnectionRefusedError())):
            self.assertTrue(cache.server_matches(options))

    def test_default_kokoro_server_assets_participate_without_model_dir_setting(self):
        models = self.root / '.local/share/kokoro-models'
        models.mkdir(parents=True)
        model = models / 'kokoro-v1.0.onnx'
        model.write_bytes(b'old')
        p = Provider({'server_start': 'python server.py'}, lang='en')
        p.name = 'kokoro'
        with patch.object(cache.Path, 'home', return_value=self.root):
            before = cache.fingerprint(p, 'hello', None, self.cfg)
            model.write_bytes(b'new model')
            self.assertNotEqual(before, cache.fingerprint(p, 'hello', None, self.cfg))

    def test_explicit_wav_gets_padding_even_for_default_mp3_provider(self):
        class Fake(Provider):
            name = 'openai'
            default_format = 'mp3'
            def synthesize(self, sentence, path, voice=None):
                with wave.open(path, 'wb') as wav:
                    wav.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(b'\x01\x01' * 100)
                return path
        self.cfg['ending_silence_ms'] = 350
        provider = Fake({}, cfg=self.cfg)
        with patch.object(config, 'load', return_value=self.cfg), patch.object(cli.providers, 'build', return_value=provider), contextlib.redirect_stdout(io.StringIO()):
            cli.speak(['--no-play', '-o', str(self.out), 'Hello.'])
            self.assertEqual(audio.duration(str(self.out)), .45)
            cli.speak(['--no-play', '-o', str(self.out), 'Hello.'])
            self.assertEqual(audio.duration(str(self.out)), .45)
