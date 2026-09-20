"""The persistent servers' own scripts: where they are, and whether they are current.

The script kokoro and rvc run is not part of this package. It is a copy written into the
provider's own venv by whoever followed the `local-tts-configure` skill, which makes it
the one piece of an install that goes stale in silence: `git pull` never touches it, and
an older copy answers `/health` perfectly well while dropping whatever it never learned
to read. A capability added on this side then does nothing, and nothing anywhere says
why -- which is exactly how the pronunciation dictionary's IPA entries could reach a
server that quietly ignored them.

So the comparison is made against the bundled skill itself -- the same text
`tts skills --print local-tts-configure` prints, and the same text an agent copies when
it installs a server in the first place. One source of truth, rather than a second copy
of the script kept in this package that could disagree with the documentation.
"""

import os
import shlex
import urllib.error
import urllib.request

from localtts import skills
from localtts.errors import TTSError

#: Providers that talk to a persistent server, and the file name each one's script is
#: written to by the configure skill's `cat > ... <<'EOF'` block.
SERVER_SCRIPTS = {"kokoro": "kokoro_server.py", "rvc": "rvc_server.py"}

#: What `state` a record can carry. `stale` is only ever "differs from the bundled
#: template" -- which includes a script somebody edited on purpose, so a refresh keeps
#: the old one beside it rather than assuming the difference was rot.
CURRENT, STALE, MISSING, UNCONFIGURED = "current", "stale", "missing", "unconfigured"


def template(provider, skill_text=None):
    """The script this version expects, read out of the bundled configure skill."""
    name = SERVER_SCRIPTS.get(provider)
    if not name:
        raise TTSError("%s does not run a persistent server" % provider)
    text = skill_text if skill_text is not None else skills.read_skill("local-tts-configure")
    marker = "%s <<'EOF'\n" % name
    start = text.find(marker)
    if start == -1:
        raise TTSError("the bundled local-tts-configure skill has no %s block -- "
                       "reinstall local-tts" % name)
    start += len(marker)
    end = text.find("\nEOF\n", start)
    if end == -1:
        raise TTSError("the %s block in the bundled skill is not terminated" % name)
    return text[start:end + 1]


def script_path(settings):
    """The `.py` this provider's `server_start` actually runs, or "" if it names none.

    Read out of the command rather than assumed to be where the skill suggests: the
    whole point of `server_start` is that the user chose where that venv lives.
    """
    try:
        parts = shlex.split(settings.get("server_start") or "")
    except ValueError:                      # an unbalanced quote is the user's problem
        return ""
    for part in parts:
        if part.endswith(".py"):
            return os.path.expanduser(part)
    return ""


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def entries(cfg):
    """One record per provider configured to talk to a server, in a stable order."""
    records = []
    for provider in sorted(SERVER_SCRIPTS):
        settings = (cfg.get("providers") or {}).get(provider) or {}
        if not settings.get("server_url"):
            continue
        path = script_path(settings)
        wanted = template(provider)
        if not path:
            state = UNCONFIGURED
        elif _read(path) is None:
            state = MISSING
        else:
            state = CURRENT if _read(path) == wanted else STALE
        records.append({"provider": provider, "url": settings["server_url"],
                        "path": path, "state": state, "template": wanted})
    return records


def refresh(record):
    """Write the current script, keeping whatever was there as `<name>.bak`.

    The backup is not politeness: `stale` cannot tell rot from a deliberate edit (a
    different model directory, an extra flag), so the previous file has to survive.
    """
    path = record["path"]
    if not path:
        raise TTSError("%s has no .py in its server_start, so there is nothing to "
                       "refresh -- see the local-tts-configure skill" % record["provider"])
    previous = _read(path)
    if previous is not None:
        with open(path + ".bak", "w", encoding="utf-8") as handle:
            handle.write(previous)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        raise TTSError("%s is not there, so %s's server script cannot be written -- "
                       "install the backend first (local-tts-configure skill)"
                       % (directory, record["provider"]))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(record["template"])
    return path + ".bak" if previous is not None else ""


def shutdown(url, timeout=2):
    """Ask a running server to exit, so a refreshed script is what runs next.

    Returns True when it is going down. A server older than `/shutdown` answers 404 and
    keeps running: that is not an error here, it just means the new file only takes over
    once the old process ages out on its own idle timeout.
    """
    request = urllib.request.Request(url.rstrip("/") + "/shutdown", data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError:
        return False
    except (urllib.error.URLError, OSError, ValueError):
        # Cut off mid-answer, or already gone. Either way nothing is serving that port,
        # which is what was being asked for.
        return True


def wait_stopped(url, timeout=5):
    """Wait for the shutdown acknowledgement to become an actually closed listener."""
    import http.client
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=.2):
                pass
        except urllib.error.HTTPError:
            pass
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            return True
        time.sleep(.05)
    return False
