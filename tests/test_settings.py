import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from localtts import config, skills, tui, warming
from localtts.errors import TTSError


class SettingsTest(unittest.TestCase):
    def test_invalid_save_preserves_existing_config_and_external_changes_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            with patch.dict(os.environ, {'LOCALTTS_CONFIG': str(path)}):
                config.set_values(['cache_enabled=true'])
                saved = path.read_bytes()
                with self.assertRaises(TTSError): config.set_values(['cache_ttl_hours=-1'])
                self.assertEqual(path.read_bytes(), saved)
                config.set_values(['cache_policy=lru'])
                self.assertTrue(config.load()['cache_enabled'])
                self.assertEqual(config.load()['cache_policy'], 'lru')

    def test_tui_covers_settings_and_masks_secrets(self):
        values = dict(tui.rows(copy.deepcopy(config.DEFAULTS)))
        self.assertIn('cache_max_mb', values)
        self.assertIn('rvc.conversion', values)
        self.assertIn('kokoro.server_start', values)
        self.assertEqual(tui.display_value('openai.api_key', 'not-for-display'), '<hidden>')
        self.assertNotIn('\x1b', tui.safe_text('\x1b[2J'))

    def test_all_agents_install_native_skills_and_preserve_legacy_content(self):
        with tempfile.TemporaryDirectory() as directory:
            for agent in skills.AGENTS:
                legacy = skills.resolve(skills.LEGACY[agent], directory) if agent in skills.LEGACY else None
                if legacy:
                    legacy.parent.mkdir(parents=True, exist_ok=True)
                    legacy.write_text('User rules\n' + skills.BEGIN + '\nold\n' + skills.END)
                skills.install(agent, base=directory)
                self.assertTrue(skills.status(agent, base=directory)[0])
                for name in skills.SKILLS:
                    self.assertTrue(skills.target_paths(name, agent, directory).is_file())
                if legacy: self.assertEqual(legacy.read_text(), 'User rules\n')
                skills.uninstall(agent, base=directory)
                self.assertFalse(skills.status(agent, base=directory)[0])

    def test_warming_reloads_settings_without_playback(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg['languages'] = {'en': {'provider': 'kokoro'}}
        cfg['providers']['kokoro']['server_url'] = 'http://old'
        cfg['warm_interval_seconds'] = .1
        now, calls, pings = [0.], [], []
        def speak(args): calls.append(args)
        def ping(url):
            pings.append(url)
            cfg['providers']['kokoro']['server_url'] = 'http://new'
        with patch.object(config, 'load', side_effect=lambda: copy.deepcopy(cfg)), patch.object(warming, 'keepalive', side_effect=ping), patch.object(warming.time, 'monotonic', side_effect=lambda: now[0]), patch.object(warming.time, 'sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)):
            warming.command(['--lang', 'en', '--keep-alive', '1'], speak)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('--no-play' in call for call in calls))
        self.assertIn('http://new', pings)

    def test_nested_delivery_and_invalid_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'LOCALTTS_CONFIG': str(Path(directory) / 'config.json')}):
                config.set_values(['rvc.delivery.es.speed=0.95'])
                self.assertEqual(config.load()['providers']['rvc']['delivery']['es']['speed'], .95)
                for assignment in ('cache_enabled=ture', 'rvc.delivery.es=invalid', 'kokoro.speed=-1'):
                    with self.assertRaises(TTSError): config.set_values([assignment])
                Path(config.config_path()).write_text('{"providers": []}')
                with self.assertRaises(TTSError): config.load()
