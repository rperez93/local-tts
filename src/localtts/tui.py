"""Portable settings editor with stable panes and shared CLI writes."""
import contextlib
import json
import os
import shutil
import sys
import textwrap
import time
import unicodedata

from localtts import __version__, config
from localtts.errors import TTSError


KEY_HELP = (
    ("Up/Down, j/k", "Select a setting; scroll help"),
    ("PgUp/PgDn", "Move one page"),
    ("Home/End, g/G", "Move to the first/last row"),
    ("Enter, e", "Edit the selected setting"),
    ("/", "Filter settings by name"),
    ("a", "Add a KEY=VALUE assignment"),
    ("Esc", "Cancel input, close help or clear the filter"),
    ("?, F1", "Open/close this help"),
    ("q, Ctrl-C", "Quit (q is ordinary text while editing)"),
    ("While typing", "Enter saves; Backspace deletes; Ctrl-U clears; Esc cancels"),
)

# Escape followed by ordinary typing is two keys, not an unknown ANSI sequence.
_pending_bytes = bytearray()


def rows(cfg):
    result = [(key, cfg[key]) for key in config.TOP_LEVEL_KEYS]
    for name, settings in cfg["providers"].items():
        result.extend((name + "." + key, value) for key, value in settings.items())
    for language, entry in cfg.get("languages", {}).items():
        result.extend(("languages." + language + "." + key, value) for key, value in entry.items())
    return sorted(result)


def is_secret(key):
    return any(word in key.lower() for word in ("api_key", "password", "secret", "token"))


def display_value(key, value):
    if is_secret(key):
        return "<hidden>" if value else "<unset>"
    return json.dumps(value, ensure_ascii=False)


def safe_text(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def cell_width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def clip(value, width):
    """Clip by terminal cells, keeping wide voices/paths out of the next pane."""
    result, used = [], 0
    for char in safe_text(value):
        size = cell_width(char)
        if used + size > max(0, width):
            break
        result.append(char)
        used += size
    return "".join(result)


@contextlib.contextmanager
def terminal():
    """Keep input raw for the whole session; restore the shell even after errors."""
    restore = None
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)) or not kernel.SetConsoleMode(handle, mode.value | 4):
            raise TTSError("settings needs a terminal with ANSI support; try Windows Terminal")
        restore = lambda: kernel.SetConsoleMode(handle, mode.value)
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        original = termios.tcgetattr(fd)
        tty.setcbreak(fd, termios.TCSANOW)
        restore = lambda: termios.tcsetattr(fd, termios.TCSANOW, original)
    try:
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
        yield
    finally:
        try:
            sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        finally:
            restore()


def read_key(timeout):
    if os.name == "nt":
        import msvcrt
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ("\x00", "\xe0"):
                    return {"H": "up", "P": "down", "I": "pageup", "Q": "pagedown",
                            "G": "home", "O": "end", ";": "help"}.get(msvcrt.getwch(), "")
                if 0xD800 <= ord(char) <= 0xDBFF:
                    char = (char + msvcrt.getwch()).encode(
                        "utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")
                return "escape" if char == "\x1b" else char
            time.sleep(.02)
        return ""
    import select
    fd = sys.stdin.fileno()
    if _pending_bytes:
        first = bytes([_pending_bytes.pop(0)])
    else:
        if not select.select([fd], [], [], timeout)[0]:
            return ""
        first = os.read(fd, 1)
    if not first:
        raise EOFError
    if first == b"\x1b":
        sequence = b""
        # CSI and SS3 keys have a final byte in @..~. Cap malformed input so
        # an unknown terminal sequence cannot trap the editor in a read loop.
        for _ in range(16):
            if not select.select([fd], [], [], .03)[0]:
                break
            byte = os.read(fd, 1)
            if not byte:
                break
            if not sequence and byte not in (b"[", b"O"):
                _pending_bytes.extend(byte)
                return "escape"
            sequence += byte
            if len(sequence) > 1 and 64 <= sequence[-1] <= 126:
                break
        return {b"": "escape", b"[A": "up", b"[B": "down", b"[5~": "pageup",
                b"[6~": "pagedown", b"[H": "home", b"[F": "end", b"OH": "home",
                b"OF": "end", b"[1~": "home", b"[4~": "end", b"OP": "help",
                b"[11~": "help"}.get(sequence, "")
    # Read a whole UTF-8 character without mixing buffered stdin with os.read.
    count = 1 if first[0] < 0xC0 else 2 if first[0] < 0xE0 else 3 if first[0] < 0xF0 else 4
    data = first
    for _ in range(count - 1):
        if not select.select([fd], [], [], .03)[0]:
            break
        data += os.read(fd, 1)
    return data.decode("utf-8", errors="replace")


class Editor:
    def __init__(self):
        self.cfg = config.load()
        self.selected = 0
        self.query = ""
        self.message = "Edits save immediately. External changes reload automatically."
        self.help = False
        self.help_offset = 0
        self.help_width = 78
        self.mode = None
        self.buffer = ""
        self.edit_key = ""

    def entries(self):
        return [(k, v) for k, v in rows(self.cfg) if self.query.lower() in k.lower()]

    def reload(self):
        entries = self.entries()
        name = entries[self.selected][0] if entries else None
        try:
            updated = config.load()
            config.validate(updated)
            self.cfg = updated
            if self.message.startswith("Config error:"):
                self.message = "Configuration reloaded."
        except TTSError as exc:
            self.message = "Config error: %s (showing last valid view)" % exc
        names = [k for k, _ in self.entries()]
        self.selected = names.index(name) if name in names else min(self.selected, max(0, len(names) - 1))

    def help_lines(self):
        from localtts.cli import COMMAND_HELP
        lines = (["Keyboard"] + ["  %-17s %s" % item for item in KEY_HELP] +
                ["", "CLI commands (run in your shell)"] +
                ["  tts %-12s %s" % item for item in COMMAND_HELP.items()] +
                ["", "tts help COMMAND lists every option for that command.",
                 "Values: text for strings; true/false; numbers; JSON lists/maps.",
                 'Use "" to empty a string; blank input cancels an edit.',
                 "Map entries: a, then pronunciations.word=respelling (empty removes).",
                 "Languages: a, then languages.es=kokoro (empty removes).",
                 "Nested values: a, then rvc.delivery.es.speed=0.95.",
                 "Environment overrides still take precedence over saved values.",
                 "New requests reload settings; server startup options need a restart."])
        return [part for line in lines for part in
                (textwrap.wrap(line, self.help_width, subsequent_indent="    ") or [""])]

    @staticmethod
    def page_size(height):
        return max(1, height - 9)

    def frame(self, width, height):
        """Return plain rows plus styles; one renderer owns clipping and painting."""
        if width < 40 or height < 12:
            return [("Window too small (40x12 minimum). q: quit", "dim")]
        self.help_width = width - 2
        entries = self.entries()
        page = self.page_size(height)
        body = [(" local-tts settings  |  v%s" % __version__, "title"),
                (" " + str(config.config_path()), "dim"),
                (" Help" if self.help else " Settings  |  filter: %s  |  %s/%s" %
                 (self.query or "all", self.selected + 1 if entries else 0, len(entries)), "accent"),
                ("─" * width, "dim")]
        if self.help:
            lines = self.help_lines()
            self.help_offset = min(self.help_offset, max(0, len(lines) - page))
            visible = [(line, "") for line in lines[self.help_offset:self.help_offset + page]]
        else:
            start = (self.selected // page) * page
            key_width = min(42, max(15, width // 2))
            visible = []
            for index, (key, value) in enumerate(entries[start:start + page], start):
                label = clip(key, key_width)
                label += " " * (key_width - sum(cell_width(c) for c in label))
                visible.append((" %s %s  %s" % (">" if index == self.selected else " ", label,
                                                display_value(key, value)),
                                "selected" if index == self.selected else ""))
            if not visible:
                visible = [(" No matching settings. Esc clears the filter; a adds a value.", "dim")]
        body += visible + [("", "")] * (page - len(visible))
        body.append(("─" * width, "dim"))
        if self.mode:
            label = {"filter": "Filter", "add": "KEY=VALUE", "edit": self.edit_key}[self.mode]
            # Assignments can contain secrets too, so mask add input as soon as
            # its key is recognizable and never echo secret edit values.
            secret = is_secret(self.edit_key) if self.mode == "edit" else (
                self.mode == "add" and is_secret(self.buffer.split("=", 1)[0]))
            shown = "*" * len(self.buffer) if secret else self.buffer
            prompt = clip(" %s: " % label, max(10, width // 2))
            room = max(1, width - len(prompt) - 3)
            while sum(cell_width(c) for c in shown) > room:
                shown = shown[1:]
            body.extend([(prompt + shown + "_", "accent"),
                         (" Enter: save  Esc: cancel  Backspace: delete  Ctrl-U: clear", "dim")])
        elif self.help:
            body.extend([(" Help rows %s–%s of %s" % (self.help_offset + 1,
                         min(len(self.help_lines()), self.help_offset + page), len(self.help_lines())), "accent"),
                         (" Up/Down PgUp/PgDn Home/End: scroll  ?: close help", "dim")])
        else:
            key, value = entries[self.selected] if entries else ("No selection", "")
            body.extend([(" " + key, "accent"), (" " + display_value(key, value), "")])
        body.append((" " + self.message, "dim"))
        legend = " Enter/e: edit  /: filter  a: add  ?: help  q: quit"
        if width < len(legend) + 1:
            legend = " Enter: edit  ?: help  q: quit"
        body.append((legend, "title"))
        return body

    def handle(self, key, height):
        if key == "\x03":
            return False
        if self.mode:
            if key == "escape":
                self.mode, self.buffer = None, ""
            elif key in ("\r", "\n"):
                try:
                    if self.mode == "filter":
                        self.query, self.selected = self.buffer.strip(), 0
                    elif self.buffer:
                        assignment = self.buffer if self.mode == "add" else self.edit_key + "=" + self.buffer
                        # Configuration strings are literal; provide an explicit
                        # way to clear one without changing blank-to-cancel.
                        if assignment.endswith('=""'):
                            assignment = assignment[:-2]
                        config.set_values([assignment])
                        self.message = "Saved. New requests use updated settings."
                    self.mode, self.buffer = None, ""
                    self.reload()
                except (TTSError, ValueError) as exc:
                    # Validator errors may quote the submitted value. Keep secret
                    # values out of the status pane as well as the input pane.
                    name = self.buffer.split("=", 1)[0] if self.mode == "add" else self.edit_key
                    self.message = "Invalid secret value; check its format." if is_secret(name) else str(exc)
            elif key in ("\x7f", "\b"):
                self.buffer = self.buffer[:-1]
            elif key == "\x15":
                self.buffer = ""
            elif len(key) == 1 and key.isprintable():
                self.buffer += key
            return True
        if key == "q":
            return False
        if key in ("?", "help"):
            self.help = not self.help
            return True
        if key == "escape":
            if self.help:
                self.help = False
            else:
                self.query, self.selected = "", 0
            return True
        count = len(self.help_lines()) if self.help else len(self.entries())
        current = self.help_offset if self.help else self.selected
        step = self.page_size(height)
        maximum = max(0, count - (step if self.help else 1))
        if key in ("j", "down"): current += 1
        elif key in ("k", "up"): current -= 1
        elif key == "pageup": current -= step
        elif key == "pagedown": current += step
        elif key in ("g", "home"): current = 0
        elif key in ("G", "end"): current = maximum
        if self.help:
            self.help_offset = max(0, min(current, maximum))
            return True
        self.selected = max(0, min(current, maximum))
        if key == "/":
            self.mode, self.buffer = "filter", self.query
        elif key == "a":
            self.mode, self.buffer = "add", ""
        elif key in ("e", "\r", "\n") and count:
            self.edit_key = self.entries()[self.selected][0]
            self.mode, self.buffer = "edit", ""
        return True


STYLES = {"title": "\x1b[1;30;46m", "accent": "\x1b[36m", "dim": "\x1b[2m",
          "selected": "\x1b[7m", "": ""}


def paint(frame, previous, width, height):
    # Absolute positioning and changed-row updates avoid scrolling, flicker and
    # erased shell history. Leave the last column unused to avoid autowrap.
    output, current = [], []
    for index in range(height):
        value, style = frame[index] if index < len(frame) else ("", "")
        value = clip(value, width - 1)
        if style in ("title", "selected"):
            value += " " * max(0, width - 1 - sum(cell_width(c) for c in value))
        line = STYLES[style] + value + "\x1b[0m\x1b[K"
        current.append(line)
        if previous is None or index >= len(previous) or previous[index] != line:
            output.append("\x1b[%s;1H%s" % (index + 1, line))
    if output:
        sys.stdout.write("".join(output))
        sys.stdout.flush()
    return current


def command(argv):
    import argparse
    parser = argparse.ArgumentParser(
        prog="tts settings", description=__doc__,
        epilog="Keyboard:\n" + "\n".join("  %-17s %s" % item for item in KEY_HELP) +
               "\n\nPress ? for all commands and value syntax. Use tts help COMMAND for CLI options.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise TTSError("settings needs an interactive terminal; use `tts config --set KEY=VALUE`")
    editor = Editor()
    _pending_bytes.clear()
    previous, size = None, None
    try:
        with terminal():
            while True:
                editor.reload()
                width, height = shutil.get_terminal_size((90, 24))
                if size != (width, height):
                    previous, size = None, (width, height)
                previous = paint(editor.frame(width, height), previous, width, height)
                if not editor.handle(read_key(float(editor.cfg.get("tui_refresh_seconds", 1))), height):
                    return 0
    except EOFError:
        return 0
