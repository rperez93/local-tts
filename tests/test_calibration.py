import array
import contextlib
import io
import json
import os
import tempfile
import unittest
import wave
from unittest.mock import patch

from localtts import calibration
from localtts.providers.base import Provider
from localtts.errors import TTSError


def write_audio(path, samples, channels=1):
    with wave.open(path, 'wb') as wav:
        wav.setparams((channels, 2, 1000, 0, 'NONE', 'not compressed'))
        wav.writeframes(array.array('h', samples).tobytes())


class CalibrationTest(unittest.TestCase):
    def test_stereo_silence_and_clipping_are_measured_in_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'sample.wav')
            write_audio(path, [0, 0] * 10 + [32767, -32768] * 20 + [0, 0] * 5, 2)
            result = calibration.measure_wav(path)
        self.assertAlmostEqual(result['duration_s'], .035)
        self.assertAlmostEqual(result['leading_silence_s'], .01)
        self.assertAlmostEqual(result['trailing_silence_s'], .005)
        self.assertAlmostEqual(result['clipped_fraction'], 20 / 35)

    def test_silent_audio_has_no_infinite_db_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'sample.wav')
            write_audio(path, [0] * 20)
            result = calibration.measure_wav(path)
        self.assertTrue(result['silent'])
        self.assertIsNone(result['peak_dbfs'])
        self.assertIsNone(result['rms_dbfs'])
        json.dumps(result, allow_nan=False)

    def test_language_variants_resolve_same_voice_and_model(self):
        for lang in ('EN-us', 'en_US', 'en-US'):
            provider = Provider({}, lang=lang)
            self.assertEqual(provider.for_language({'en': 'base', 'en-US': 'regional'}), 'regional')
            self.assertEqual(provider.for_language({'EN_us': 'regional', 'en': 'base'}), 'regional')

    def test_cli_preserves_samples_and_reports_each_run(self):
        class Fake(Provider):
            def synthesize(self, text, out_path, voice=None):
                write_audio(out_path, [1000] * 50)
                self.emit_part(out_path)
        cfg = {'provider': 'kokoro', 'languages': {'en-US': {'provider': 'kokoro'}},
               'pronunciations': {}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(calibration.config, 'load', return_value=cfg), patch.object(calibration.providers, 'build', side_effect=lambda *a, **kw: Fake({})), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(calibration.command(['--output', tmp]), 0)
            directory = os.path.join(tmp, os.listdir(tmp)[0])
            with open(os.path.join(directory, 'report.json')) as handle:
                rows = json.load(handle)['samples']
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(os.path.isfile(row['path']) for row in rows))
            self.assertTrue(all(row['first_fragment_s'] <= row['render_s'] for row in rows))
            self.assertEqual(rows[0]['language'], 'en-US')

    def test_render_failure_restores_sink(self):
        provider = Provider({})
        sink = lambda path: None
        provider.on_part = sink
        with patch.object(provider, 'synthesize', side_effect=TTSError('failed')):
            with self.assertRaises(TTSError):
                calibration.render_sample(provider, 'text', 'unused.wav')
        self.assertIs(provider.on_part, sink)
