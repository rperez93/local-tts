"""Audio playback using whatever command-line player the system already has."""

import errno
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave

from localtts import lock as filelock
from localtts.errors import TTSError

# (executable, argv builder) in preference order.
PLAYERS = [
    ("ffplay", lambda p: ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", p]),
    ("paplay", lambda p: ["paplay", p]),
    ("aplay", lambda p: ["aplay", "-q", p]),
    ("afplay", lambda p: ["afplay", p]),          # macOS
    ("play", lambda p: ["play", "-q", p]),        # sox
    ("mpv", lambda p: ["mpv", "--no-video", "--really-quiet", p]),
    ("cvlc", lambda p: ["cvlc", "--play-and-exit", "--intf", "dummy", p]),
]


def _is_wsl():
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def _powershell_exe():
    """PowerShell, whether we are on Windows itself or reaching out of WSL."""
    for name in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _powershell_command(path):
    """Play through Windows' own sound player (native Windows, or WSL reaching out)."""
    exe = _powershell_exe()
    if not exe:
        return None
    win_path = os.path.abspath(path)
    if _is_wsl() and shutil.which("wslpath"):
        try:
            win_path = subprocess.check_output(
                ["wslpath", "-w", os.path.abspath(path)], text=True).strip()
        except subprocess.CalledProcessError:
            return None
    script = "(New-Object Media.SoundPlayer '%s').PlaySync()" % win_path.replace("'", "''")
    return [exe, "-NoProfile", "-NonInteractive", "-Command", script]


#: Names that explicitly select Windows' own sound player, from native Windows or from
#: WSL. Needed as real names because it is not on PATH as a player: `player=powershell.exe`
#: used to resolve to `powershell.exe <file.wav>`, which runs the wav as a script.
WINDOWS_PLAYER_NAMES = ("windows", "powershell", "powershell.exe", "pwsh", "pwsh.exe")


def _player_tuning():
    """Per-machine `player_args` / `player_env`.

    Read from the config here rather than threaded through every caller because the
    detached runner is a separate process that never sees the CLI's loaded config, and
    it is the one that actually spawns the player. Never fatal: a broken config must not
    take playback down, it just means no tuning.
    """
    try:
        from localtts import config
        cfg = config.load()
    except Exception:                    # noqa: BLE001 - tuning is strictly optional
        return {}, {}
    args = cfg.get("player_args")
    env = cfg.get("player_env")
    return (args if isinstance(args, dict) else {}), (env if isinstance(env, dict) else {})


def _with_extra_args(name, command):
    """Insert this player's configured extra arguments just before the file, which every
    builder in PLAYERS puts last."""
    extras, _ = _player_tuning()
    extra = extras.get(name)
    if not extra:
        return command
    if isinstance(extra, str):
        extra = extra.split()
    return command[:-1] + [str(item) for item in extra] + command[-1:]


def player_environment():
    """os.environ plus `player_env`, or None when nothing is configured -- so the child
    simply inherits, exactly as it did before this existed."""
    _, extra = _player_tuning()
    if not extra:
        return None
    merged = dict(os.environ)
    merged.update({str(key): str(value) for key, value in extra.items()})
    return merged


def find_player(path, preferred=""):
    if preferred:
        if preferred.lower() in WINDOWS_PLAYER_NAMES:
            command = _powershell_command(path)
            if not command:
                raise TTSError("configured player %r needs PowerShell, which was not "
                               "found" % preferred)
            return command
        exe = shutil.which(preferred)
        if not exe:
            raise TTSError("configured player %r was not found on PATH" % preferred)
        for name, build in PLAYERS:
            if name == os.path.basename(preferred):
                return _with_extra_args(name, build(path))
        return [exe, path]

    # Windows and WSL both default to Windows' own player: it is always there, and it is
    # the native way out of either. On WSL this also closes a trap -- installing ffmpeg
    # (which local-tts recommends, for tone shaping) would otherwise silently promote
    # ffplay over a player that was already working, and WSL's Linux audio bridge is
    # frequently the worse of the two. `player=ffplay` still picks it deliberately.
    if sys.platform == "win32" or _is_wsl():
        return _powershell_command(path) or _first_player(path)
    return _first_player(path)


def _first_player(path):
    for name, build in PLAYERS:
        if shutil.which(name):
            return _with_extra_args(name, build(path))
    return None


def playback_lock_path():
    """Where the machine-wide "only one audio at a time" lock lives -- derived from
    STATE_FILE's own directory (like state_path()) so tests that redirect STATE_FILE
    into a temp dir for sandboxing redirect this too, instead of every test process
    fighting over the one real lock file."""
    return os.path.join(os.path.dirname(STATE_FILE), "local-tts-playback.lock")


def _safe_duration(path):
    """Length in seconds, or 0.0 for anything we cannot parse (never fatal)."""
    try:
        return duration(path)
    except (wave.Error, EOFError, OSError):
        return 0.0


def play(path, preferred="", verbose=False, title=True):
    """Play a file. Returns True if something played, False if no player exists.

    Blocks (holding a machine-wide lock) until any other local-tts playback --
    any session, any provider -- currently in progress finishes, so audio never
    overlaps.
    """
    cmd = find_player(path, preferred)
    if not cmd:
        return False
    if verbose:
        print("+ %s" % " ".join(cmd), file=sys.stderr)
    stream = None if verbose else subprocess.DEVNULL
    with open(playback_lock_path(), "a+") as handle:
        filelock.acquire(handle)
        painted = False
        try:
            if title:
                # This process owns the terminal for the whole blocking play, so
                # unlike play_detached() it can write straight to stderr.
                write_terminal_title(title_for(path, _safe_duration(path)))
                painted = True
            try:
                subprocess.run(cmd, check=True, stdout=stream, stderr=stream,
                               env=player_environment())
            except subprocess.CalledProcessError as exc:
                raise TTSError("playback failed (%s exited with %d)" % (cmd[0], exc.returncode))
            except KeyboardInterrupt:
                return True
        finally:
            if painted:
                restore_title()
            filelock.release(handle)
    return True


def available_players():
    """Players that could be used, best first -- the same order find_player() picks in."""
    found = [name for name, _ in PLAYERS if shutil.which(name)]
    if sys.platform == "win32" or _is_wsl():
        exe = _powershell_exe()
        if exe:
            found.insert(0, os.path.basename(exe))
    return found


def concat_wavs(paths, out_path, gap_seconds=0.35):
    """Join same-format wav files into one, with a short silence between them."""
    if not paths:
        raise TTSError("nothing to join: every chunk failed")
    if len(paths) == 1 and paths[0] == out_path:
        return out_path

    with wave.open(paths[0], "rb") as first:
        params = first.getparams()
    silence = b"\x00" * int(params.framerate * gap_seconds) * params.sampwidth * params.nchannels

    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        for index, path in enumerate(paths):
            with wave.open(path, "rb") as part:
                if part.getparams()[:3] != params[:3]:
                    raise TTSError("cannot join %s: format differs from the first chunk" % path)
                out.writeframes(part.readframes(part.getnframes()))
            if index != len(paths) - 1:
                out.writeframes(silence)
    return out_path


def duration(path):
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


# --- detached playback -------------------------------------------------
#
# Playing in the background means the caller (usually an agent) is not blocked for the
# length of the audio. The player's pid is recorded in a small state file so a later
# `stop` / `pause` / `resume` can find it.

STATE_FILE = os.path.join(tempfile.gettempdir(), "local-tts-playback.json")


def _safe_session(session):
    if not session:
        return None
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(session).strip())[:40]
    digest = hashlib.sha1(str(session).encode("utf-8", "surrogateescape")).hexdigest()[:8]
    return "%s-%s" % (safe, digest) if safe else digest


def state_path(session=None):
    """The state file for one playback stream. With no session, this is STATE_FILE --
    the original single global slot, so a caller that never passes a session sees exactly
    the pre-multi-session behavior. Each session gets its own file so two concurrent
    sessions (two terminals, two agent instances) neither stop nor see each other's audio.
    Derived from STATE_FILE's own directory (not tempfile.gettempdir() directly) so tests
    that redirect STATE_FILE into a temp dir redirect session-scoped files too."""
    tag = _safe_session(session)
    if not tag:
        return STATE_FILE
    base, ext = os.path.splitext(STATE_FILE)
    return "%s-%s%s" % (base, tag, ext)


def _write_state(pid, path, duration_seconds=0.0, paused=False, elapsed=0.0, segment_start=None,
                 session=None):
    """`elapsed` is time already spent playing before the current running segment (0 if
    never paused); `segment_start` is when the current running segment began, or None
    while paused. Together they let playback_status() compute progress without polling
    the player process for position, which most players do not expose anyway."""
    try:
        with open(state_path(session), "w", encoding="utf-8") as handle:
            json.dump({
                "pid": pid,
                "path": os.path.abspath(path),
                "duration": duration_seconds,
                "paused": paused,
                "elapsed": elapsed,
                "segment_start": segment_start,
            }, handle)
    except OSError:
        pass          # a lost state file only costs us the stop/pause convenience


def read_state(session=None):
    try:
        with open(state_path(session), "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("pid") else None


def clear_state(session=None):
    try:
        os.unlink(state_path(session))
    except OSError:
        pass


def is_running(pid):
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                             capture_output=True, text=True)
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


# -- terminal title -------------------------------------------------------
#
# A speaker icon in the tab/window title is the one progress indicator that
# survives the agent's own chat output scrolling away. Playback is detached, so
# the process that *starts* it exits long before the audio ends -- it cannot
# clear the title afterwards. The runner (_playback_runner.py) outlives the
# audio and does both, but start_new_session() leaves it with no controlling
# terminal, so it cannot find the tty by itself. We therefore resolve the tty
# path here, while we still have one, and hand it over.

TITLE_ICON = "\U0001f50a"          # speaker
_OSC_TITLE = "\033]0;%s\007"      # sets icon name and window title together


def terminal_path():
    """Path of the terminal attached to this process, or "" if there isn't one.

    Checked across all three standard fds because any one of them may be
    redirected (a pipe into `boost`, `-o` capture, a hook harness) while the
    others are still the real terminal.
    """
    for fd in (2, 1, 0):
        try:
            if os.isatty(fd):
                return os.ttyname(fd)
        except (OSError, AttributeError):     # AttributeError: no ttyname on Windows
            continue
    return ""


def write_terminal_title(text, tty=""):
    """Set the terminal title, or clear it when `text` is empty. Never raises:
    a title is cosmetic and must not take playback down with it."""
    try:
        payload = (_OSC_TITLE % text).encode("utf-8", "replace")
        if tty:
            # O_NOCTTY: opening a tty from a session with no controlling terminal
            # would otherwise claim it as ours, which would put the runner in the
            # user's foreground process group.
            fd = os.open(tty, os.O_WRONLY | getattr(os, "O_NOCTTY", 0))
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        elif sys.stderr and sys.stderr.isatty():
            sys.stderr.buffer.write(payload)
            sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass


#: Query the terminal for its current window title (xterm OSC 21). The reply
#: comes back as ESC ] l <title> ESC \\ on the same tty.
_OSC_QUERY_TITLE = "\033[21t"

#: Cached base title: the tab's own text, read once per tty. None means "not
#: asked yet", "" means "asked and the terminal did not answer".
_BASE_TITLE = {}


def read_terminal_title(tty="", timeout=0.15):
    """The tab's current title, or "" if the terminal will not say.

    Most terminals answer xterm's OSC 21, but plenty refuse on purpose -- being
    able to read a title back is a small information leak, so it is disabled in
    several of them. Refusing is normal, not an error: the caller falls back to
    replacing the title instead of prefixing it.

    The answer is read raw, with the tty briefly in non-canonical mode so a
    reply that never comes cannot block playback.
    """
    import select
    import termios
    import tty as ttymod

    path = tty or terminal_path()
    if not path:
        return ""
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_NOCTTY", 0))
    except OSError:
        return ""
    try:
        try:
            previo = termios.tcgetattr(fd)
        except termios.error:
            return ""                      # not a real terminal
        try:
            ttymod.setraw(fd)
            os.write(fd, _OSC_QUERY_TITLE.encode("ascii"))
            datos = b""
            fin = time.time() + timeout
            while time.time() < fin:
                listo, _, _ = select.select([fd], [], [], max(0.0, fin - time.time()))
                if not listo:
                    break
                trozo = os.read(fd, 256)
                if not trozo:
                    break
                datos += trozo
                if datos.endswith(b"\033\\") or datos.endswith(b"\007"):
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previo)
    except (OSError, ValueError):
        return ""
    finally:
        os.close(fd)

    texto = datos.decode("utf-8", "replace")
    m = re.search(r"\033\]l(.*?)(?:\033\\|\007)", texto, re.DOTALL)
    return m.group(1).strip() if m else ""


def base_title(tty=""):
    """The tab's own text, asked once per tty and remembered.

    Asked once because the query costs a round trip to the terminal and the tab
    name does not change while a single file plays.
    """
    clave = tty or terminal_path() or "-"
    if clave not in _BASE_TITLE:
        _BASE_TITLE[clave] = read_terminal_title(tty)
    return _BASE_TITLE[clave]


def title_for(path, duration_seconds=0.0, tty=""):
    """The title text shown while `path` plays.

    The icon goes FIRST so a tab that is speaking is recognisable at a glance
    in a row of tabs, where everything after the first few characters is cut
    off anyway.

    When the terminal reports its own title, the icon is prefixed to it and the
    tab keeps its identity -- which is the whole point: you want to know *which*
    of your tabs is talking, and a tab renamed to the wav file no longer tells
    you that. When it does not, we fall back to naming the file, as before.
    """
    base = base_title(tty)
    if base:
        return "%s %s" % (TITLE_ICON, base)
    label = os.path.basename(path) or "audio"
    if duration_seconds and duration_seconds > 0:
        return "%s %s %s" % (TITLE_ICON, format_time(duration_seconds), label)
    return "%s %s" % (TITLE_ICON, label)


def restore_title(tty=""):
    """Put the tab's own text back, or clear it if we never learned it."""
    write_terminal_title(base_title(tty), tty)

def play_detached(path, preferred="", verbose=False, session=None, title=True):
    """Start playback in the background. Returns (pid, duration_seconds), or (None, 0).

    Does not itself block, but the audio may: it's queued behind a small runner
    process (see _playback_runner.py) that waits its turn on the machine-wide
    playback lock -- shared with play() and every other session -- before
    actually starting the player, so at most one stream plays at a time no
    matter which provider or session queued it. The returned pid is the
    runner's, which stays alive for the whole wait-then-play span and is what
    stop/pause/resume act on (signaled as a process group, since the runner's
    child player shares it).
    """
    cmd = find_player(path, preferred)
    if not cmd:
        return None, 0.0
    if verbose:
        print("+ %s &" % " ".join(cmd), file=sys.stderr)

    stop_previous(session=session)   # one playback at a time PER SESSION, not machine-wide
    try:
        length = duration(path)
    except (wave.Error, EOFError, OSError):
        length = 0.0

    tty = terminal_path() if title else ""
    runner_cmd = [sys.executable, "-m", "localtts._playback_runner",
                 playback_lock_path(), session or "", tty,
                 title_for(path, length)] + cmd
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(runner_cmd, **kwargs)
    except OSError as exc:
        raise TTSError("could not start %s: %s" % (cmd[0], exc))
    # segment_start=None until the runner actually gets its turn (see main() there) --
    # otherwise elapsed-time display would tick while the audio is still silently queued.
    _write_state(process.pid, path, duration_seconds=length, segment_start=None, session=session)
    return process.pid, length


# --- streamed playback -------------------------------------------------
#
# Long text sounds late: with one joined file nothing plays until the *last* fragment is
# rendered, which for a tagged story or a document is most of a minute of silence.
# Streaming inverts that -- each fragment is published the moment it exists and the
# runner plays them in order while the rest are still being made, so time-to-first-sound
# stops depending on the length of the text.
#
# The hand-off is a directory rather than a pipe because producer and runner are separate
# processes with no shared handles, and the runner may still be queued behind another
# session's audio when the first fragment lands. Parts are numbered, written under a
# temporary name and renamed into place, so the runner can never read a half-written wav.
# `done` records the final count, which is also how the runner learns the producer
# finished rather than died.

STREAM_DONE = "done"


def stream_new():
    return tempfile.mkdtemp(prefix="local-tts-stream-")


def stream_part_path(stream_dir, index):
    return os.path.join(stream_dir, "%04d.wav" % index)


def stream_add(stream_dir, index, src):
    """Publish one finished fragment. Copied, not moved: the producer still needs its own
    part to join into the single output file afterwards."""
    final = stream_part_path(stream_dir, index)
    temporary = final + ".part"
    shutil.copyfile(src, temporary)
    os.replace(temporary, final)      # atomic: no half-written wav is ever visible
    return final


def stream_finish(stream_dir, count):
    """Record that no further fragments are coming. Written the same rename-into-place
    way, so a runner polling for it never sees an empty file and reads 0 parts."""
    temporary = os.path.join(stream_dir, STREAM_DONE + ".part")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(str(int(count)))
        os.replace(temporary, os.path.join(stream_dir, STREAM_DONE))
    except OSError:
        pass


def stream_count(stream_dir):
    """Final fragment count, or None while the producer is still going."""
    try:
        with open(os.path.join(stream_dir, STREAM_DONE), "r", encoding="utf-8") as handle:
            return int((handle.read() or "0").strip())
    except (OSError, ValueError):
        return None


def stream_known_duration(stream_dir):
    """Total length of the fragments published *so far*. Grows as the producer works, so
    a progress bar over a stream is honest about what it knows rather than pretending to
    a total nobody can compute until synthesis ends."""
    total, index = 0.0, 0
    while True:
        part = stream_part_path(stream_dir, index)
        if not os.path.exists(part):
            return total
        total += _safe_duration(part)
        index += 1


def stream_cleanup(stream_dir):
    shutil.rmtree(stream_dir, ignore_errors=True)


def play_stream_detached(stream_dir, preferred="", verbose=False, session=None, title=True,
                         producer_pid=None, label=""):
    """Start a background runner that plays fragments from `stream_dir` in order as they
    appear. Returns (pid, popen), or (None, None) when no player exists at all.

    Unlike play_detached() the total duration is not known yet -- it grows as fragments
    land -- so the state file starts at zero and the runner keeps it current.
    """
    if not find_player(stream_part_path(stream_dir, 0), preferred):
        return None, None
    stop_previous(session=session)
    tty = terminal_path() if title else ""
    runner_cmd = [sys.executable, "-m", "localtts._playback_runner", "--stream",
                  playback_lock_path(), session or "", tty, "1" if title else "",
                  stream_dir, preferred or "", str(int(producer_pid or 0)),
                  label or stream_dir]
    if verbose:
        print("+ %s &" % " ".join(runner_cmd), file=sys.stderr)
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(runner_cmd, **kwargs)
    except OSError as exc:
        raise TTSError("could not start playback: %s" % exc)
    _write_state(process.pid, label or stream_dir, duration_seconds=0.0,
                 segment_start=None, session=session)
    return process.pid, process


def _elapsed(state):
    base = float(state.get("elapsed") or 0.0)
    segment_start = state.get("segment_start")
    if segment_start is None:
        return base
    return base + max(0.0, time.time() - float(segment_start))


def _signal_playback(sig, name, paused, session=None):
    """Send a signal to the recorded player. Returns (ok, message)."""
    state = read_state(session)
    if not state:
        return False, "nothing is playing"
    pid = int(state["pid"])
    if not is_running(pid):
        clear_state(session)
        return False, "nothing is playing (the last playback already finished)"
    if sig is None:
        return False, ("%s is not supported on this platform; use `stop` instead" % name)
    try:
        os.killpg(pid, sig)   # reaches the runner and its player child together (same group)
    except OSError as exc:
        return False, "could not %s pid %d: %s" % (name, pid, exc)

    elapsed = _elapsed(state)
    _write_state(pid, state.get("path") or "", duration_seconds=float(state.get("duration") or 0.0),
                paused=paused, elapsed=elapsed, segment_start=None if paused else time.time(),
                session=session)
    return True, "%s: %s" % (name, state.get("path") or pid)


def stop_previous(session=None):
    """Stop any playback this session started earlier, quietly."""
    state = read_state(session)
    if state and is_running(int(state["pid"])):
        stop_playback(session)
    else:
        clear_state(session)


def stop_playback(session=None):
    state = read_state(session)
    if not state:
        return False, "nothing is playing"
    pid = int(state["pid"])
    if not is_running(pid):
        clear_state(session)
        return False, "nothing is playing (the last playback already finished)"
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        # Resume first: a paused process never sees SIGTERM. killpg reaches the runner
        # and its player child together (same process group) whichever phase it's in --
        # still waiting its turn on the playback lock, or already playing.
        cont = getattr(signal, "SIGCONT", None)
        if cont is not None:
            try:
                os.killpg(pid, cont)
            except OSError:
                pass
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError as exc:
            return False, "could not stop pid %d: %s" % (pid, exc)
    path = state.get("path")
    clear_state(session)
    # SIGTERM/taskkill kills the runner outright, so its own title-clearing `finally`
    # never runs. We are the ones with a terminal here, so clear it from this side.
    write_terminal_title("")
    return True, "stopped: %s" % (path or pid)


def pause_playback(session=None):
    return _signal_playback(getattr(signal, "SIGSTOP", None), "paused", paused=True, session=session)


def resume_playback(session=None):
    return _signal_playback(getattr(signal, "SIGCONT", None), "resumed", paused=False, session=session)


def format_time(seconds):
    seconds = max(0, int(seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def progress_bar(elapsed, total, width=20):
    """A static text bar: '[####------] 0:04 / 0:12'. No terminal control codes —
    safe to print once and leave in a transcript, unlike a carriage-return spinner."""
    if total <= 0:
        return "[%s] %s" % ("?" * width, format_time(elapsed))
    # Real elapsed time can briefly exceed the file's duration -- the player takes a
    # moment to exit after the audio ends, and we only notice on the next refresh -- so
    # clamp for display, or the label reads like "0:11 / 0:09" for a second or two.
    elapsed = min(elapsed, total)
    filled = min(width, int(width * (elapsed / total)))
    bar = "#" * filled + "-" * (width - filled)
    return "[%s] %s / %s" % (bar, format_time(elapsed), format_time(total))


def compact_status(width=10, session=None):
    """A short one-line status for embedding in a host status bar: '🔊 0:03/0:12' while
    playing, '⏸ 0:03/0:12' while paused, '' (empty string) when nothing is — so a caller
    that only shows our text while it's non-empty needs no separate on/off check."""
    state = read_state(session)
    if not state:
        return ""
    pid = int(state["pid"])
    if not is_running(pid):
        clear_state(session)
        return ""
    icon = "⏸" if state.get("paused") else "🔊"
    elapsed = _elapsed(state)
    total = float(state.get("duration") or 0.0)
    if total <= 0:
        return "%s %s" % (icon, format_time(elapsed))
    elapsed = min(elapsed, total)   # see progress_bar()'s comment on the same clamp
    filled = min(width, int(width * (elapsed / total)))
    bar = "#" * filled + "-" * (width - filled)
    return "%s %s/%s [%s]" % (icon, format_time(elapsed), format_time(total), bar)


def playback_status(session=None):
    state = read_state(session)
    if not state:
        return False, "nothing is playing"
    pid = int(state["pid"])
    if not is_running(pid):
        clear_state(session)
        return False, "nothing is playing"
    label = "paused" if state.get("paused") else "playing"
    elapsed = _elapsed(state)
    total = float(state.get("duration") or 0.0)
    bar = progress_bar(elapsed, total)
    return True, "%s %s (pid %d): %s" % (label, bar, pid, state.get("path") or "?")
