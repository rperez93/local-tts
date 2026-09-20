"""Portable terminal settings editor with automatic refresh and shared CLI writes."""
import contextlib
import json
import os
import shutil
import sys
import time

from localtts import config
from localtts.errors import TTSError


def rows(cfg):
    result = [(key, cfg[key]) for key in config.TOP_LEVEL_KEYS]
    for name, settings in cfg['providers'].items():
        result.extend((name + '.' + key, value) for key, value in settings.items())
    for language, entry in cfg.get('languages', {}).items():
        result.extend(('languages.' + language + '.' + key, value) for key, value in entry.items())
    return sorted(result)


def display_value(key, value):
    if any(word in key.lower() for word in ('api_key', 'password', 'secret', 'token')):
        return '<hidden>' if value else '<unset>'
    return json.dumps(value, ensure_ascii=False)


def safe_text(value):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))


def read_key(timeout):
    if os.name == 'nt':
        import msvcrt
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ('\x00', '\xe0'):
                    return {'H': 'up', 'P': 'down'}.get(msvcrt.getwch(), '')
                return char
            time.sleep(.02)
        return ''
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        if not select.select([fd], [], [], timeout)[0]: return ''
        char = os.read(fd, 1).decode(errors='replace')
        if char == '\x1b':
            sequence = b''
            for _ in range(2):
                if select.select([fd], [], [], .03)[0]: sequence += os.read(fd, 1)
            return {b'[A': 'up', b'[B': 'down'}.get(sequence, '')
        return char
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)


def command(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='tts settings', description=__doc__)
    parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise TTSError('settings needs an interactive terminal; use `tts config --set KEY=VALUE`')
    selected, query, message = 0, '', 'Edits save immediately. External changes reload automatically.'
    last_good = config.load()
    restore_console = None
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            if kernel.SetConsoleMode(handle, mode.value | 4):
                restore_console = (kernel, handle, mode.value)
    try:
        while True:
            try:
                updated = config.load()
                config.validate(updated)
                last_good = updated
            except TTSError as exc:
                message = 'Config error (showing last view): ' + str(exc)
            entries = [(k, v) for k, v in rows(last_good) if query.lower() in k.lower()]
            selected = min(selected, max(0, len(entries) - 1))
            width, height = shutil.get_terminal_size((90, 24))
            page = max(1, height - 8)
            start = (selected // page) * page
            print('\x1b[2J\x1b[H', end='')
            print('local-tts settings | arrows/j/k move | Enter edit | / filter | a add | q quit')
            print(safe_text(config.config_path())[:width])
            print('Filter: ' + safe_text(query))
            for index, (key, value) in enumerate(entries[start:start + page], start):
                line = ('> ' if index == selected else '  ') + key + ' = ' + display_value(key, value)
                print(safe_text(line)[:max(1, width - 1)])
            print('\n' + safe_text(message)[:max(1, width - 1)])
            print('Reload: next request; active utterances retain their settings.', flush=True)
            key = read_key(float(last_good.get('tui_refresh_seconds', 1)))
            if key in ('q', '\x03'): return 0
            if key in ('j', 'down'): selected = min(selected + 1, len(entries) - 1)
            elif key in ('k', 'up'): selected = max(0, selected - 1)
            elif key == '/':
                query = input('\nFilter settings: ').strip(); selected = 0
            elif key in ('a', '\r', '\n', 'e'):
                if key != 'a' and not entries: continue
                try:
                    if key == 'a':
                        assignment = input('\nKEY=VALUE (blank cancels): ')
                    else:
                        name, current = entries[selected]
                        prompt = '\nNew %s (JSON for lists/maps; blank cancels): ' % name
                        if any(word in name.lower() for word in ('api_key', 'password', 'secret', 'token')):
                            import getpass
                            value = getpass.getpass(prompt)
                        else: value = input(prompt)
                        assignment = name + '=' + value if value else ''
                    if assignment:
                        config.set_values([assignment])
                        message = 'Saved. New requests use the updated settings.'
                except (TTSError, ValueError) as exc:
                    message = str(exc)
    except EOFError:
        return 0
    finally:
        print('\x1b[0m\n', end='', flush=True)
        if restore_console:
            kernel, handle, mode = restore_console
            kernel.SetConsoleMode(handle, mode)
