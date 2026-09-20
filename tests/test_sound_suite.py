import contextlib
import importlib.util
import io
import unittest
from unittest.mock import patch
from localtts import calibration
from localtts.errors import TTSError

class SoundSuiteTest(unittest.TestCase):
    def test_missing_language_suite_fails_before_starting_models(self):
        cfg={'languages':{'fr':{'provider':'kokoro'}},'provider':'kokoro'}
        with patch.object(calibration.config,'load',return_value=cfg),patch.object(calibration.providers,'build') as build:
            with self.assertRaises(TTSError):calibration.command(['--suite','sounds'])
        build.assert_not_called()

    def test_custom_text_cannot_silently_override_sound_suite(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                calibration.command(['--suite','sounds','--text','test'])

    def test_asr_distance_handles_insertions_deletions_and_accents(self):
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/'tools'/'evaluate_speech.py'
        spec=importlib.util.spec_from_file_location('evaluate',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.tokens('¡Reparación rápida!'),['reparación','rápida'])
        self.assertEqual(module.edit_distance(['a','b','c'],['a','d','c','e']),2)
        self.assertEqual(module.edit_distance(['a','b'],[]),2)
