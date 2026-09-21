"""Capture real settings TUI screens through an isolated tmux session.

Documentation-only dependencies: tmux, Pillow and DejaVu Sans Mono (or --font).
No user configuration is loaded. PNGs are rasterized from tmux's captured cells
and SGR styles, not a separately maintained mockup of the editor.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont


def rasterize(capture, path, font_path, columns=100, rows=30):
    font = ImageFont.truetype(font_path, 18)
    cell, line, margin = round(font.getlength('M')), 26, 18
    background, foreground = '#14181d', '#dde3ea'
    palette = ['#14181d', '#d65b5b', '#86ba90', '#dec47e', '#4888db',
               '#b18ccd', '#72c7cf', '#dde3ea']
    image = Image.new('RGB', (columns * cell + margin * 2, rows * line + margin * 2), background)
    draw = ImageDraw.Draw(image)
    fg, bg, dim, reverse = foreground, background, False, False
    for y, text in enumerate(capture.splitlines()[:rows]):
        x = 0
        for part in re.split(r'(\x1b\[[0-9;]*m)', text):
            if part.startswith('\x1b['):
                for code in [int(n) if n else 0 for n in part[2:-1].split(';')]:
                    if code == 0: fg, bg, dim, reverse = foreground, background, False, False
                    elif code == 2: dim = True
                    elif code == 22: dim = False
                    elif code == 7: reverse = True
                    elif code == 27: reverse = False
                    elif code == 39: fg = foreground
                    elif code == 49: bg = background
                    elif 30 <= code <= 37: fg = palette[code - 30]
                    elif 40 <= code <= 47: bg = palette[code - 40]
                continue
            ink, paper = (bg, fg) if reverse else (fg, bg)
            if dim and not reverse: ink = '#8a96a3'
            for char in part:
                left, top = margin + x * cell, margin + y * line
                draw.rectangle((left, top, left + cell - 1, top + line - 1), fill=paper)
                draw.text((left, top + 2), char, font=font, fill=ink)
                x += 1
    image.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', default='/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    socket = 'tts-docs-%s' % os.getpid()
    def tmux(*argv):
        return subprocess.check_output(['tmux', '-L', socket, *argv], text=True)
    def wait_for(text):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            capture = tmux('capture-pane', '-p', '-e', '-t', 'docs')
            if text in capture:
                return capture
            time.sleep(.05)
        raise RuntimeError('Editor did not display %r' % text)
    with tempfile.TemporaryDirectory(prefix='tts-screens-') as directory:
        # A relative path keeps machine-specific usernames and random paths out
        # of documentation while remaining the path shown by the real editor.
        command = shlex.join(['env', 'LOCALTTS_CONFIG=demo-settings.json',
                              'PYTHONPATH=' + str(root / 'src'), sys.executable,
                              '-m', 'localtts', 'settings'])
        try:
            tmux('new-session', '-d', '-s', 'docs', '-c', directory, '-x', '100', '-y', '30', command)
            wait_for('local-tts settings')
            tmux('send-keys', '-t', 'docs', '/', 'c', 'a', 'c', 'h', 'e', 'Enter')
            capture = wait_for('filter: cache')
            rasterize(capture, root / 'assets/settings.png', args.font)
            tmux('send-keys', '-t', 'docs', '?')
            capture = wait_for('CLI commands')
            rasterize(capture, root / 'assets/settings-help.png', args.font)
        finally:
            subprocess.run(['tmux', '-L', socket, 'kill-server'], capture_output=True)


if __name__ == '__main__':
    main()
