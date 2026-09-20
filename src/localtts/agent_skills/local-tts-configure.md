---
name: local-tts-configure
description: Install, diagnose, and configure local-tts backends, per-language voices, pronunciation dictionaries, audio caching, model warm-up, the settings TUI, and agent integration. Use when speech is missing, a backend or voice needs setup, playback starts slowly, or the user wants to change settings.
---

# Configuring `local-tts`

Use this when speech does not work yet, sounds wrong, or needs a new language. For simply
speaking text that already works, use the `local-tts-speak` skill.

## Always start here

```bash
tts check
```

It prints the config file path, the default backend, one line per backend with `[ok]` or
`[--]` plus the reason, and the detected audio players. **Read it before changing
anything** — it usually names the exact problem.

`[--]` on a backend the user does not use is fine. Only the backend they need must be
`[ok]`.

## Rules

- **Ask before installing anything, downloading a voice (~60 MB each), or running `sudo`.**
- **A request to install a provider is a request to follow this skill.** "Install kokoro",
  "add rvc", "set up piper" — go to that backend's section below and use those steps
  verbatim (its own venv, the exact config keys, the verification command). They exist
  because each backend has a specific trap: piper needs a separate venv or it drags
  onnxruntime into local-tts, kokoro needs a wrapper script, rvc needs a trained model
  the user must already have. Improvising an install is how those get missed.
- **kokoro and rvc are meant to run as a persistent server. Recommend it, every time.**
  Not a neutral option you mention: it is the difference between ~1s and several seconds
  per call, and IPA phonetics and `emphasis_lengthen` do not work without it. Still an
  **ask** — it leaves a background process running — but ask with a recommendation. See
  "Recommend the server, then ask".
- Show `tts check` output to the user rather than paraphrasing it.
- Never edit the config JSON by hand; use `tts config --set` so validation applies.
- If `tts` itself is missing, that is a full install: follow `AGENT_INSTALL.md` in the
  local-tts repository, not this skill.

## The six backends

| Backend | Offline | Languages | Needs |
| --- | --- | --- | --- |
| `llamacpp` | yes | **English, Chinese, Japanese, Korean only** | `llama-tts` binary |
| `piper` | yes | ~40 languages, fast | `piper` binary + a `.onnx` voice |
| `kokoro` (default) | yes | 8 languages, small model | a `kokoro-tts` wrapper (set up below) |
| `openai` | no | whatever the endpoint offers | a URL, and a key only for api.openai.com |
| `rvc` | yes | inherits its base provider's | **not installed automatically** — see below |
| `command` | yes | whatever the tool offers | any binary that writes a WAV |

**The single most common problem:** the user's text is not in one of llamacpp's four
languages, so it is pronounced with English phonetics. The fix is a backend that speaks
it -- kokoro (the default) or piper -- not a setting.

**If the user has an existing `command.template`** wired to something that now has a real
provider above (kokoro, rvc), run `tts config --detect-migrations` and offer to switch —
full workflow in the `local-tts-update` skill, since that's checked on every update too.

## Adding a language with piper

Check whether piper exists first:

```bash
tts check | grep piper
```

If it is missing, install it **in its own virtualenv** — piper pulls in onnxruntime and
numpy (~200 MB), and local-tts is deliberately dependency-free.

**Linux / macOS:**

```bash
python3 -m venv ~/.local/share/piper-venv
~/.local/share/piper-venv/bin/pip install piper-tts
PIPER=~/.local/share/piper-venv/bin/piper
VOICES=~/.local/share/piper-voices
```

**Windows (PowerShell):**

```powershell
py -m venv $env:LOCALAPPDATA\piper-venv
& "$env:LOCALAPPDATA\piper-venv\Scripts\pip.exe" install piper-tts
$PIPER = "$env:LOCALAPPDATA\piper-venv\Scripts\piper.exe"
$VOICES = "$env:LOCALAPPDATA\piper-voices"
```

Note the differences on Windows: the launcher is `py` (or `python`, never `python3`), the
scripts live in `Scripts\` rather than `bin/`, and executables end in `.exe`.

Then pick a voice. **Ask the user which language and region** — accent matters to people:

```bash
# Linux / macOS — list every voice, then fetch one
~/.local/share/piper-venv/bin/python -m piper.download_voices
mkdir -p ~/.local/share/piper-voices && cd ~/.local/share/piper-voices
~/.local/share/piper-venv/bin/python -m piper.download_voices es_MX-claude-high
```

```powershell
# Windows
& "$env:LOCALAPPDATA\piper-venv\Scripts\python.exe" -m piper.download_voices
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\piper-voices" | Out-Null
Set-Location "$env:LOCALAPPDATA\piper-voices"
& "$env:LOCALAPPDATA\piper-venv\Scripts\python.exe" -m piper.download_voices es_MX-claude-high
```

Voice names read `<lang>_<REGION>-<speaker>-<quality>`, quality being
`x_low | low | medium | high`. Prefer the region matching the user's audience — `es_MX` for
Latin America, `es_ES` for Spain, `pt_BR` vs `pt_PT`, and so on. Voices come from
`rhasspy/piper-voices` on Hugging Face: public, MIT/CC, **no API token or account**.

Register it and record the language preference:

```bash
# Linux / macOS
tts config --set piper.binary=~/.local/share/piper-venv/bin/piper
tts config --set piper.model=~/.local/share/piper-voices/es_MX-claude-high.onnx
tts languages --set es=piper:~/.local/share/piper-voices/es_MX-claude-high.onnx
tts --lang es "Prueba de voz."      # verify it out loud
```

```powershell
# Windows — pass full paths; ~ is not expanded by PowerShell
tts config --set "piper.binary=$env:LOCALAPPDATA\piper-venv\Scripts\piper.exe"
tts config --set "piper.model=$env:LOCALAPPDATA\piper-voices\es_MX-claude-high.onnx"
tts languages --set "es=piper:$env:LOCALAPPDATA\piper-voices\es_MX-claude-high.onnx"
tts --lang es "Prueba de voz."
```

Recording the language is not optional bookkeeping — it is how every agent knows what to
use next time.

## Adding Kokoro (small, fast, offline, many languages)

Kokoro-82M is a comparable alternative to piper — similar footprint, similar language
coverage. Reach for it when the user specifically wants Kokoro, or when piper's available
voices don't cover what they need.

There is no single official CLI to install; the simplest reliable path is a minimal
wrapper around the `kokoro`/`kokoro-onnx` Python package, kept in its own venv the same
way piper is:

```bash
python3 -m venv ~/.local/share/kokoro-venv
~/.local/share/kokoro-venv/bin/pip install kokoro-onnx soundfile

mkdir -p ~/.local/share/kokoro-models && cd ~/.local/share/kokoro-models
# fetch kokoro-v1.0.onnx and voices-v1.0.bin -- ask the user before downloading;
# they're published at https://github.com/nazdridoy/kokoro-tts/releases
```

Write the wrapper script (`~/.local/share/kokoro-venv/kokoro_cli.py`):

```python
#!/usr/bin/env python3
import argparse, os, sys, warnings
warnings.filterwarnings("ignore")
MODELS = os.path.expanduser("~/.local/share/kokoro-models")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output", required=True)
    p.add_argument("-v", "--voice", default="af_heart")
    p.add_argument("-l", "--lang", default="en-us")
    p.add_argument("-s", "--speed", type=float, default=1.0)
    p.add_argument("text", nargs="*")
    a = p.parse_args()
    text = " ".join(a.text).strip() or sys.stdin.read().strip()
    if not text:
        sys.exit("kokoro-tts: no text")
    import soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro(f"{MODELS}/kokoro-v1.0.onnx", f"{MODELS}/voices-v1.0.bin")
    samples, rate = k.create(text, voice=a.voice, speed=a.speed, lang=a.lang)
    sf.write(a.output, samples, rate)

if __name__ == "__main__":
    main()
```

And a thin shell shim on PATH so `kokoro.binary`'s default (`kokoro-tts`) finds it:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/kokoro-tts <<'EOF'
#!/usr/bin/env bash
exec "$HOME/.local/share/kokoro-venv/bin/python" \
     "$HOME/.local/share/kokoro-venv/kokoro_cli.py" "$@"
EOF
chmod +x ~/.local/bin/kokoro-tts
```

**Offer a second language, don't assume one.** Language tags (below) can only name
languages that are configured, and borrowed English words inside another language are
common — so it is worth asking whether they want English set up alongside their own
language. Kokoro holds one model for every language, so the extra entry costs nothing at
runtime. Ask; don't add it unprompted.

Ask which voice and language the user wants — voice IDs are per-language (e.g. `af_heart`
for US English, `ef_dora` for Spanish; the underlying model card lists the full set) —
then register and record it the same way as piper:

```bash
tts config --set kokoro.voice=ef_dora
tts config --set kokoro.lang=es
tts languages --set es=kokoro
tts --lang es "Prueba de voz con Kokoro."
```

`kokoro.model_dir` is a separate, optional setting for a *different* kind of kokoro CLI
(one that resolves its model files from its own working directory rather than managing
them internally, like this wrapper does) — leave it unset for the setup above.

### Recommended: keep the model loaded (a persistent server)

**This is how kokoro is meant to run.** The CLI wrapper above reloads the whole model from
disk on every single call, and that load — not the synthesis — is most of the wait. It is
also where the phonemizer lives, so `emphasis_lengthen` and IPA pronunciation entries do
nothing without it: they are not "slower" off the server, they are absent.

So treat this as the second half of installing kokoro, not an extra. **Still ask** — it
leaves a background process running — but ask with a recommendation, and say what they
lose by declining:

> The script below is a **copy**, not a link into the package: once written, nothing
> updates it on its own. After any `local-tts` update, `tts servers` says whether it still
> matches this version and `tts servers --refresh` rewrites it (keeping the old one as
> `.bak`) and stops the running server so the new one takes over. If you are writing this
> file by hand today, you never need to diff it yourself again.

```bash
cat > ~/.local/share/kokoro-venv/kokoro_server.py <<'EOF'
#!/usr/bin/env python3
"""Persistent Kokoro server: loads the model once, serves POST /synthesize over HTTP.
Talked to by local-tts's kokoro provider when kokoro.server_url is set. Exits on its own
after --idle-timeout seconds with no synthesis request, to release the loaded model; 0
disables that."""
import argparse
import io
import json
import os
import sys
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

warnings.filterwarnings("ignore")

MODELS = os.path.expanduser("~/.local/share/kokoro-models")

#: Vowels that can carry stress across the languages kokoro speaks. Used to find the
#: vowel a primary-stress mark applies to, so a length mark lands on the vowel itself
#: rather than on the consonant in front of it.
VOWELS = set("aeiouɑɐɒæɛɜɪiɔoʊuʌyøœɵɤɯəɨʉ")
_BACKENDS = {}


def phonemes(text, lang):
    """IPA for `text`, with stress marks.

    Uses kokoro-onnx's own Tokenizer rather than reaching around it to phonemizer.
    Three reasons, in order of how much they bite:

    - It is what the model itself uses, so a transcription made here tokenizes to
      exactly what `create(text)` would have produced -- verified: same tokens, and the
      audio differs only by the runtime's own float noise.
    - It is not an extra dependency. kokoro-onnx already requires phonemizer and
      espeakng-loader; importing them directly only looked free because they were
      already installed for it.
    - Raw EspeakBackend with preserve_punctuation keeps characters the model has no
      token for -- Spanish "¿" among them -- which are then silently dropped.

    Built once per language: it loads espeak's data, which is not something to redo per
    request.
    """
    if lang not in _BACKENDS:
        from kokoro_onnx.tokenizer import Tokenizer
        _BACKENDS[lang] = Tokenizer()
    return _BACKENDS[lang].phonemize(text.strip(), lang=lang).strip()


def lengthen_stressed(ipa, marks):
    """Append `marks` IPA length marks to the vowel carrying primary stress in each word.

    This is emphasis the way a phonetician writes it: kˈasa -> kˈaːsa. Kokoro has the
    length mark in its own vocabulary, so the model hears it rather than skipping it --
    an isolated word measures 0.576s plain, 0.640s with one mark, 0.661s with two.
    """
    if marks <= 0:
        return ipa
    out, index = [], 0
    while index < len(ipa):
        char = ipa[index]
        out.append(char)
        index += 1
        if char != "ˈ":                      # primary stress only, not secondary
            continue
        while index < len(ipa) and ipa[index] not in VOWELS:
            out.append(ipa[index]); index += 1
        while index < len(ipa) and ipa[index] in VOWELS:
            out.append(ipa[index]); index += 1
        out.append("ː" * marks)
    return "".join(out)


#: Entries already reported, so a warning is printed once and not per call.
_WARNED = set()


def _warn_unsayable(table, lang):
    """Say out loud when an entry asks for phonemes this model has no token for.

    They are dropped silently otherwise, which is the worst failure to debug: the word
    still comes out, just mangled, and nothing anywhere says why. Usually a typo -- an
    ASCII "r" where IPA wants "ɹ", or a stress mark copied as an apostrophe.
    """
    try:
        vocab = set(_BACKENDS.setdefault(lang, None).vocab) if _BACKENDS.get(lang) else None
        if vocab is None:
            phonemes("a", lang)                 # builds the tokenizer for this language
            vocab = set(_BACKENDS[lang].vocab)
    except Exception:
        return
    for word, ipa in table.items():
        missing = sorted({c for c in ipa if c not in vocab and not c.isspace()})
        if missing and (word, lang) not in _WARNED:
            _WARNED.add((word, lang))
            print("phonetics: %r asks for %s, which this model has no token for -- "
                  "dropped" % (word, ", ".join(repr(c) for c in missing)),
                  file=sys.stderr, flush=True)


def substitute_phonetics(text, lang, table):
    """Transcribe the whole line, then swap in the dictionary's IPA for the words it
    names.

    This is how a borrowed word keeps its own sound without the sentence being cut into
    pieces. A word synthesized on its own gets its own end-of-sentence fall, and dropped
    mid-sentence that reads as an interruption.

    The line is transcribed WHOLE, exactly as Kokoro._prepare does, and only then are
    phonemes swapped. Transcribing the fragments around each word instead looks
    equivalent and is not: espeak assigns stress per utterance, so every fragment gets
    its own citation-form stress and each boundary invents a stressed word.

        whole line   ʝˈa suβˈi  el  pˈuʎ rekˈest al rˌepositˈoɾjo.
        fragments    ʝˈa suβˈi ˈel  pˈʊl ɹᵻkwˈɛst al rˌepositˈoɾjo.
                               ^^^ the article should not carry stress

    Where a word lands in the transcription is found by transcribing the line a second
    time with that word replaced by a nonsense placeholder: everything before and after
    it comes out identical, so the region that differs is the word. Spans are measured
    against the same baseline, so several words do not disturb each other, and they are
    applied right to left so earlier offsets stay valid.

    Any language works -- IPA is not tied to one -- but the model can only say the
    phonemes its own vocabulary contains, so a sound it was never trained on comes out
    as the nearest thing it has.
    """
    import re as _re
    if not table:
        return text, False

    #: Nonsense, unlikely to appear, and pronounceable in every language espeak knows,
    #: so its presence does not disturb the stress of what surrounds it.
    marker = "Kalakala"

    baseline = None
    spans = []
    occupied = []
    for word in sorted(table, key=len, reverse=True):
        # A hyphen counts as part of the word: "pre-build" is not "build", and matching
        # inside it used to snap the span out to the surrounding spaces and swallow the
        # prefix -- the audio lost "pre" entirely.
        pattern = _re.compile(r"(?<![\w-])%s(?![\w-])" % _re.escape(word), _re.IGNORECASE)
        found = list(pattern.finditer(text))
        for occurrence, hit in enumerate(found):
            # The longest matching phrase owns its words. Otherwise entries for
            # both "pull request" and "request" overwrite overlapping phoneme
            # offsets and corrupt the rest of the sentence.
            if any(hit.start() < end and hit.end() > start for start, end in occupied):
                continue
            if baseline is None:
                baseline = phonemes(text, lang)
            _warn_unsayable({word: table[word]}, lang)
            # One placeholder run per occurrence: replacing them all at once would leave
            # a single differing region covering everything between the first and last,
            # and replacing only the first left every later one in the host language.
            marked_text = text[:hit.start()] + marker + text[hit.end():]
            marked = phonemes(marked_text, lang)
            head = 0
            while head < min(len(baseline), len(marked)) and baseline[head] == marked[head]:
                head += 1
            tail = 0
            while (tail < min(len(baseline), len(marked)) - head
                   and baseline[-1 - tail] == marked[-1 - tail]):
                tail += 1
            start, end = head, len(baseline) - tail
            # Snap outwards to whitespace. The scan stops at the first character that
            # differs, so a placeholder sharing a leading letter with the word
            # ("Kalakala" against "klustˈeɾ") leaves it behind and the swap produces
            # "kklˈʌstɚ". Phonemes are space separated, so the gaps are the real edges.
            while start > 0 and not baseline[start - 1].isspace():
                start -= 1
            while end < len(baseline) and not baseline[end].isspace():
                end += 1
            # A span that is empty, or that swallowed most of the line, means the
            # second transcription diverged for another reason. Skipping is the safe
            # answer: the word is still said, just this language's way.
            isolated = not _re.search(r"\w", text[:hit.start()] + text[hit.end():])
            if start >= end or (not isolated and (end - start) > len(baseline) * 0.6):
                continue
            if any(start < old_end and end > old_start for old_start, old_end, _ in spans):
                continue
            # Punctuation rides along with the word it follows ("backend?" has no
            # space before the mark) and the snap would swallow it. Losing it costs the
            # sentence its intonation, so it is put back after the transcription.
            carried = ""
            while (carried != baseline[start:end]
                   and baseline[end - 1 - len(carried)] in ".,;:!?…"):
                carried = baseline[end - 1 - len(carried)] + carried
            spans.append((start, end, table[word] + carried))
            occupied.append((hit.start(), hit.end()))

    if not spans:
        return text, False

    out = baseline
    for start, end, ipa in sorted(spans, reverse=True):
        out = out[:start] + ipa + out[end:]
    return out, True


def runtime_cache_state():
    # Capture identities once, when the models have just loaded, not per health probe.
    assets = {}
    model_roots = [MODELS] if "MODELS" in globals() else []
    for argument in [sys.argv[0]] + sys.argv[1:] + model_roots:
        path = os.path.expanduser(argument.split("=", 1)[-1])
        candidates = [path]
        if os.path.isdir(path):
            candidates += [os.path.join(path, name) for name in
                           ("kokoro-v1.0.onnx", "voices-v1.0.bin")]
        if path.endswith(".onnx"):
            candidates.append(path + ".json")
        for candidate in candidates:
            if os.path.isfile(candidate):
                st = os.stat(candidate)
                assets[os.path.realpath(candidate)] = [st.st_size, st.st_mtime_ns]
    return {"argv": sys.argv[1:], "script": os.path.realpath(sys.argv[0]), "assets": assets}


def make_handler(kokoro, last_activity):
    cache_state = runtime_cache_state()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            # A health check is not "activity" -- it must not keep the process alive
            # forever just because something is polling it.
            if self.path == "/health":
                # JSON, not "ok": local-tts asks this endpoint what the server can do,
                # so that `tts check` can tell a server that understands the
                # pronunciation dictionary's IPA entries from an older copy that
                # would accept them and drop them without a word.
                body = json.dumps({"ok": True, "phonetics": True, "shutdown": True,
                                   "vocab": True, "cache_state": cache_state}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/vocab":
                # Which phonemes this model has a token for. A transcription asking for
                # one it does not have loses that character silently -- the word still
                # comes out, just mangled -- so `tts pronounce` reads this and says which
                # character is the problem instead of leaving it to be heard.
                try:
                    phonemes("a", "en-us")          # builds the tokenizer if it is not yet
                    vocab = "".join(sorted(_BACKENDS["en-us"].vocab))
                    body = json.dumps({"vocab": vocab}).encode("utf-8")
                except Exception as exc:            # an older kokoro-onnx, or no espeak data
                    body = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/keepalive":
                last_activity[0] = time.time()
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/shutdown":
                # `tts servers --refresh` rewrites this file, but the process already
                # running is still the old code -- and it would keep answering for up
                # to a whole idle timeout. Exiting hands the port back; the next call
                # auto-starts the new file.
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.wfile.flush()
                threading.Thread(target=_exit_soon, daemon=True).start()
                return
            if self.path != "/synthesize":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            text = (body.get("text") or "").strip()
            if not text:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"no text")
                return

            import soundfile as sf
            lang = body.get("lang") or "en-us"
            spoken, is_phonemes = text, False
            marks = int(body.get("emphasis_lengthen") or 0)
            table = {k.lower(): v for k, v in (body.get("phonetics") or {}).items()}
            try:
                if table:
                    spoken, is_phonemes = substitute_phonetics(text, lang, table)
                if marks > 0:
                    source = spoken if is_phonemes else phonemes(text, lang)
                    spoken, is_phonemes = lengthen_stressed(source, marks), True
            except Exception as exc:          # never lose the audio over a nicety
                print("phonetics skipped (%s: %s)" % (type(exc).__name__, exc),
                      file=sys.stderr, flush=True)
                spoken, is_phonemes = text, False

            kwargs = {}
            for key in ("sentence_pause", "clause_pause"):
                if body.get(key) is not None:
                    kwargs[key] = float(body[key])
            try:
                samples, rate = kokoro.create(
                    spoken,
                    voice=body.get("voice") or "af_heart",
                    speed=float(body.get("speed") or 1.0),
                    lang=lang,
                    is_phonemes=is_phonemes,
                    **kwargs
                )
            except Exception as exc:
                # Answer with an error instead of dying: an unknown voice used to take
                # the whole server down mid-request, which reads as a network failure.
                print("synthesis failed (%s: %s)" % (type(exc).__name__, exc),
                      file=sys.stderr, flush=True)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(exc).encode("utf-8", "replace")[:500])
                return
            buf = io.BytesIO()
            sf.write(buf, samples, rate, format="WAV")
            last_activity[0] = time.time()

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            self.wfile.write(buf.getvalue())

    return Handler


def _exit_soon():
    """Let the 200 reach the client first, then go. os._exit for the same reason
    watch_idle uses it: a loaded model has threads that will not unwind politely."""
    time.sleep(0.2)
    print("shutdown requested, exiting", file=sys.stderr)
    os._exit(0)


def watch_idle(last_activity, idle_timeout):
    if idle_timeout <= 0:
        return
    while True:
        time.sleep(10)
        if time.time() - last_activity[0] > idle_timeout:
            print("idle for %ds, exiting to release the model" % idle_timeout, file=sys.stderr)
            os._exit(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--idle-timeout", type=int, default=300,
                        help="exit after this many idle seconds; 0 disables")
    args = parser.parse_args()
    from kokoro_onnx import Kokoro
    print("loading model...", file=sys.stderr)
    kokoro = Kokoro("%s/kokoro-v1.0.onnx" % MODELS, "%s/voices-v1.0.bin" % MODELS)
    last_activity = [time.time()]
    threading.Thread(target=watch_idle, args=(last_activity, args.idle_timeout), daemon=True).start()
    print("ready on port %d (idle timeout %ds)" % (args.port, args.idle_timeout), file=sys.stderr)
    HTTPServer(("127.0.0.1", args.port), make_handler(kokoro, last_activity)).serve_forever()


if __name__ == "__main__":
    main()
EOF

tts config --set kokoro.server_url=http://127.0.0.1:8765
tts config --set 'kokoro.server_start=~/.local/share/kokoro-venv/bin/python ~/.local/share/kokoro-venv/kokoro_server.py --port 8765'
tts -p kokoro "Prueba con el servidor."   # auto-starts it (a few seconds, model load),
                                          # every call after that is fast
```

Voice, language and speed still travel **per call** — the server holding the model
resident doesn't fix them to whatever it started with. **It exits on its own after 5
minutes idle** (`--idle-timeout`, seconds — append e.g. `--idle-timeout 600` to
`kokoro.server_start` for a different value, or `--idle-timeout 0` to disable it and keep
it running indefinitely); the next call after it exits just auto-starts a fresh one, paying
the model-load cost again. Checked in ~10s increments, so actual shutdown can lag the
configured timeout by up to that much — irrelevant at the 5-minute default. A health check
(what auto-start's own readiness poll does) does not count as activity and never keeps it
alive on its own. `tts check` reports whether it's already running, or will auto-start on
first use, without starting it itself (a check
should never have the side effect of spinning up a background process). Nothing here is
run or managed by this tool beyond talking to it and auto-starting it — killing the
process is how you stop it; the next call starts a fresh one.

## Adding RVC (voice conversion — not installed automatically)

RVC (retrieval-based voice conversion) does **not** do text-to-speech. `rvc-python`
converts an existing audio file to a target voice; it has no text input at all. The `rvc`
provider handles this by chaining: it synthesizes with another provider first (piper by
default, or whatever `rvc.base_provider` names), then converts that result to the target
voice. This is for a specific, deliberate ask — "make it sound like this voice" with a
trained `.pth` model in hand — not a general voice-quality upgrade; point most requests at
piper or kokoro instead.

**Always ask before installing.** rvc-python pulls in torch and is sizable — never install
it as a side effect of a routine request.

```bash
python3 -m venv ~/.local/share/rvc-venv
~/.local/share/rvc-venv/bin/pip install rvc-python
# GPU support needs a matching torch build -- see https://github.com/daswer123/rvc-python
# for the exact index URL; ask the user whether they have a GPU before adding it.
```

The user needs an actual trained voice model — a `.pth` file, plus an optional `.index`
file that improves quality — from wherever they trained or downloaded one (out of scope
here; this skill only wires up whatever they already have).

```bash
tts config --set rvc.python=~/.local/share/rvc-venv/bin/python
tts config --set rvc.model=~/.local/share/rvc-models/<name>/<name>.pth
tts config --set rvc.index=~/.local/share/rvc-models/<name>/<index-file>     # optional
tts config --set rvc.base_provider=piper     # or kokoro/llamacpp -- whichever gives the
                                              # best base voice to convert from
tts -p rvc --dry-run "test"    # shows both steps: base synthesis, then conversion
tts -p rvc "Test of the converted voice."
```

`rvc.device` defaults to `cpu`; set it to `cuda:0` only if the venv above actually has a
CUDA-enabled torch installed. There is no `rvc.voice` — voice comes entirely from which
`.pth` model is configured, not a per-call flag.

### Recommended: keep the models loaded (a persistent server)

**This is how rvc is meant to run** — more so than kokoro, because the per-call load
includes importing torch. That load is the slow part of every call; a persistent server
pays it once, and it can hold **several voices resident at the same time**, picking one
per request. That is what makes a second language cheap: one server, one copy of torch,
one GPU context, N voices.

Running rvc per call is a fallback for a machine that cannot spare the RAM, not the normal
setup. **Still ask before starting it** — see "Recommend the server, then ask" below for
how to put it to the user.

```bash
cat > ~/.local/share/rvc-venv/rvc_server.py <<'EOF'
#!/usr/bin/env python3
"""Multi-voice RVC conversion server.

Holds one RVCInference per --model NAME=PATH pair, all resident, and picks one per
request from the JSON body's "model" key. Requests that name nothing get the first
model, so a single-model setup behaves exactly as it always did.
"""
import argparse, json, math, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

def runtime_cache_state():
    # Capture identities once, when the models have just loaded, not per health probe.
    assets = {}
    model_roots = [MODELS] if "MODELS" in globals() else []
    for argument in [sys.argv[0]] + sys.argv[1:] + model_roots:
        path = os.path.expanduser(argument.split("=", 1)[-1])
        candidates = [path]
        if os.path.isdir(path):
            candidates += [os.path.join(path, name) for name in
                           ("kokoro-v1.0.onnx", "voices-v1.0.bin")]
        if path.endswith(".onnx"):
            candidates.append(path + ".json")
        for candidate in candidates:
            if os.path.isfile(candidate):
                st = os.stat(candidate)
                assets[os.path.realpath(candidate)] = [st.st_size, st.st_mtime_ns]
    return {"argv": sys.argv[1:], "script": os.path.realpath(sys.argv[0]), "assets": assets}


def make_handler(models, default_name, last_activity, lock):
    cache_state = runtime_cache_state()
    defaults = {name: {key: getattr(rvc, key) for key in
                ("f0up_key", "index_rate", "protect")} for name, rvc in models.items()}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Neither of these counts as activity: polling must not keep a GPU
            # model resident forever.
            if self.path == "/health":
                # JSON rather than a bare "ok": a client reads this to tell a current
                # server from an older copy that would ignore what it cannot parse.
                self._json(200, {"ok": True, "shutdown": True, "conversion_parameters": True, "cache_state": cache_state})
            elif self.path == "/models":
                self._json(200, {"models": sorted(models), "default": default_name})
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self):
            if self.path == "/keepalive":
                last_activity[0] = time.time()
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/shutdown":
                # See kokoro's server: a refreshed script only takes over once the
                # process running the old one lets go of the port.
                self.send_response(200); self.send_header("Content-Length", "0")
                self.end_headers(); self.wfile.flush()
                threading.Thread(target=_exit_soon, daemon=True).start()
                return
            if self.path != "/convert":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self.send_response(400); self.end_headers(); return

            if not isinstance(body, dict):
                self._json(400, {"error": "expected a JSON object"}); return
            overrides = {}
            try:
                for key, target, lower, upper in (("pitch", "f0up_key", -24, 24),
                        ("index_rate", "index_rate", 0, 1), ("protect", "protect", 0, 0.5)):
                    if key in body:
                        value = float(body[key])
                        if not math.isfinite(value) or not lower <= value <= upper:
                            raise ValueError("%s must be between %s and %s" % (key, lower, upper))
                        if key == "pitch" and not value.is_integer():
                            raise ValueError("pitch must be an integer")
                        overrides[target] = int(value) if key == "pitch" else value
            except (TypeError, ValueError) as exc:
                self._json(400, {"error": str(exc)}); return
            name = body.get("model") or default_name
            if name not in models:
                self._json(404, {"error": "no such model %r" % name,
                                 "available": sorted(models)})
                return
            input_path = body.get("input_path") or ""
            if not input_path or not os.path.exists(input_path):
                self.send_response(400); self.end_headers()
                self.wfile.write(b"input_path missing or does not exist"); return

            rvc = models[name]
            descriptor, out_path = tempfile.mkstemp(prefix="local-tts-rvc-", suffix=".wav")
            os.close(descriptor)
            try:
                # Reset every request to the model's startup values, then apply only
                # this request's overrides. Always restore, including on inference errors.
                with lock:
                    try:
                        rvc.set_params(**dict(defaults[name], **overrides))
                        rvc.infer_file(input_path, out_path)
                    finally:
                        rvc.set_params(**defaults[name])
                with open(out_path, "rb") as fh:
                    data = fh.read()
                if not data:
                    raise RuntimeError("conversion produced no audio")
            except Exception as exc:
                # Cleanup must finish before the client receives the failure.
                if os.path.exists(out_path):
                    os.unlink(out_path)
                self._json(500, {"error": str(exc)}); return
            finally:
                if os.path.exists(out_path):
                    os.unlink(out_path)
                last_activity[0] = time.time()

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler

def _exit_soon():
    """Let the 200 reach the client first, then go -- torch will not unwind politely."""
    time.sleep(0.2)
    print("shutdown requested, exiting", file=sys.stderr)
    os._exit(0)


def watch_idle(last_activity, idle_timeout):
    if idle_timeout <= 0:
        return
    while True:
        time.sleep(10)
        if time.time() - last_activity[0] > idle_timeout:
            print("idle for %ds, exiting to release the models" % idle_timeout, file=sys.stderr)
            os._exit(0)

def split_pair(raw, flag):
    """NAME=PATH -> (name, path). A bare PATH (no '=') becomes the 'default' voice, so
    the old single-model command line keeps working unchanged."""
    if "=" in raw:
        name, path = raw.split("=", 1)
        name = name.strip()
        if not name:
            sys.exit("%s: empty name in %r" % (flag, raw))
        return name, os.path.expanduser(path.strip())
    return "default", os.path.expanduser(raw.strip())

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--model", action="append", required=True, metavar="NAME=PATH",
                   help="repeatable; a bare path is registered as 'default'")
    p.add_argument("--index", action="append", default=[], metavar="NAME=PATH",
                   help="repeatable; NAME must match a --model name")
    p.add_argument("--device", default="cpu")
    p.add_argument("--index-rate", type=float, default=None)
    p.add_argument("--protect", type=float, default=None)
    p.add_argument("--f0method", default=None)
    p.add_argument("--pitch", type=int, default=None)
    p.add_argument("--idle-timeout", type=int, default=300,
                   help="exit after this many idle seconds; 0 disables")
    args = p.parse_args()

    model_paths = dict(split_pair(m, "--model") for m in args.model)
    index_paths = dict(split_pair(i, "--index") for i in args.index)
    unknown = set(index_paths) - set(model_paths)
    if unknown:
        sys.exit("--index names with no matching --model: %s" % ", ".join(sorted(unknown)))

    from rvc_python.infer import RVCInference
    models, order = {}, list(model_paths)
    for name in order:
        print("loading %s..." % name, flush=True)
        rvc = RVCInference(device=args.device)
        rvc.load_model(model_paths[name], index_path=index_paths.get(name, ""))
        startup = {k: v for k, v in (
            ("index_rate", args.index_rate), ("protect", args.protect),
            ("f0method", args.f0method), ("f0up_key", args.pitch),
        ) if v is not None}
        if startup:
            rvc.set_params(**startup)
        models[name] = rvc

    default_name = order[0]
    last_activity, lock = [time.time()], threading.Lock()
    threading.Thread(target=watch_idle, args=(last_activity, args.idle_timeout),
                     daemon=True).start()
    print("ready on port %d | voices: %s | default: %s | idle timeout %ds"
          % (args.port, ", ".join(order), default_name, args.idle_timeout), flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port),
                        make_handler(models, default_name, last_activity, lock)).serve_forever()

if __name__ == "__main__":
    main()
EOF
```

Register the server and tell local-tts which voice belongs to which language:

```bash
M=~/.local/share/rvc-models
tts config --set rvc.server_url=http://127.0.0.1:8766
tts config --set "rvc.server_start=~/.local/share/rvc-venv/bin/python ~/.local/share/rvc-venv/rvc_server.py --port 8766 --device cuda:0 --idle-timeout 300 --index-rate 0.88 --protect 0.20 --f0method rmvpe --model jarvis=$M/jarvis/jarvis.pth --index jarvis=$M/jarvis/jarvis.index --model cortana-es=$M/cortana-es/model.pth --index cortana-es=$M/cortana-es/model.index"

# which resident voice each language uses
tts config --set rvc.language_models.es=cortana-es
tts config --set rvc.language_models.en=jarvis
tts config --set rvc.server_model=jarvis          # fallback when the call has no --lang

tts --lang es "Prueba de voz."   # auto-starts the server, asks it for cortana-es
tts --lang en "Voice test."      # same server, same torch, jarvis this time
```

**Startup flags establish defaults; current servers also accept per-request tuning.**
`rvc.index_rate` and `rvc.protect` apply to both server and CLI conversion. A language
can override them with `rvc.conversion.es={"index_rate":0.65,"protect":0.2}`. Supported
keys are `index_rate` (0–1), `protect` (0–0.5), and `pitch` (integer semitones, −24–24).
`rvc.method` remains a CLI setting; use `--f0method` for the server. An old server that
cannot honor per-request tuning reports an error instead of silently ignoring it;
run `tts servers --refresh`. Each request restores startup values afterward, including
on failure, so an experiment cannot affect another language or later call. A scoped
`"pitch":0` explicitly requests no shift; an unset pitch preserves the startup value.

**Which voices exist is still fixed at startup** — adding one means restarting the server
with another `--model name=path` pair. What is *not* fixed any more is which of them a
given call uses. `tts check` lists the resident voices and the language mapping without
starting anything.

**RVC transfers timbre, not pitch.** The base provider's pitch contour survives conversion
unchanged, so a voice can come out recognisably "wrong" even with the right model loaded.
Use `--pitch -2` (semitones) on the server, and pick a base voice already close to the
target, before concluding the model is bad.

Same idle behavior as kokoro's server: **exits after 5 minutes with no conversion
request** (`--idle-timeout`, seconds — `0` disables). Worth knowing here specifically
because torch models held resident use real memory (and VRAM on `cuda:0`) the whole time,
and with several voices loaded that is now N models, not one.

### Recommend the server, then ask

Whenever you set up or repair `kokoro` or `rvc`, **recommend server mode and then ask** —
recommend, because per-call loading is the wrong way to run either of them; ask, because
starting a background process on someone's machine is their call, never yours.

Lead with the win, name the cost, and let them decide:

- **Faster:** the model and torch load once, not per call. On a warm server a sentence
  comes back in about a second; cold, the same call pays several seconds of load every
  single time.
- **Phonetics only exist here:** `kokoro.emphasis_lengthen` and IPA entries in the
  pronunciation dictionary need the phonemizer, which lives in the server. Off the server
  they are silently ignored — mention this explicitly if they have any IPA entries or have
  asked about pronunciation.
- **One server, many voices:** several languages share one process and one GPU context.
- **The cost:** a background process holding RAM (and VRAM) while it lives, released
  automatically after the idle timeout and restarted transparently on the next call.

If they say yes, write the script, put the conversion flags on the command line, set
`language_models`, and verify with `tts check`. If they say no, that is a fine answer:
leave `server_url` empty, the per-call CLI path keeps working — just say once that speech
will be several seconds slower per call and IPA entries will not apply, so it is not a
surprise later. Never start a background process on a machine without asking first.

## The language memory

```bash
tts languages                                   # what is remembered
tts languages --set es=piper:/path/voice.onnx    # record
tts languages --set en=llamacpp                  # provider only
tts languages --forget de                        # drop one
```

Lookups match the specific tag before the base one, so `es-MX` wins over `es` when both
exist. Update this whenever the user expresses a preference, and confirm what you recorded.

## The speaker icon in the terminal title

While audio plays, local-tts sets the terminal's tab/window title to `🔊 0:12 <file>`, and
restores it the moment playback ends, is stopped, or the process dies. It is handled by
the same background runner that owns the playback, so it clears itself without the agent
having to remember — including when the user runs `tts stop`.

Nothing needs configuring. It is skipped automatically when there is no terminal to write
to (output piped into another tool, a status-line hook, CI), and `terminal_title=false`
turns it off for a user who keeps their own title. If a user reports a stuck 🔊 in a tab,
`tts stop` clears it; a title left over from a killed terminal cannot outlive that
terminal.

## Playback control

Audio started with `tts -b` is detached, so it can be controlled afterwards:

```bash
tts playback     # playing / paused / nothing, plus the file
tts stop
tts pause        # POSIX only (Linux, macOS, WSL)
tts resume
```

If the user reports that audio "will not stop", run `tts playback` first — if it reports
nothing, the sound is coming from something else, not this CLI. A stale state file is
harmless: the next `stop` clears it.

Playback is also serialized machine-wide — at most one file plays at a time, regardless of
provider or session, via a lock file `play_detached()` holds for the duration of playback
(see `audio.playback_lock_path()`, `localtts/_playback_runner.py`). If the user reports
"nothing plays" or "it's stuck at 0:00", check whether *another session* already has audio
running (`tts playback --session <other-id>` if known, or just ask) — a queued session
correctly shows frozen `0:00 / <total>` until its turn comes, that is not a bug. It starts
advancing once the earlier one finishes.

## Status-bar hook (progress in the host's own UI, not chat)

`local-tts-speak` prints a status line for each thing it plays. If the user finds that
noisy and asks for progress somewhere else — "can this show in my status bar instead",
"don't spam the chat" — a real hook exists for two hosts, verified against their own
settings schemas, not assumed:

| Host | Mechanism |
| --- | --- |
| Claude Code | `~/.claude/settings.json` → `statusLine.command`, with a real `refreshInterval` timer |
| Qwen Code | `~/.qwen/settings.json` → `ui.statusLine.command`, same idea |

```bash
tts hooks                    # what's detected, installed, and why the rest can't do this
tts hooks --install          # install into every detected supported host
tts hooks --uninstall        # remove it, restoring whatever status line was there before
```

Everything else — Gemini CLI, Codex CLI, OpenCode, Cursor, Windsurf, GitHub Copilot CLI —
reports a specific reason it isn't supported (`tts hooks` prints it): no such mechanism
exists yet for most of them (open upstream feature requests, not a gap in this tool), and
Cursor/Windsurf would need a full IDE extension rather than a lightweight hook. Don't
promise this to a user on one of those hosts; tell them why it isn't available there yet.

**Install never rewrites `statusLine.command` — it appends into the file it already points
to.** This is load-bearing, not a nicety: an earlier version replaced the pointer with its
own wrapper and saved the old command as a string to restore later, and that broke a real
Boost installation — when Boost's own reinstall regenerated its script, our saved reference
went stale, and because we now owned the slot, Boost's own installer no longer recognized
it as its own and silently stopped rendering. The fix is structural: if a status line is
already configured, we add a small marked block to the *end of that script file* instead,
so whatever tool owns the slot keeps owning it and keeps running exactly as it did. Idle,
output is byte-for-byte what it was before installing. Reinstalling replaces our block in
place, never duplicating it; `--uninstall` removes only our block, leaving everything else
untouched.

This only works when the existing command is a **plain path to a writable script file** —
not a one-liner, not something with arguments, not something unwritable. If it doesn't
qualify, install refuses and prints the exact block to add by hand, or the user can choose
`--force` to fall back to the old replace-and-chain behavior — tell them plainly that this
means the tool that used to own the slot stops running, which is a real cost, not a free
upgrade; don't reach for `--force` as a default when append fails, ask first.

After an append-mode install, no restart is needed — it takes effect on the very next
status-bar refresh, since only the script's own content changed. A fresh install with
nothing configured before (or `--force`) does change `statusLine.command`/`refreshInterval`
directly, and *does* need a restart. Verify with `tts hooks --status`, which prints
`active`/`inactive` — that only flips to `active` once the host has actually called the
hook at least once, so give it a moment right after.

**If the user says the bar "looks frozen"**, that's the default: appending into an existing
status line leaves the refresh cadence exactly as it was, and without a timer configured
that means only redrawing on host events (a new message, a permission-mode change), not a
per-second tick. Offer `--refresh-interval N` — ask what cadence they want rather than
picking one:

```bash
tts hooks --install claude-code --refresh-interval 2   # real timer, ticks live
tts hooks --install claude-code --refresh-interval 0   # explicitly event-based (removes any existing timer)
```

Tell them plainly that this also changes how often the *other* tool's script re-runs, not
just ours — that's real overhead if the existing tool does anything non-trivial per call,
which you have no visibility into. `0` is a deliberate choice (explicitly no timer), not
the same as omitting the flag (leave whatever cadence was already configured). This does
write to settings.json — only the `refreshInterval` key, `command` is still never touched —
so it needs a restart.

**Multiple sessions on the same machine are correctly isolated for Claude Code**, verified
against a live capture. Playback started with `--session` is looked up by that same id when
rendering — for a fresh/`--force` install, from the `session_id` field in the host's JSON
payload; for the (default) appended mode, from `$CLAUDE_CODE_SESSION_ID` in the
environment, since stdin may already be consumed by the script we appended into. Only
`CLAUDE_CODE_SESSION_ID` is verified so far — Qwen Code's shell tool sets no session-id env
var (checked its docs), so session isolation there is unconfirmed; a single Qwen session
still works correctly, it's only concurrent Qwen sessions that aren't proven yet. If a
user reports their status bar showing playback they didn't start, or not showing playback
they did, check `tts hooks --status` and confirm they're on a build where `-b` actually
uses `--session` — that correlation is what makes this work, not anything host-specific.

## Other settings

```bash
tts config --show                       # effective configuration
tts config --path                       # where it lives
tts config --set provider=piper         # default backend
tts config --set play=false             # never auto-play
tts config --set player=ffplay          # force a playback command
tts config --set terminal_title=false   # stop showing 🔊 in the terminal tab title

# llama.cpp performance
tts config --set llamacpp.threads=8
tts config --set llamacpp.gpu_layers=99
tts config --set llamacpp.max_words=26  # prompt chunk size; 0 disables chunking

# an OpenAI-compatible server (no key needed for a local one)
tts config --set openai.base_url=http://localhost:8880/v1
tts -p openai -o out.mp3 "hello"
```

Per-run overrides that change nothing permanently: `tts -s threads=4 "..."`.

## Tone and emotion tags

Wrapping text in `<name>...</name>` (e.g. `<happy>Good news!</happy>`) marks its tone or
emotion — `local-tts-speak` is where an agent is told to actually use this. Configuring it
is what this section covers: what each backend does with a tag, and the two settings that
control it. There is no fixed tag vocabulary to install or manage — any word works (see
`text.TAG_PROFILES` in the source for the built-in presets: anger, happy, joy, sad, fear,
surprise, disgust, calm, excited, serious, whisper, sarcastic, urgent, gentle, confident,
tired, playful, question, exclamation); anything else still works, just without a hand-tuned
speed/volume preset behind it.

**Per-backend realization** (verified against each backend's own real interface, not
assumed):

| Backend | What a tag does |
| --- | --- |
| `openai` | The real thing — sends the tag's phrase as the `instructions` field. **Only `model=gpt-4o-mini-tts`** (or its dated alias) accepts this; `tts-1`/`tts-1-hd` reject it, and local-tts raises a clear error rather than silently dropping it if a tag is used with the wrong model. |
| `piper` | Approximated with `--length-scale` (rate) and `--volume` (real piper flags) — not true emotional synthesis, just faster/slower and louder/quieter. |
| `kokoro` | Approximated with speed (`-s`) only — kokoro/kokoro_onnx has no volume or pitch knob at all, verified against `Kokoro.create()`'s own signature. |
| `llamacpp` | No synthesis-time hook exists (verified: no style/emotion flag in `llama-tts --help`), so the tag's **speed and volume are applied to the rendered audio instead** (see below). The free-text half of a tag has nowhere to go and is dropped. |
| `rvc` | Voice conversion has no text or emotion input at all. rvc splits on the tags itself, converts **each span separately**, and shapes each converted span's speed/volume afterwards. Converting one merged wav would give every span one flat delivery, which is exactly what tags exist to prevent. |
| `command` | Depends on `command.tone_tags` (below) — local-tts can't know what an arbitrary script understands. |

More than one segment (a tag partway through the text) means more than one synthesis call,
joined afterward — the same chunk-and-join machinery already used for long text, so nothing
extra to install or configure for that.

**What a backend cannot do at synthesis time is done to the audio afterwards.** A tone
profile is two measurable numbers (a speed multiplier and a volume multiplier) plus a
free-text instruction. Each backend declares which of the two it realizes itself —
`piper` both, `kokoro` speed only (it genuinely has no volume knob), `openai` both,
`llamacpp` and `rvc` neither — and anything left over is applied to that segment's
rendered wav by `localtts/audiofx.py`. So `<whisper>` is quieter on kokoro even though
kokoro has no volume control, and an emotion is audible on llamacpp even though it has no
style flag at all.

This is deliberately per *segment*, not over the finished file: the profile changes from
span to span, and a transform applied to the join could no longer tell them apart. It
costs nothing when no tag asks for a change — untagged text still takes the plain
single-call path, byte for byte as before.

Speed uses ffmpeg's `atempo` when ffmpeg is on PATH (pitch-preserving), and falls back to
a pure-Python WSOLA stretch otherwise, so the feature never silently does nothing on a
machine without ffmpeg — it is just cleaner with it. Volume is exact integer scaling,
clamped rather than wrapped.

**If a user says tagged speech sounds robotic, buzzy or warbly, check ffmpeg first** —
`tts check` prints a `tone shaping:` line naming which path is in use. This is the single
most likely cause, because a tag's speed change touches every sample of the span:

```bash
command -v ffmpeg || echo "no ffmpeg -- tagged speech is using the built-in stretch"
sudo apt install ffmpeg     # Debian/Ubuntu (needs sudo — ask first)
brew install ffmpeg         # macOS
```

Recommend it during any install or configuration too, not only when something already
sounds wrong — but **say what it buys and ask**, since it needs `sudo` on Linux. The
fallback is deliberately adequate, so nothing is broken without ffmpeg; it is just worse,
and silently so.

The other reason a tag can sound wrong is the *base* voice, not the shaping: a tag whose
profile leaves speed at 1.0 (`<question>`, `<confident>`, `<sarcastic>`) is never
retimed at all, so if those sound fine and the rest do not, the shaping path is the
culprit — and if all of them sound the same, the tags are not reaching the provider.

Only 16-bit PCM wav can be shaped this way, which covers every offline backend. A provider
whose `default_format` is compressed (openai's mp3) opts out rather than be decoded and
re-encoded — it realizes tone itself anyway.

**Two settings, each per-provider** (`openai`, `piper`, `kokoro`):

```bash
tts config --set openai.tone="speak warmly and slowly"   # flat instructions with NO tags anywhere in the text
tts config --set openai.auto_tone=true                    # also derive tone from ?/!/. when no tag is active
tts config --set piper.auto_tone=true
tts config --set kokoro.auto_tone=true
```

`auto_tone` (off by default — ask before turning it on, it changes *how* things are said
without being asked per-utterance) classifies any untagged sentence by its own trailing
punctuation and applies the same built-in "question"/"exclamation" presets a `<question>`/
`<exclamation>` tag would. `openai.tone` is a flat fallback instructions string used only
where no tag/auto_tone applies at all — piper/kokoro have no free-text equivalent (no
"instructions" hook to send one to), so they don't have this setting.

**`command.tone_tags`** (default `"strip"`) controls whether a `<tag>` reaches a custom
`command` template's `{text}` verbatim (`"pass"`) or gets removed first like every other
backend with no real hook (`"strip"`). Only set this to `"pass"` if the user's own script is
written to parse the markup itself — ask first, since local-tts has no way to verify that:

```bash
tts config --set command.tone_tags=pass
```

**`command.audio_fx`** (default `false`) is the matching decision for the *audio* half.
Every other backend has had its capabilities verified here, so whatever it cannot do at
synthesis time is safely local-tts's to apply afterwards. A `command` template is somebody
else's script: it may already vary its own delivery, and speeding up or rescaling audio it
deliberately shaped would fight it. So by default the command's output is left exactly as
it rendered it. Turn it on only when the user confirms their script does nothing with tone:

```bash
tts config --set command.audio_fx=true
```

## Picking and tuning the audio player

Autodetect prefers **Windows' own player on both Windows and WSL** (always present, and
the native way out of either), and the first available Linux player elsewhere. This is
deliberate on WSL: installing ffmpeg — which this skill recommends for tone shaping —
would otherwise let a fresh `ffplay` silently take over from a player that was already
working, and WSL's PulseAudio bridge is often the noisier of the two.

```bash
tts config --set player=ffplay      # name a Linux player explicitly
tts config --set player=windows     # or force Windows' own (also "powershell")
```

**If a user reports noisy, crackling or distorted playback, suspect the player before the
synthesis.** The quickest discriminator is to play one file two ways and ask which is
clean — a plain untagged file, so tone shaping is not in the picture:

```bash
tts -p piper --no-play -o /tmp/ab.wav "The quick brown fox jumps over the lazy dog."
ffplay -nodisp -autoexit -loglevel error /tmp/ab.wav
powershell.exe -NoProfile -NonInteractive -Command "(New-Object Media.SoundPlayer '$(wslpath -w /tmp/ab.wav)').PlaySync()"
```

If only one is noisy, it is the player, and the answer is `player=` — not a config change
to any provider. If both are, the noise is in the audio itself.

Per-machine tuning, for when a player is *nearly* right:

```bash
tts config --set 'player_args.ffplay=-af aresample=48000'   # inserted before the file
tts config --set player_env.PULSE_LATENCY_MSEC=90           # set for the player only
tts config --set player_args.ffplay=                        # empty value removes it
```

Worth trying when a device resamples badly or underruns; **verify by ear rather than
assuming**, since these are machine-specific and some combinations make things worse.
`tts check` prints which player is being used and any tuning in effect.

## Phonetics: a borrowed word said properly

Real speech mixes languages, and "Ya subí el pull request" read entirely with Spanish
phonetics sounds wrong. Give the pronunciation dictionary the word's IPA and the same
voice says it correctly, inside the same utterance:

```bash
tts config --set 'pronunciations.pull request=/pˈʊl ɹᵻkwˈɛst/'
tts --lang es "Ya subí el pull request al repositorio."
```

A value between slashes is IPA; a bare value is a respelling, as before. Both live in
the same `pronunciations` table.

**This replaces the old `<en>…</en>` language spans.** Those synthesized the borrowed
word separately and spliced it in, which gave it its own end-of-sentence intonation --
mid-sentence that reads as an interruption -- and left a seam at each edge. Measured on
a sentence with three English words: 4.651s spliced against 4.020s as one utterance.
Old markup is recognized and removed rather than read aloud, so existing text is safe.

**Any language, one caveat.** IPA is not tied to one language, so `/ˈkʁwasɑ̃/` for a
French word inside Spanish works the same way. The limit is the backend's phoneme
vocabulary: a model only produces sounds it was trained on, and an unfamiliar one comes
out as the nearest thing it has.

**Not every backend can use IPA**, and this is worth saying to the user rather than
letting them discover it. local-tts has no runtime dependencies and cannot transcribe
text itself, so it passes the table to a backend with its own phonemizer:

| Backend | IPA entries |
| --- | --- |
| `kokoro` with a **running, current** server | yes -- the server holds the phonemizer |
| `rvc` over a kokoro base | yes -- inherited from the base |
| `kokoro` without a server | no -- the CLI wrapper takes text only |
| `piper`, `llamacpp`, `openai`, `command` | no -- the word is said their own way |

`tts check` prints which, so read it back rather than promising:

```
phonetics   : 2 word(s) with /IPA/ in the table -> kokoro, rvc; ignored by llamacpp, openai, piper, command
```

An ignored entry is not an error. If the user needs IPA and their backend cannot take
it, the fix is the persistent kokoro server (above), not a different dictionary entry.

**`check` asks the server, it does not assume.** A configured `server_url` says a URL was
written down; a running process says nothing about whether it is this version of the
script. `/health` reports `{"ok": true, "phonetics": true}`, and a server copied from an
earlier version answers with a plain `ok` -- so it reads as *ignored* rather than as
working, and local-tts does not send it a table it would drop. If a user's entries show
as ignored while their server is up, their script predates this: `tts servers` says so,
and `tts servers --refresh` rewrites it and stops the old process. That is the upgrade
path -- no hand-copying, and the previous file is kept as `.bak`.

**Where to get the IPA.** Wiktionary prints it for most words; `espeak-ng --ipa -q -v en
"pull request"` prints it for anything. Use the transcription of the language the word
comes *from* -- that is the entire point. To pick between candidates by ear, and to be
told when a transcription asks for phonemes the model has no token for, use
`tts pronounce` and the **local-tts-phonetics** skill rather than guessing.

### Transcriptions nobody typed in: `phonetics_hooks`

The dictionary is for words a person wrote down. When the answers are *generated* -- a
lexicon, a team glossary, a real grapheme-to-phoneme transcriber -- they belong in a
hook instead, because local-tts has no runtime dependencies and cannot grow a
transcriber of its own.

A hook is any executable named in `phonetics_hooks`. It gets one utterance's resolved
table on stdin as JSON and prints the table to use:

```bash
tts config --set phonetics_hooks='["~/bin/lexicon.py"]'
tts config --set phonetics_hook_timeout=5     # seconds, per hook
```

```python
#!/usr/bin/env python3
import json, sys
call = json.load(sys.stdin)
#  {"text": ..., "lang": "es", "provider": "kokoro", "phonetics": {word: ipa}}
table = call["phonetics"]
table["croissant"] = "k\u0281was\u0251\u0303"      # bare IPA, or /between slashes/
print(json.dumps({"phonetics": table}))
```

What to tell a user setting one up:

- They run **in order**, each seeing what the last returned, and may add, rewrite or
  drop entries.
- They **cannot change the text**. A hook can only change how a word is transcribed,
  never what is said.
- A non-zero exit, output that is not JSON, or a script slower than the timeout leaves
  the table alone and prints one line to stderr. Speech that is slightly wrong beats
  speech that does not happen.
- A `.py` without `chmod +x` is run with the current interpreter rather than refused.
- It is the user's own program running with the user's privileges -- the same trust as
  `server_start`. Say so when you set one up for them.
- Unrelated to `tts hooks`, which is the status-bar hook. Do not conflate them in what
  you tell the user.


## Pronunciation dictionary

Say these words this way — applied before synthesis on every backend:

```bash
tts config --set pronunciations.jarvis="JAR-viss"
tts config --set pronunciations.es:jarvis="yarvis"   # Spanish only
tts config --set pronunciations.jarvis=                # empty removes it
```

Keys match whole words, case-insensitively; the replacement is used exactly as written. A
bare key applies everywhere, `<lang>:<word>` to one language. Tone-tag markup is never
rewritten. Reach for this when a user says a name or a technical term comes out wrong —
it is almost always the right fix, and much cheaper than changing voices.

## Kokoro voices per language

Kokoro names voices by language (`a`/`b` English, `e` Spanish, `f` French, ...), so one
flat `voice` cannot serve two languages:

```bash
tts config --set kokoro.language_voices.en=bm_george
tts config --set kokoro.language_voices.es=ef_dora
```

The phonemizer language is then derived from the chosen voice, not from `lang` — a stale
`lang` is how one language ends up read with another's phonetics. Clear the flat
`kokoro.voice`/`kokoro.lang` once a map is in place, or an unmapped language silently
inherits them.

**Voice prefixes:** `af_`/`am_` US English, `bf_`/`bm_` British English, `ef_`/`em_`
Spanish, `ff_` French, `hf_`/`hm_` Hindi, `if_`/`im_` Italian, `jf_`/`jm_` Japanese,
`pf_`/`pm_` Brazilian Portuguese, `zf_`/`zm_` Mandarin.

Kokoro also has three delivery settings of its own, distinct from rvc's `delivery` map
(which is about the gaps *between* fragments):

```bash
tts config --set kokoro.emphasis_lengthen=2   # IPA length marks on the stressed vowel
tts config --set kokoro.sentence_pause=0.25   # kokoro's own within-utterance pauses,
tts config --set kokoro.clause_pause=0.1      # in seconds; empty leaves them alone
```

`emphasis_lengthen` needs the **persistent server**, which is where the phonemizer lives —
the subprocess CLI wrapper has no equivalent flag. The server falls back to plain text if
phonemization fails, so it can never cost the user their audio.

## Delivery: pacing, pauses, emphasis

```bash
tts config --set 'rvc.delivery.es={"speed": 1.0, "pause_ms": 45, "pause_tone_ms": 130, "emphasis_lengthen": 2}'
tts config --set 'rvc.delivery.en={"speed": 1.0, "pause_ms": 60, "pause_tone_ms": 160}'
```

`pause_ms` is the gap between fragments delivered the same way; `pause_tone_ms` the
longer one where the tone changes — the breath. `"*"` covers unnamed languages.

`trim_ms` (default 10) is the silence left at each fragment edge **before** the pause is
applied. It exists because every fragment arrives with its own lead-in and tail — the
synthesizer's padding plus whatever conversion adds, measured at 0.15–0.25s each — so
without trimming, the real gap is that dead air *plus* `pause_ms`, and the setting
controls neither. On one mixed-language sentence, trimming cut 7.24s to 5.79s without
changing a word. Raise it if onsets sound clipped; lower it if gaps still feel long.

`emphasis_lengthen` puts N IPA length marks on the vowel carrying primary stress
(`kˈasa` → `kˈaːsa`). It needs a **kokoro base with the persistent server**, since that
is where the phonemizer lives; the server falls back to plain text if phonemization
fails, so it can never cost the user their audio. The effect is subtle on a long
sentence and clearest on short, emphatic spans — have the user judge it by ear before
raising the number.

## Streaming playback (on by default)

Long text used to be silent until the *last* fragment was rendered — most of a minute for
a tagged story. Each fragment is now played the moment it exists, while the rest are still
being synthesized, so time-to-first-sound stops depending on how long the text is. The
single joined file is still written exactly as before.

```bash
tts config --set stream=false     # go back to synthesize-everything-then-play
tts --no-stream "..."             # or just for one run
```

It applies to any wav backend and needs no setup. `tts check` prints a `streaming:` line
saying which mode is active. Leave it on unless a user has a specific reason to want one
file assembled before anything is heard — the fragment boundaries are the tone-tag and
chunk boundaries that were already there, so nothing is split that was not split before.

## Every setting, in one place

`tts config --show` prints the effective configuration; `tts config --init` writes a file
containing every default, ready to edit. This is the map of what those keys mean, so you
never have to guess whether something is configurable.

**Top level**

| Key | Default | What it does |
| --- | --- | --- |
| `provider` | `kokoro` | backend used when `--provider` is not given |
| `play` | `true` | play after synthesis (unless `--output`) |
| `player` | `""` | force a player; `windows`/`powershell` names the Windows one |
| `player_args` | `{}` | extra argv per player, e.g. `{"ffplay": ["-af", "aresample=48000"]}` |
| `player_env` | `{}` | environment for the player process only |
| `pronunciations` | `{}` | word → respelling, or `/IPA/` for phonetics; `<lang>:<word>` scopes it |
| `terminal_title` | `true` | speaker icon in the terminal tab while playing |
| `stream` | `true` | play each fragment as it is synthesized |
| `phonetics_hooks` | `[]` | executables that shape the `/IPA/` table per utterance (see above) |
| `phonetics_hook_timeout` | `5` | seconds one hook may take before the call goes on without it |
| `languages` | `{}` | the language memory — see `tts languages` |

**kokoro** (the default backend)

| Key | Default | What it does |
| --- | --- | --- |
| `binary` | `kokoro-tts` | the wrapper CLI |
| `model_dir` | `""` | only for a CLI that resolves models by working directory |
| `voice` / `lang` | `""` | flat fallback when no per-language voice applies |
| `language_voices` | `{}` | language → voice; the phonemizer language follows the voice |
| `speed` | `1.0` | rate multiplier |
| `emphasis_lengthen` | `0` | IPA length marks on the stressed vowel (server only) |
| `sentence_pause` / `clause_pause` | `""` | kokoro's own within-utterance pauses, seconds |
| `auto_tone` | `false` | derive tone from `?`/`!` when no tag is active |
| `server_url` / `server_start` / `server_timeout` | | the persistent server |
| `extra_args` | `[]` | appended to every call |

**piper**

| Key | Default | What it does |
| --- | --- | --- |
| `binary` / `model` | | the executable and the flat `.onnx` voice |
| `language_models` | `{}` | language → `.onnx`; a piper voice *is* a language |
| `speaker` | `null` | speaker id for a multi-speaker voice |
| `length_scale` / `volume` | `null` | base rate and loudness (a tag multiplies these) |
| `auto_tone`, `extra_args` | | as kokoro |

**rvc** — voice conversion over a base provider

| Key | Default | What it does |
| --- | --- | --- |
| `python` | `""` | interpreter of the rvc-python venv |
| `base_provider` | `""` | which backend speaks before conversion (kokoro is the sensible one) |
| `model` / `index` | `""` | the `.pth` and `.index` for the CLI fallback |
| `device` | `cpu` | `cuda:0` only if that venv has a CUDA torch |
| `pitch` / `method` / `index_rate` / `protect` | | conversion parameters; method uses server startup flags |
| `conversion` | `{}` | per-language pitch, index_rate and protect overrides |
| `server_url` / `server_start` / `server_timeout` | | the persistent multi-voice server |
| `server_models` / `server_model` | | voices the server holds, and the fallback one |
| `language_models` | `{}` | language → resident voice name |
| `delivery` | see below | per-language pacing |

**`rvc.delivery.<lang>`** — `"*"` covers any language not named

| Key | Default | What it does |
| --- | --- | --- |
| `speed` | `1.0` | folded into the base provider's own rate control |
| `pause_ms` | `45` | gap between fragments delivered the same way |
| `pause_tone_ms` | `130` | gap where the tone changes — the breath |
| `trim_ms` | `10` | silence left at each fragment edge *before* the pause |
| `emphasis_lengthen` | `0` | IPA length marks (needs a kokoro base + server) |

**openai** — `base_url`, `api_key`, `model` (`gpt-4o-mini-tts` for tone), `voice`, `speed`,
`timeout`, `tone` (flat instructions), `auto_tone`.

**llamacpp** — `binary`, `model`/`vocoder` (both or neither), or pull from Hugging Face
instead with `hf_repo`/`hf_file` and `hf_repo_vocoder`/`hf_file_vocoder`; plus
`speaker_file`, `max_words` (26), `max_workers` (2), `threads`, `gpu_layers`,
`guide_tokens`, `extra_args`.

**command** — `template` (must contain `{text}` and `{output}`), `tone_tags`
(`strip`/`pass`), `audio_fx` (`false` — see above).

**If the user asks to make it *sound* better** rather than to set something up — pacing,
robotic artifacts, noise, gaps — that is the `local-tts-tune` skill, not this one.

## Diagnosing

| `tts check` / error says | Meaning | Fix |
| --- | --- | --- |
| `'llama-tts' not found on PATH` | llama.cpp missing | install it, or `tts config --set llamacpp.binary=/full/path` |
| `model set without a vocoder` | llamacpp needs both files | set `llamacpp.vocoder`, or clear `llamacpp.model` to use defaults |
| `no voice model configured` | piper has no `.onnx` | download one, set `piper.model` |
| `('x' is not on PATH)` on `command` | template points at a missing binary | install it or change the template |
| `no api_key and $OPENAI_API_KEY is unset` | only matters if they use `openai` | export the key, or ignore |
| `players : none found` | no audio player (Linux only) | install `ffmpeg`, or always use `-o` |
| `tone shaping: built-in WSOLA` | no ffmpeg; tagged speech is retimed in pure Python | install `ffmpeg` for `atempo` — ask first, it needs sudo |
| right words, wrong accent | llamacpp used for an unsupported language | switch that language to piper |

Useful when something is off: `--verbose` streams the backend's own stderr, and `--dry-run`
prints the exact command (plus the chunk plan) without running it.

## Platform differences that actually matter

| | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Python launcher | `python3` | `python3` | `py` or `python` |
| venv binaries | `<venv>/bin/` | `<venv>/bin/` | `<venv>\Scripts\` + `.exe` |
| Config file | `~/.config/local-tts/config.json` | same | `%APPDATA%\local-tts\config.json` if `XDG_CONFIG_HOME` is unset, else that |
| Audio player | needs one installed | `afplay`, built in | PowerShell player, built in |
| llama.cpp | package manager, release zip, or build | `brew install llama.cpp` | `winget install llama.cpp` |
| `~` in a config value | expanded by the CLI | expanded | **use a full path**; PowerShell will not expand it |

The CLI itself expands `~` in config values on every platform, so
`tts config --set piper.model=~/voices/x.onnx` is fine — the risk on Windows is the *shell*
not expanding it before the CLI sees it, which is why the examples above use
`$env:LOCALAPPDATA`.

Run commands one at a time rather than `&&`-chaining them; `&&` is not valid in older
PowerShell.

## Finish

Re-run `tts check`, speak one real sentence in the user's language, and tell them what you
changed, what you recorded in `tts languages`, and the one command they will use daily.

## v2 settings, caching and model warm-up

Use `tts settings` for the terminal editor or keep using `tts config --set KEY=VALUE`.
Both write the same configuration atomically. Changes load on the next request;
the editor and bounded warm sessions also refresh external edits. Never promise
live replacement of an active model: startup-only server settings take effect when
that server next starts, without interrupting active speech.

New settings (all also exposed as LOCALTTS_<UPPERCASE_KEY>):

| Setting | Default | Meaning |
| --- | --- | --- |
| cache_enabled | false | Opt in to the persistent audio cache |
| cache_ttl_hours | 36 | Fixed expiration after insertion |
| cache_max_mb | 256 | MiB limit for cache entries, including headers |
| cache_policy | lfu | Evict least-used first; lru selects oldest access |
| cache_dir | empty | OS cache directory; custom root gets audio-v1 subdirectory |
| cache_revision | empty | Manual cache invalidation for opaque remote model changes |
| ending_silence_ms | 0 | Extra silence only after the final WAV fragment; 0–5000 |
| warm_keep_alive_seconds | 0 | Default bounded warm duration; 0–86400 |
| warm_interval_seconds | 10 | Keep-alive/config reload interval; 0.1–60 |
| tui_refresh_seconds | 1 | Editor reload interval; 0.1–60 |

`tts cache status|prune|clear` manages the cache. `--no-cache` bypasses it;
`--refresh-cache` renders and replaces an entry. Dynamic phonetics hooks bypass it.
Expiration and budget cleanup run on activity, not via a hidden daemon. The budget
does not cover exported recordings, model files, RAM/VRAM, or filesystem block
rounding. Cache hits are copied before playback, so eviction cannot remove playing audio.

`tts warm --lang en --lang es` loads selected models without playback.
`tts warm --lang en --text "The build is ready." --keep-alive 1800` prefills a phrase
and keeps local servers resident for thirty minutes. It runs in the foreground;
launch it in a managed terminal when ongoing warmth is desired. Stop with Ctrl-C.
Do not describe an enabled cache as proof that an oversized/failed entry was stored.
Use `tts cache status` and `--verbose` to verify a hit. Refresh old server scripts
with `tts servers --refresh` before using keep-alive. Cache removes synthesis latency,
not PowerShell startup or time queued behind another speaker.
