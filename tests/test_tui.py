"""Exercise the interactive state and a real terminal without touching user config."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from localtts import cli, config, tui


class HelpTest(unittest.TestCase):
    def test_every_dispatched_command_is_visible_and_has_safe_help(self):
        for name in cli.SUBCOMMANDS:
            with self.subTest(name=name), contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as result:
                    cli.main(['help', name])
                self.assertEqual(result.exception.code, 0)
                self.assertIn('usage:', out.getvalue())
        text = cli._speak_parser().format_help()
        for name in cli.SUBCOMMANDS:
            self.assertIn('tts ' + name, text)

    def test_settings_help_works_without_a_terminal(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as result:
                tui.command(['--help'])
        self.assertEqual(result.exception.code, 0)
        for key, description in tui.KEY_HELP:
            self.assertIn(description, out.getvalue())

    def test_windows_surrogates_are_combined_before_editing(self):
        from unittest.mock import Mock
        console = Mock()
        console.kbhit.return_value = True
        console.getwch.side_effect = ['\ud83d', '\udd0a']
        with patch.dict(sys.modules, {'msvcrt': console}), patch.object(tui.os, 'name', 'nt'):
            self.assertEqual(tui.read_key(.1), '🔊')


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.json'
        env = patch.dict(os.environ, {'LOCALTTS_CONFIG': str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        self.editor = tui.Editor()

    def type(self, text):
        for char in text:
            self.editor.handle(char, 24)

    def filter(self, text):
        self.editor.handle('/', 24)
        self.editor.handle('\x15', 24)
        self.type(text)
        self.editor.handle('\n', 24)

    def test_edit_validate_cancel_and_external_reload(self):
        self.filter('cache_max_mb')
        self.editor.handle('e', 24)
        self.type('-1\n')
        self.assertEqual(self.editor.mode, 'edit')
        self.assertFalse(self.path.exists())
        self.editor.handle('\x15', 24)
        self.type('128\n')
        self.assertEqual(config.load()['cache_max_mb'], 128)
        self.editor.handle('e', 24)
        self.type('256')
        self.editor.handle('escape', 24)
        self.assertEqual(config.load()['cache_max_mb'], 128)
        config.set_values(['cache_max_mb=64'])
        self.editor.reload()
        self.assertEqual(self.editor.entries(), [('cache_max_mb', 64)])
        self.path.write_text('{')
        self.editor.reload()
        self.assertEqual(self.editor.entries(), [('cache_max_mb', 64)])
        self.assertIn('Config error:', self.editor.message)

    def test_empty_strings_languages_and_unicode(self):
        self.editor.handle('a', 24)
        self.type('languages.es=kokoro\n')
        self.assertEqual(config.load()['languages']['es']['provider'], 'kokoro')
        self.editor.handle('a', 24)
        self.type('player=音声\n')
        self.filter('player')
        self.editor.handle('e', 24)
        self.type('""\n')
        self.assertEqual(config.load()['player'], '')
        self.editor.handle('a', 24)
        self.type('languages.es=\n')
        self.assertNotIn('es', config.load()['languages'])

    def test_secret_input_and_error_are_never_painted(self):
        self.filter('openai.api_key')
        self.editor.handle('e', 24)
        self.type('secret-value')
        with patch.object(config, 'set_values', side_effect=ValueError('secret-value')):
            self.editor.handle('\n', 24)
        self.assertNotIn('secret-value', repr(self.editor.frame(80, 24)))
        self.editor.handle('escape', 24)
        self.editor.handle('a', 24)
        self.type('openai.api_key=secret-value')
        self.assertNotIn('secret-value', repr(self.editor.frame(80, 24)))

    def test_help_scroll_navigation_and_resize_fit_the_screen(self):
        self.editor.handle('end', 24)
        self.assertEqual(self.editor.selected, len(self.editor.entries()) - 1)
        self.editor.handle('home', 24)
        self.assertEqual(self.editor.selected, 0)
        self.editor.handle('pagedown', 24)
        self.assertEqual(self.editor.selected, tui.Editor.page_size(24))
        self.editor.handle('?', 24)
        for width, height in ((80, 24), (40, 12), (120, 40), (12, 3)):
            frame = self.editor.frame(width, height)
            self.assertLessEqual(len(frame), height)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                painted = tui.paint(frame, None, width, height)
            for line in painted:
                import re
                plain = re.sub(r'\x1b\[[0-9;]*[mK]', '', line)
                self.assertLessEqual(sum(tui.cell_width(c) for c in plain), width - 1)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                tui.paint(frame, painted, width, height)
            self.assertEqual(output.getvalue(), '')
        self.editor.frame(80, 24)
        self.editor.handle('end', 24)
        self.assertGreater(self.editor.help_offset, 0)
        self.editor.handle('escape', 24)
        self.assertFalse(self.editor.help)

    def test_filter_empty_results_can_be_cleared(self):
        self.filter('no-setting-has-this-name')
        self.editor.handle('down', 24)
        self.editor.handle('\n', 24)
        self.assertIsNone(self.editor.mode)
        self.editor.handle('escape', 24)
        self.assertTrue(self.editor.entries())
        self.assertEqual(self.editor.selected, 0)

    def test_wide_characters_and_control_codes_do_not_overflow(self):
        self.assertEqual(tui.clip('音声abc', 5), '音声a')
        self.assertEqual(tui.clip('e\u0301xy', 2), 'e\u0301x')
        self.assertNotIn('\x1b', tui.clip('\x1b[2J', 20))


@unittest.skipIf(os.name == 'nt', 'POSIX pseudo-terminal')
class TerminalTest(unittest.TestCase):
    def test_escape_preserves_the_next_key_including_utf8(self):
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(read_fd, 'r') as stream, patch.object(sys, 'stdin', stream):
                for char in ['q', '音', '\x1b']:
                    os.write(write_fd, ('\x1b' + char).encode())
                    self.assertEqual(tui.read_key(.1), 'escape')
                    self.assertEqual(tui.read_key(.1), 'escape' if char == '\x1b' else char)
        finally:
            os.close(write_fd)

    def test_real_terminal_keys_saving_and_shell_restoration(self):
        import fcntl
        import pty
        import select
        import struct
        import termios
        import time
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        before = termios.tcgetattr(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'config.json')
            env = dict(os.environ, LOCALTTS_CONFIG=path,
                       PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'), TERM='xterm-256color')
            process = subprocess.Popen([sys.executable, '-m', 'localtts', 'settings'],
                                       stdin=slave, stdout=slave, stderr=slave, env=env)
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            output = bytearray()
            def until(needle):
                deadline = time.monotonic() + 5
                while needle not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output.extend(os.read(master, 65536))
                self.assertIn(needle, output)
            until(b'local-tts settings')
            os.write(master, b'/cache_enabled\rea')
            # Enter above starts edit; e and a are literal text in that mode.
            os.write(master, b'\x15true\r')
            until(b'Saved.')
            self.assertTrue(json.loads(Path(path).read_text())['cache_enabled'])
            os.write(master, b'?\x1b[6~')
            until(b'CLI commands')
            os.write(master, b'q')
            process.wait(timeout=5)
            until(b'\x1b[?1049l')
            self.assertEqual(process.returncode, 0)
            self.assertEqual(termios.tcgetattr(slave), before)

    def test_console_restores_on_exception(self):
        import pty
        import termios
        master, slave = pty.openpty()
        try:
            with os.fdopen(os.dup(slave), 'r') as stream:
                before = termios.tcgetattr(slave)
                with patch.object(sys, 'stdin', stream), contextlib.redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(RuntimeError):
                        with tui.terminal():
                            raise RuntimeError('failed')
                self.assertEqual(termios.tcgetattr(slave), before)
                self.assertIn('\x1b[?1049l', out.getvalue())
        finally:
            os.close(master)
            os.close(slave)
