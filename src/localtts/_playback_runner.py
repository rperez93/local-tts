"""Internal worker spawned by audio.play_detached() -- not a public entry point.

Detached playback must not block the calling `tts` process, but the audio itself
still has to wait its turn if something else (any session, any provider) is
already playing. So play_detached() doesn't launch the player directly: it
launches this script, which blocks *itself* on the machine-wide playback lock
first, marks the state file as actually playing once it gets its turn, then
runs the real player to completion before releasing the lock. The caller sees
none of that waiting.

It is also what paints (and, crucially, un-paints) the terminal title: this
process is the only one that lives for exactly as long as the audio does. The
tty path is passed in rather than discovered here, because start_new_session()
in play_detached() leaves this process without a controlling terminal.

Invoked as:
    python -m localtts._playback_runner <lock_path> <session-or-empty>
        <tty-or-empty> <title-or-empty> <player argv...>

or, for streamed playback (audio.play_stream_detached):
    python -m localtts._playback_runner --stream <lock_path> <session-or-empty>
        <tty-or-empty> <title-flag> <stream_dir> <player-or-empty> <producer_pid>
        <label>
"""

import os
import signal
import subprocess
import sys
import time

from localtts import audio, lock

#: How often to look for the next fragment. Short enough to be inaudible as a gap
#: between fragments, long enough not to spin a core while synthesis is the slow part.
POLL_SECONDS = 0.05


def _play(cmd):
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   stdin=subprocess.DEVNULL, env=audio.player_environment())


def run_stream(argv):
    """Play `stream_dir`'s fragments in order, waiting for each one to be published.

    Holds the machine-wide playback lock across the *whole* stream, not per fragment:
    releasing between fragments would let another session cut in halfway through a
    sentence. The producer's pid is watched so a crashed producer ends the stream instead
    of leaving the runner waiting for a fragment that will never arrive.
    """
    lock_path, session = argv[0], (argv[1] or None)
    tty, title_on = argv[2], bool(argv[3])
    stream_dir, preferred = argv[4], (argv[5] or "")
    producer_pid, label = int(argv[6] or 0), argv[7]

    # SIGTERM (what `tts stop` sends the group) would otherwise skip the cleanup below,
    # leaving the title set and the stream directory behind.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    with open(lock_path, "a+") as handle:
        lock.acquire(handle)
        painted = False
        index, elapsed = 0, 0.0
        try:
            while True:
                part = audio.stream_part_path(stream_dir, index)
                if os.path.exists(part):
                    cmd = audio.find_player(part, preferred)
                    if not cmd:
                        break
                    # Total grows as fragments land; report what is known now rather
                    # than a number nobody can have until synthesis ends.
                    total = audio.stream_known_duration(stream_dir)
                    audio._write_state(os.getpid(), label, duration_seconds=total,
                                       paused=False, elapsed=elapsed,
                                       segment_start=time.time(), session=session)
                    if title_on:
                        audio.write_terminal_title(audio.title_for(label, total, tty), tty)
                        painted = True
                    _play(cmd)
                    elapsed += audio._safe_duration(part)
                    index += 1
                    continue
                count = audio.stream_count(stream_dir)
                if count is not None:
                    if index >= count:
                        break
                elif producer_pid and not audio.is_running(producer_pid):
                    break        # producer died without publishing a final count
                time.sleep(POLL_SECONDS)
        finally:
            if painted:
                audio.restore_title(tty)
            lock.release(handle)
            audio.stream_cleanup(stream_dir)


def main(argv):
    if argv and argv[0] == "--stream":
        return run_stream(argv[1:])
    lock_path, session = argv[0], (argv[1] or None)
    tty, title = argv[2], argv[3]
    player_cmd = argv[4:]
    with open(lock_path, "a+") as handle:
        lock.acquire(handle)
        try:
            state = audio.read_state(session)
            if state and int(state.get("pid") or -1) == os.getpid():
                audio._write_state(
                    os.getpid(), state.get("path") or "",
                    duration_seconds=float(state.get("duration") or 0.0),
                    paused=False, elapsed=0.0, segment_start=time.time(), session=session,
                )
            # Only once we hold the lock: while queued behind another session the
            # audio is silent, and a speaker icon then would be a lie.
            if title:
                audio.write_terminal_title(title, tty)
            try:
                subprocess.run(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, env=audio.player_environment())
            finally:
                if title:
                    audio.restore_title(tty)
        finally:
            lock.release(handle)


if __name__ == "__main__":
    main(sys.argv[1:])
