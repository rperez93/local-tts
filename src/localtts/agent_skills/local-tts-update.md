---
name: local-tts-update
description: Update an already-installed local-tts CLI to the latest version — locate the repo behind the running `tts` command, pull, reinstall only if needed, refresh the skill/hook files that are copies rather than live links to it, and check whether a `command` provider template can now migrate to a real native provider (e.g. kokoro, rvc). Use when the user asks to update, upgrade, or get the latest local-tts, or when something documented (a flag, a setting, a skill) doesn't match what `tts` actually does.
---

# Updating `local-tts`

The PyPI distribution is `agents-local-tts`; commands remain `tts` and `local-tts`.
For PyPI installs use `pipx upgrade agents-local-tts` or the owning environment's
`python -m pip install --upgrade agents-local-tts`. Source installs use the git flow
below. Legacy editable distributions named `local-tts` must be uninstalled before
installing `agents-local-tts` in that same environment; do not leave competing entry
points. Preserve the configuration and model directories.

After either update, refresh skills and server scripts. Caches include implementation
identity, so changed code does not reuse stale speech. `tts cache prune` reclaims
expired entries; `tts cache clear` is an optional explicit reset.

## 1. Identify the installed distribution

Don't ask where it was cloned — resolve it from the running binary, the same way you would
diagnose any other "which install is this" question:

```bash
TTS_BIN=$(command -v tts || command -v local-tts)
VENV_PY=$(head -1 "$TTS_BIN" | sed 's/^#!//')     # the shebang is an absolute interpreter path
"$VENV_PY" -m pip show agents-local-tts local-tts
```

- **`Editable project location: /path/to/repo`** present → this is the normal install (a
  venv plus `pip install -e .`, optionally symlinked onto `PATH`). That path is the repo —
  `cd` into it for every step below.
- **No "Editable project location" and distribution `agents-local-tts`** → use
  `pipx upgrade agents-local-tts`, or the owning interpreter's
  `-m pip install --upgrade agents-local-tts`. No checkout is needed. Then refresh
  skills, hooks and servers in step 6 and verify in step 8; skip git-only steps.
- **Legacy static `local-tts` distribution** → preserve configuration/model directories,
  uninstall that legacy distribution in its owning environment, then install
  `agents-local-tts`. This migrates the package without inventing a missing checkout.

On Windows the shebang line isn't plain text the same way; instead run
`python -m pip show agents-local-tts local-tts` using whichever `python` the user activated when they
installed it (ask if that's unclear).

## 2. Source installs only: check for local changes before pulling

```bash
cd <repo>
git status --short
```

If this prints anything, **stop and ask** before pulling — do not discard someone's
in-progress edits with a `git pull` that could conflict or a `reset`. Uncommitted changes
under `src/` are the most likely reason someone is running an editable install in the first
place: they're customizing it.

## 3. See what's new, then pull

```bash
git fetch origin
git log HEAD..origin/main --oneline   # substitute the actual default branch if it isn't main
git pull
```

Keep that commit list — you'll want it to tell the user what actually changed, not just
"update complete."

## 4. Refresh yourself, and pick up the current instructions if you just changed

**The instructions you are following right now are a snapshot** — installed into your
skills directory the last time someone ran `tts skills --install`, not read live from the
repo you just pulled. The update process itself can change between versions (a step added,
removed, reordered, a flag renamed), so don't blindly keep following what's already loaded
once a newer copy might exist a few directories away.

If step 3's commit list was **empty** (nothing to pull), skip this step — there is nothing
newer to pick up. Otherwise, refresh every installed skill from what you just pulled — the
same idempotent write `tts skills` always documents, so it costs nothing even when this
skill didn't change:

```bash
tts skills --install
```

Then print this skill's **current** content directly, straight from the install you just
refreshed rather than whatever's cached in your own context or your host's skill loader:

```bash
tts skills --print local-tts-update
```

**Treat that printed text as your instructions for the rest of this task, replacing what
you started with.** This is the deterministic path — it does not depend on whether your
host re-reads a skill file mid-session (most don't reliably), because the command output
lands directly in what you can already see, the same as any other command's output. Note
where you are (you've done steps 1-4) and resume from step 5 in whatever the printed
version calls it — steps may have moved.

Keep what you already learned in steps 1-3 (the repo path, the commit list) — you're
continuing the same task, not starting over.

At the very end (the last step, wherever it ends up), **tell the user to restart their
agent or start a new session.** Printing solved *this* conversation; it did not change what
your host loads automatically next time, and if step 4's instructions themselves moved or
changed, the only way everything (not just this one update) is guaranteed current going
forward is a fresh start.

## 5. Reinstall — only sometimes needed, but always safe

**Editable install:** since `src/localtts/` is loaded live, ordinary code changes take
effect on the very next `tts` invocation — no reinstall required. The one case that *does*
need one is a `pyproject.toml` change (a new entry point, a version bump, a bumped Python
floor) or a genuinely new dependency. Rather than diffing `pyproject.toml` yourself to
decide, just run it — it's idempotent and costs nothing here, since the package has zero
runtime dependencies:

```bash
pip install -e .        # using the venv's own pip (activate it, or call it by full path)
```

**Source-based pipx install only:** pipx never re-reads the source directory on its own — reinstall:

```bash
pipx install . --force
```

## 6. Refresh what lives *outside* the repo

For a PyPI update, run `tts skills --install` now; source installs refreshed skills
in step 4. One more thing `local-tts` writes elsewhere
is a **copy made at install time**, not a live pointer into the repo — pulling does not
update it on its own:

**The status-bar hook**, if one is installed (`tts hooks --status` to check first). The
wrapper script `tts hooks --install` writes is also generated once, not linked live:

```bash
tts hooks --install     # only if `tts hooks --status` shows one is active
```

**A kokoro or rvc persistent server**, if either is configured (`kokoro.server_url` /
`rvc.server_url` set — `tts check` shows whether one is currently running). Its script
(`kokoro_server.py` / `rvc_server.py`) lives inside the provider's own venv, written there
once by an agent following the configure skill. **`git pull` never touches it**, and it is
the single most likely thing to be stale after an update: the script is *not* installed by
`tts skills --install` either, so nothing refreshes it automatically.

`tts servers` does the comparison for you — it reads the script out of the freshly
pulled skill and diffs it against what is actually on disk, wherever `server_start`
points:

```bash
tts servers              # what is installed, and whether it matches this version
tts servers --refresh    # rewrite the stale ones and stop the running server
```

Run this after **every** update, not only when the commit list happens to mention
servers: a server script silently missing a flag looks like a voice-quality problem, not
like a stale file, and that is a bad afternoon.

`--refresh` keeps the previous script as `<name>.bak` — `stale` only means "differs from
this version's template", which includes a script somebody edited on purpose (a different
model directory, an extra flag). If the backup shows an edit worth keeping, re-apply it
on top of the new file and say so; do not silently discard someone's change.

It also stops a server that is running, so the next `tts` call starts the new script
instead of talking to the old code for another five minutes. A server installed before
`/shutdown` existed cannot be stopped that way — `tts servers --refresh` says so plainly
when it happens, and that one has to age out on its own idle timeout or be killed.

Capabilities added to these scripts over time that an older copy will not have — check for
each, since a missing one fails silently rather than erroring:

- **multiple models in one server** (`--model NAME=PATH`, repeatable, plus `/models`), so
  one process serves several languages instead of one server per voice
- **conversion parameters at startup** (`--index-rate`, `--protect`, `--f0method`,
  `--pitch`) — without these the server runs rvc-python's own defaults no matter what
  `tts config` says, because the request body carries only `input_path`, `model` and
  `pitch`
- **`--idle-timeout`**, which releases the model (and VRAM) after 5 minutes idle

If you rewrite an rvc server that was serving one voice per port, this is also the moment
to collapse those into a single multi-model server and set `rvc.language_models` — mention
it to the user, since it frees a whole copy of torch.

## 7. Check for a `command` provider that a new update now supports natively

Before local-tts had a real `kokoro` or `rvc` provider, the only way to drive either was
the generic `command` escape hatch — a hand-written template like
`kokoro-tts -o {output} -v ef_dora -l es {text}`. If an update just added native support
for something the user was already running that way, they're now on the harder-to-maintain
path for no reason. Check every time you update, not just once:

```bash
tts config --detect-migrations
```

Prints nothing if there's nothing to migrate — nothing to do. If it finds a match, it
prints the exact `tts config --set` commands that would switch to the native provider
(never applies them itself). **Ask before running them** — the same rule as everywhere
else this skill touches configuration. If the user says yes:

```bash
tts config --set kokoro.voice=ef_dora    # whatever --detect-migrations printed
tts config --set kokoro.lang=es
tts check                                 # confirm the native provider is [ok] too
```

Leave `command.template` itself alone — migrating only adds the native provider's
settings, it never deletes the old template, so it stays there as a fallback. If the
migrated provider was the user's actual default (`--detect-migrations` says so
explicitly, appending a `provider=<name>` line), ask separately before switching that,
since it changes what plays by default rather than just adding an option.

This detector only knows about tools with a real provider today — it will not (yet) flag
some other CLI wired through `command`, and that's fine, there's nothing to migrate it to.

## 8. Verify, and report what changed

```bash
tts --version
tts check
tts skills
```

Read the `tone shaping:` line in `tts check`. If it says **built-in WSOLA**, ffmpeg is
missing, and every tone tag that changes pacing (`<happy>`, `<sad>`, `<whisper>`, most of
them) is being retimed in pure Python instead of through ffmpeg's `atempo`. That is
listenable but audibly noisier, and nothing else ever reports it — so an update is a
natural moment to offer it:

```bash
sudo apt install ffmpeg     # Debian/Ubuntu (needs sudo — ask first)
brew install ffmpeg         # macOS
```

Say what it improves and let the user decide; never install it as part of a routine
update. Skip the offer entirely if the line already says `ffmpeg atempo`.

Confirm the version actually moved and that `tts check` still shows `[ok]` on the user's
default provider — a missed `pyproject.toml`-driven reinstall (step 5) is exactly the kind
of thing this would catch. Then tell the user what changed, using the commit list from step
3, rather than just reporting success silently. Mention anything migrated in step 7 too.

**If step 4 printed this skill's content instead of re-invoking it**, close by telling the
user to restart their agent or start a new session — say so plainly, it's not optional
cleanup. This update is already complete either way; the restart is about their *next*
conversation loading everything (not just this skill) the normal way instead of whatever
got it here this time.

For v2, verify `tts settings --help`, `tts cache status`, and `tts warm --help`.
All eight agent targets now receive native SKILL.md directories; migrating a complete
installation backs up/removes only the old managed instruction block. Restart the
agent session after `tts skills --install`. The PyPI distribution rename does not
rename config files, models, commands, or the `localtts` Python package.
