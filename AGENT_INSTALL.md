# Agent install guide for `local-tts`

**Audience: an AI coding agent.** A human has asked you to install this tool on their
machine. Follow this document top to bottom. It is self-contained — you do not need to
read the rest of the repository first.

The human triggers you with something like:

> "Install local-tts."
> "I want to install this with the link."
> "Set this up with piper in Spanish, and make it global."

---

## Rules

1. **Never install anything, download a model, create a symlink, or run `sudo` without
   asking first.** Every step below marked **ASK** is a stop point. Present what you are
   about to do, wait for a yes.
2. **Detect before you ask.** Run the detection commands first so your questions are about
   what is actually missing, not about everything.
3. **Batch your questions.** Ask once, up front, covering every decision you found — do
   not interrupt the human six times.
4. **Report honestly.** If a step fails, say so and stop; do not paper over it or claim
   success. If you skip something, say what and why.
5. **Recommend the persistent server for `kokoro` and `rvc`.** Both reload their model
   on every call otherwise. Recommending is not deciding: it is still an **ASK**, because
   it leaves a background process running.
6. **Do not modify the user's shell config** (`.bashrc`, `.zshrc`, `PATH` exports) unless
   they explicitly ask. Prefer the symlink approach in Step 5.

### Consent shortcuts

Map the human's phrasing onto the questions so you do not re-ask what they already
answered:

| If they said… | Treat as answered |
| --- | --- |
| "with the link", "make it global", "on my PATH" | Step 5 (symlink) = **yes** |
| "with piper", "with kokoro" | that backend's step = **yes** |
| "with rvc" | there is no rvc step here — finish this install, then follow the `local-tts-configure` skill |
| "for Spanish/French/Italian/…", for a language kokoro speaks | Step 3 (kokoro, with a voice for that language) = **yes** |
| "for German/Polish/Russian/…", for any other language | Step 4 (piper — kokoro has no voice for it) = **yes** |
| "make it fast", "don't wait every time" | the persistent server in Step 3 = **yes** |
| "just the CLI", "no extras" | Steps 3b, 4 and 5 = **no** |
| "install everything", "all of it", "don't ask" | All steps = **yes**, but still show the plan before running |

Anything they did not cover, you still ask.

---

## Step 0 — Detect the current state

Run these and keep the results. None of them change anything.

```bash
# repo + python
pwd && ls pyproject.toml 2>/dev/null && echo "repo: ok"
python3 --version                       # need >= 3.9
python3 -c "import ensurepip" 2>&1      # if this errors, venv creation will fail

# is local-tts already installed?
command -v tts && tts --version

# backends -- kokoro is the default, check it first
command -v kokoro-tts || ls ~/.local/share/kokoro-venv/bin/python 2>/dev/null
ls ~/.local/share/kokoro-models/ 2>/dev/null
command -v piper || ls ~/.local/share/piper-venv/bin/piper 2>/dev/null
command -v llama-tts && llama-tts --version 2>&1 | head -1

# audio players (any one is enough)
for p in ffplay paplay aplay afplay play mpv cvlc; do command -v $p; done
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL: powershell.exe fallback available"

# where a symlink would go
echo "$PATH" | tr ':' '\n' | grep -E "\.local/bin|/usr/local/bin"
ls -la ~/.local/bin/tts 2>/dev/null && echo "WARNING: ~/.local/bin/tts already exists"
```

Summarize for the human as a short table: **present / missing** for each of python, venv
support, kokoro, piper, llama.cpp, an audio player.

**ASK now**, in one message, only about what is missing or undecided:

- Install kokoro? (**the default provider** — English, Spanish, French, Italian,
  Portuguese, Hindi, Japanese and Mandarin; this is the one to install unless their
  language is outside that set, or they specifically want something else)
- Run kokoro as a persistent server? (**recommended** — see Step 3; it is the difference
  between about a second per sentence and several, and IPA phonetics need it)
- Install piper as well, and for which language/voice?
- Install llama.cpp? (only if they specifically want it — English, Chinese, Japanese and
  Korean only)
- Create the global symlink?
- Install an audio player if none was found?

---

## Step 1 — Python environment

Requires **Python ≥ 3.9**.

If `import ensurepip` failed in Step 0, `python3 -m venv` will not work. On Debian/Ubuntu
that means `python3-venv` is missing. Two ways out — **ASK** which:

```bash
# a) system package (needs sudo)
sudo apt install python3-venv

# b) no sudo: use a pyenv interpreter if one exists
ls ~/.pyenv/versions
~/.pyenv/versions/<VERSION>/bin/python -m venv .venv
```

Otherwise:

```bash
cd <repo>
python3 -m venv .venv
.venv/bin/python -V          # confirm it is the interpreter you expect
```

> If a `.venv` already exists but is broken (a failed earlier attempt), `rm -rf .venv` and
> recreate it — a half-built venv keeps a `bin/python` symlink to the wrong interpreter.

---

## Step 2 — Install the package

The project metadata uses PEP 639 licence fields, which need **pip ≥ 24.2** and
**setuptools ≥ 77**. Upgrade pip first or the install fails with
`configuration error: project.license must be string`.

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e .
.venv/bin/tts --version
```

`local-tts` itself has **zero runtime dependencies** — if pip installs anything besides
`local-tts`, something is wrong; stop and report it.

---

## Step 3 — kokoro (the default provider) — **ASK**

`kokoro` is what `tts` uses with no configuration at all: Kokoro-82M, fully offline, eight
languages, small. There is no single official CLI, so this installs the `kokoro-onnx`
package in **its own venv** (never into `.venv` — local-tts has zero runtime dependencies
and must keep them) plus a small wrapper the `kokoro.binary` default finds on `PATH`.

```bash
python3 -m venv ~/.local/share/kokoro-venv
~/.local/share/kokoro-venv/bin/pip install kokoro-onnx soundfile

mkdir -p ~/.local/share/kokoro-models
# fetch kokoro-v1.0.onnx and voices-v1.0.bin into it, e.g. from
# https://github.com/nazdridoy/kokoro-tts/releases
```

The wrapper script and the `~/.local/bin/kokoro-tts` shim are in the README's
[kokoro section](https://github.com/rperez93/local-tts#kokoro--the-default-small-fast-offline-eight-languages)
and in the `local-tts-configure` skill — **copy them verbatim**, they are not worth
improvising. Then pick a voice for the human's language and prove it works:

```bash
.venv/bin/tts config --set kokoro.language_voices.en=af_heart
.venv/bin/tts config --set kokoro.language_voices.es=ef_dora   # if they speak Spanish
.venv/bin/tts -p kokoro --no-play -o /tmp/k.wav "Kokoro is installed."
```

### Then offer the persistent server — **ASK, and recommend it**

The wrapper above reloads the whole model from disk on **every call**. That load, not the
synthesis, is most of the wait. Put it to the human as a recommendation, not a neutral
option:

- **Faster:** a warm server answers a sentence in about a second; without one, every
  single call pays several seconds of model loading first.
- **It is the only way to get phonetics.** `kokoro.emphasis_lengthen` and IPA
  pronunciation entries need the phonemizer, and the phonemizer lives in the server. On
  the subprocess wrapper they silently do nothing.
- **One server, many voices/languages** — one process, one model in RAM.
- **The cost:** a background process holding RAM while it lives, exiting on its own after
  5 idle minutes and restarting transparently on the next call.

The server script is in the `local-tts-configure` skill (self-contained, stdlib only).
Copy it into `~/.local/share/kokoro-venv/`, then:

```bash
.venv/bin/tts config --set kokoro.server_url=http://127.0.0.1:8765
.venv/bin/tts config --set 'kokoro.server_start=~/.local/share/kokoro-venv/bin/python ~/.local/share/kokoro-venv/kokoro_server.py --port 8765'
.venv/bin/tts check          # says whether it is running or will auto-start
```

**Never start a background process without asking first.** If they decline, leave
`server_url` empty — the per-call path works, just slower and without phonetics.

---

## Step 3b — llama.cpp — **ASK, only if they want it**

Not the default, and worth installing only if the human asked for it or already has it:
the default OuteTTS weights speak **English, Chinese, Japanese and Korean only** and read
every other language with English phonetics. This repo neither bundles nor builds
llama.cpp.

```bash
# macOS / Linux
brew install llama.cpp

# Windows
winget install llama.cpp

# Prebuilt binaries: https://github.com/ggml-org/llama.cpp/releases
#   -> llama-<build>-bin-<platform>.zip, unzip, put the bin/ dir on PATH

# From source
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build && cmake --build build --config Release -j
# binaries land in build/bin
```

Verify, and register a non-PATH location if needed:

```bash
llama-tts --version
# only if it is not on PATH:
.venv/bin/tts config --set llamacpp.binary=/full/path/to/llama-tts
```

**Model weights are downloaded on first use, not now** (~640 MB of OuteTTS + WavTokenizer
into `~/.cache/huggingface/hub`). Tell the human the first run will be slow.

⚠️ **Language limit — surface this proactively.** The default OuteTTS weights speak
**English, Chinese, Japanese and Korean only**. Any other language is rendered with
English phonetics and sounds wrong. If the human mentioned another language, point them
back at Step 3 (kokoro) or on to Step 4 (piper) rather than leaving this as their
backend.

---

## Step 4 — piper (an alternative to kokoro) — **ASK**

Piper covers ~40 languages and runs ~7× realtime on CPU. It is not needed if kokoro is
installed and the human is happy with its voices — install it when they want a different
voice for a language, or one flat `.onnx` file per voice.

Two upstreams; know the difference before you answer questions about it:

| | `rhasspy/piper` | `OHF-Voice/piper1-gpl` ← **use this** |
| --- | --- | --- |
| Licence | MIT | **GPL-3.0** |
| Status | archived, last release Nov 2023 | actively maintained |
| Ships as | standalone binary | Python wheel (`piper-tts`) |

**Install it in its own virtualenv.** Piper pulls in onnxruntime and numpy (~200 MB);
putting those in this project's `.venv` would destroy its zero-dependency property. The
`piper.binary` setting exists precisely so the two stay separate.

```bash
python3 -m venv ~/.local/share/piper-venv
~/.local/share/piper-venv/bin/pip install piper-tts
```

Pick a voice — **ASK** which language/region if they have not said:

```bash
# list every available voice
~/.local/share/piper-venv/bin/python -m piper.download_voices

mkdir -p ~/.local/share/piper-voices && cd ~/.local/share/piper-voices
~/.local/share/piper-venv/bin/python -m piper.download_voices <VOICE>
```

Voice names are `<lang>_<REGION>-<speaker>-<quality>`, quality ∈ `x_low|low|medium|high`
(a `high` voice is ~60–65 MB). Weights come from
[`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) — public, MIT/CC, no
API token or account. Prefer the region that matches the human's audience, e.g. `es_MX`
for Latin American Spanish vs `es_ES` for Castilian.

Register it:

```bash
.venv/bin/tts config --set piper.binary=~/.local/share/piper-venv/bin/piper
.venv/bin/tts config --set piper.model=~/.local/share/piper-voices/<VOICE>.onnx
```

**ASK** whether piper should become the default provider (right answer if their content is
mostly not English):

```bash
.venv/bin/tts config --set provider=piper
```

---

## Step 5 — Global symlink ("the link") — **ASK**

This is what "install this with the link" refers to. It makes `tts` work in any directory
without activating the venv, because the entry point's shebang is an absolute path to the
venv's interpreter.

Check the target is free and the directory is on `PATH` **before** creating it:

```bash
echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin" && echo "on PATH"
ls -la ~/.local/bin/tts 2>/dev/null   # must not already exist
```

- If `~/.local/bin` is **not** on `PATH`: report it and ask before touching shell config;
  do not silently edit `.bashrc`/`.zshrc`.
- If `~/.local/bin/tts` **already exists**: stop and show the human what it points to. Do
  not overwrite it without explicit permission.

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/.venv/bin/tts" ~/.local/bin/tts
# optional second name:
ln -s "$(pwd)/.venv/bin/local-tts" ~/.local/bin/local-tts
```

Tell them the consequences:

- The install is **editable**, so edits to `src/localtts/` take effect immediately.
- **Do not move or delete the repo** — the symlink and shebang both point into it.
- Uninstall the link with `rm ~/.local/bin/tts`.

---

## Step 6 — Audio player — **ASK if none was found**

No audio library is installed by this package; playback uses whatever CLI player exists:
`ffplay` → `paplay` → `aplay` → `afplay` → `play` → `mpv` → `cvlc`. On WSL with none of
them, it falls back to Windows' `powershell.exe` player automatically.

```bash
sudo apt install ffmpeg     # Debian/Ubuntu (needs sudo — ask)
brew install ffmpeg         # macOS
```

Without a player, synthesis still works: the file is kept and its path printed instead of
being played.

**Recommend ffmpeg even when a player was already found, and say why.** It does a second,
less obvious job: tone tags (`<happy>`, `<sad>`, `<whisper>`) change a span's pacing, and
on any backend without its own rate control that retiming is done to the rendered audio.
With ffmpeg it goes through `atempo`; without it, through a built-in WSOLA stretch —
listenable, but measurably noisier. A user who never installs ffmpeg gets audibly worse
tagged speech and no error explaining it, so `tts check` reports which one is in use.
It is not required, and it needs `sudo` on Linux — mention the benefit and ask, don't
install it silently.

---

## Step 6b — Agent skills and the language memory — **ASK**

Install the skills that teach coding agents (this one included) to use the CLI:

```bash
tts skills                 # what is detected and what is installed
tts skills --install       # every detected agent
```

Then record which backend speaks the human's language, so the preference is shared by every
agent instead of living in one session:

```bash
tts languages --set es=kokoro:ef_dora
tts languages --set en=kokoro:af_heart
tts languages --set de=piper:~/.local/share/piper-voices/de_DE-thorsten-high.onnx
tts languages
```

If you installed piper or llama.cpp in Step 4 or 3b, **record which language each one
speaks here** — otherwise the next agent will not know it exists and will fall back to the
default provider, which may have the wrong phonetics for that language.

Tell the human that skills only take effect after restarting their agent.

## Step 6c — Phonetics for borrowed words — **ASK**

Technical Spanish is full of English words, and vice versa. Read with the host
language's phonetics they sound wrong -- "pull request" as *pull rekest*. The
pronunciation dictionary fixes that: a value between slashes is IPA rather than a
respelling.

```bash
tts config --set 'pronunciations.pull request=/pˈʊl ɹᵻkwˈɛst/'
tts config --set pronunciations.kubectl="kube control"       # a plain respelling
tts check                                                     # the `phonetics:` line
```

Both kinds live in one table. A respelling is rewritten into the text and works on every
backend; IPA is handed to the model as phonemes and needs a backend with a phonemizer --
`kokoro` with `server_url` set, or `rvc` over a kokoro base. `tts check` prints which,
and an entry a backend ignores is not an error: the word is still said, just its own way.

**Say this rather than letting them find out:** if they are on piper or llamacpp, IPA
entries will do nothing until they set up the kokoro server (Step 6 in
`local-tts-configure`). Respellings still work.

Get the transcription from Wiktionary, or from `espeak-ng --ipa -q -v en "<word>"`. Use
the language the word comes *from* -- that is the whole point.

## Step 7 — Validate

Run all of these and show the real output:

```bash
tts --version                 # or .venv/bin/tts if no symlink was created
tts                           # with no args, prints the full parameter list
tts check                     # per-provider readiness + detected players
tts languages                 # recorded language preferences
tts skills                    # agent skill status
```

`tts check` must show `[ok]` on the row for the **default provider**. Other rows may be
`[--]`; that is fine and expected (e.g. `openai` with no API key).

Then a real synthesis, in the human's language:

```bash
tts --no-play -o /tmp/localtts-smoke.wav "Hello, the installation works."
python3 -c "import wave; w=wave.open('/tmp/localtts-smoke.wav'); \
print('%d Hz, %.1fs' % (w.getframerate(), w.getnframes()/w.getframerate()))"
rm -f /tmp/localtts-smoke.wav
```

A non-zero duration means the pipeline works end to end. Optionally run the test suite:

```bash
.venv/bin/python -m unittest discover -s tests
```

---

## Step 8 — Report

Close with a short summary containing:

- What you installed, and **where** — only the ones you actually created: repo `.venv`,
  `~/.local/share/kokoro-venv` + `~/.local/share/kokoro-models` + the
  `~/.local/bin/kokoro-tts` shim, `~/.local/share/piper-venv` + `~/.local/share/piper-voices`
- What you skipped and why
- Whether the symlink was created, and the exact path
- The `tts check` result
- Which agents received the skills, and that a restart is needed
- What was recorded in `tts languages`
- The one command they will use day to day, e.g.
  `tts --lang es -f documento.md -o documento.wav`
- How to undo it, naming only what you installed:
  `rm ~/.local/bin/tts ~/.local/bin/kokoro-tts`,
  `rm -rf .venv ~/.local/share/kokoro-venv ~/.local/share/kokoro-models`,
  `rm -rf ~/.local/share/piper-venv ~/.local/share/piper-voices`

---

## Known failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ensurepip is not available` | Debian/Ubuntu without `python3-venv` | `sudo apt install python3-venv`, or use a pyenv interpreter |
| `.venv/bin/python -V` reports an unexpected version | leftover `.venv` from a failed run | `rm -rf .venv` and recreate |
| `configuration error: project.license must be string` | pip/setuptools too old for PEP 639 | `pip install --upgrade pip`, then reinstall |
| pip installs numpy/onnxruntime into the project venv | kokoro or piper installed in the wrong environment | undo; use a separate venv per Step 3 / Step 4 |
| Speech is in the right words but the wrong accent | a backend used for a language it does not support, e.g. OuteTTS outside EN/ZH/JA/KO | switch to kokoro (Step 3) or piper (Step 4), and record it in `tts languages` |
| Every call takes several seconds before any audio | kokoro or rvc running per-call instead of as a server | set up the persistent server — Step 3 for kokoro, the `local-tts-configure` skill for rvc |
| An IPA pronunciation entry does nothing | the backend has no phonemizer | it needs kokoro with `server_url` set; `tts check` says which backends honour them |
| `tts` not found after the symlink | `~/.local/bin` not on `PATH` | report it; ask before editing shell config |
| `command not found: <binary>` from `tts` | a configured provider binary is missing | `tts check` names it; fix the path with `tts config --set <provider>.binary=…` |
| Traceback instead of a one-line error | a genuine bug | report it; the CLI is supposed to print `tts: error: …` |

## v2.0 installation and validation

For normal usage prefer `pipx install agents-local-tts`, or
`python -m pip install agents-local-tts` inside the selected virtual environment.
Use editable source installs for development. The commands remain `tts` / `local-tts`.
When migrating the legacy `local-tts` distribution in the same environment, uninstall
that old distribution before installing `agents-local-tts`; preserve config/models.

After backend setup run `tts check`, `tts skills --install`, and
`tts servers --refresh`. Verify a requested language with the user's actual player.
`tts settings` exposes all settings in a terminal; `tts config --set` remains the
scriptable interface. For latency, offer the bounded disk cache (36 hours/256 MiB)
and explicit `tts warm --lang CODE --keep-alive 1800`. These are configurable, and
model residency uses RAM/VRAM. Follow existing user authorization rather than asking
again for an already approved installation or configuration.

See [agent support](docs/agents.md) for current native paths for Codex, Claude Code,
OpenCode, Cursor, Gemini, Windsurf, Copilot CLI and Qwen. Native skills do not imply
that every agent exposes a live status-bar hook, or that remote audio plays locally.
