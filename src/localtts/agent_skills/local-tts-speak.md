---
name: local-tts-speak
description: TRIGGER — whenever the user's message means "say this out loud / speak / talk to me / read it to me", in any language, invoke this skill and actually run `tts` — do not just reply in text and describe what you would say, and do not treat a bare one-word request like "talk", "speak", "habla" or "parle" as too vague to act on; it is the whole request. Covers "talk to me", "say that out loud", "read this file to me", "narrate this", "can you speak?", "make an audio of this", "tell me X out loud", "reply in voice", "keep talking to me" (a standing request — stay in voice for every following reply until they say otherwise), and the same phrasings in other languages ("háblame", "léeme esto", "dímelo en voz alta", "lis-moi ça", "parle-moi", "sag mir das laut", "leggimelo"). Speaks fully offline with the local `tts` CLI (local-tts), auto-picking the right backend for the language.
---

# Speaking to the user with `local-tts`

The `tts` command turns text into speech locally. **If you are reading this file, the
user's message already means "run `tts`," not "reply in text about it."** Use it whenever
the user wants to **hear** something rather than read it — including a bare "talk to me" or
"habla" with nothing else attached; that alone is the complete request, not something too
vague to act on.

## When to use this

Triggers include: "talk to me", "say that out loud", "read this file to me", "narrate
this", "can you speak?", "make an audio of this", "háblame", "léeme esto", "lies mir das
vor". Also use it when the user has asked you to *keep* talking to them — once they ask for
voice, prefer voice for your substantive answers until they say otherwise, without needing
to be asked again each time.

Do **not** use it for ordinary text replies the user did not ask to hear.

## Before the first use

```bash
command -v tts || echo "local-tts is not installed"
tts check
```

`tts check` prints every backend and marks the usable ones `[ok]`. If `tts` is missing, or
the backend for the language you need is not `[ok]`, switch to the
`local-tts-configure` skill instead of improvising.

## Choosing the language

**Always check the recorded preferences first.** They are the user's own past feedback:

```bash
tts languages
```

Then speak with the language flag, which selects the remembered backend and voice:

```bash
tts --lang es "Hola, ya terminé la tarea."
tts --lang en "Done — the tests pass."
```

If the language you need is **not** recorded, do not guess silently. Pick a sane default,
say which one you used, and offer to remember it:

- The default is `kokoro`, which speaks eight languages: English, Spanish, French,
  Italian, Portuguese, Hindi, Japanese and Mandarin. `llamacpp` only speaks
  **English, Chinese, Japanese and Korean** -- anything else comes out with English
  phonetics and sounds wrong, so don't reach for it outside those four.
- For a language outside kokoro's eight — German, Dutch, Polish, Russian, Turkish and
  most others — `piper` is the backend that has it (~40 languages), but it needs a voice
  installed first. If neither is set up, use the
  `local-tts-configure` skill rather than guessing which one to install.
- `rvc` is different from the other backends: it's a voice *conversion* layer over
  whichever provider it's configured to use underneath, for a specific voice the user
  already has a trained model for — not a general-purpose language backend. Use it only
  when the user asked for that particular voice, never as a language fallback.

## The default way to speak: `-b`

**Always use `--background` (`-b`).** It synthesizes, starts playback, and returns
immediately — you are never blocked for the length of the audio, and the user can pause or
stop it while you carry on working. It also keeps the file and prints its path.

```bash
tts -b --lang es "Terminé de revisar el código."
```

Prints the path on stdout and the controls on stderr:

```
playing in the background (pid 4123) — `tts stop` to end it
/tmp/local-tts-a1b2c3d4.wav
```

**If more than one of you could be running at once** (multiple terminals, multiple agent
sessions on the same machine), pass `--session` with something that identifies *this* run,
so your playback and status-bar entry stay yours — starting new audio in one session must
never stop another session's, and one session's status bar must never show another's
progress:

```bash
tts -b --lang es --session "$CLAUDE_CODE_SESSION_ID" "Terminé de revisar el código."
```

If you have a stable session id available to you (Claude Code sets
`$CLAUDE_CODE_SESSION_ID` for every command it runs — check `echo $CLAUDE_CODE_SESSION_ID`
once and use it every time), pass it explicitly like that. `tts` also auto-detects the same
variable when `--session` is omitted, so this only matters on a host without a known
variable yet, or when you want to be certain rather than rely on the fallback. Omitting it
entirely is still safe for the common case (one session at a time) — it just shares the one
original global slot, exactly like before this existed.

Session isolation is about *control* (whose `stop`/`pause`/status this is), not about
letting two sessions' audio overlap: actual playback is serialized machine-wide, so if
another session already has something playing, yours queues silently behind it rather than
talking over it. You don't need to do anything about this — `-b` still returns immediately
either way — just don't assume the audio is audibly playing the instant the command
returns; on a quiet machine it will be, but if another session is mid-sentence, yours
starts once that one finishes.

**Run the command itself in the background too**, using whatever mechanism you have for
non-blocking shell commands. `-b` returns as soon as playback is queued (see above — it may
briefly wait its turn behind another session before audibly starting), but *synthesis*
still happens first, and that takes a moment (a second or two for a sentence, ~12s for a
1000-word document). Never sit blocked on it.

### Sound human, not flat

A monotone reading of everything is not the goal — **prefer expressive delivery over a flat
recitation.** Wrap a stretch of text in `<name>...</name>` to mark its tone/emotion, e.g.:

```bash
tts -b "<happy>Good news, the tests pass!</happy> <serious>One thing still needs your review though.</serious>"
```

Use tags **whenever the content actually calls for it** — genuine excitement, a real
question, delivering bad news gently, a joke — not mechanically on every sentence. A short,
neutral status update needs none of this. A tag wraps only the words it actually applies to;
plain text outside any tag is unaffected, and you can mix several in one call. If the text
itself needs to literally contain an angle bracket (rare), escape it: `\<` / `\>`.

Any reasonable word works as a tag name — `<anger>`, `<happy>`, `<sad>`, `<excited>`,
`<serious>`, `<whisper>`, `<calm>`, `<urgent>`, `<gentle>`, `<sarcastic>`, `<playful>`,
`<question>`, `<exclamation>` all have a built-in preset; anything else still does *something*
reasonable, it just isn't hand-tuned. **How much a tag actually changes the audio depends on
the backend** — openai's `gpt-4o-mini-tts` model genuinely performs the emotion; piper and
kokoro approximate it with pacing (and, for piper, volume). On backends with no hook of
their own (`llamacpp`, `rvc`) the tag's speed and volume are applied to the rendered audio
afterwards instead, so a tag is still audible there rather than being a no-op. `rvc`
converts each tagged span separately for exactly this reason. The markup itself is never
spoken literally on any backend. Tone markup is not spoken literally. However, rate and gain changes do not reproduce
natural emotion, and many short spans can sound choppy. Prefer complete phrases and
use tags sparingly; compare the untagged voice when speech sounds robotic.

If a user consistently wants their own text (already containing sentences that read as
questions or exclamations) to sound that way automatically, without you adding tags by hand,
that's a separate opt-in setting — point them at the `local-tts-configure` skill for
`<provider>.auto_tone`, not something to enable yourself mid-conversation.

### Always play the whole thing

**Play regardless of length.** Do not decide for the user that something is too long to
listen to, do not truncate, and do not silently switch to a summary. If they asked to hear
a document, play the document.

The only exceptions are when the user says otherwise — "just save it", "don't play it",
"give me the file" — or when there is no audio player at all. Then use `--no-play` and hand
them the path.

### Letting the user pause and stop

Because playback is detached, the user stays in control. **Tell them these once, the first
time you speak in a session**, then do not repeat them every turn:

```bash
tts pause      # suspend it
tts resume     # continue
tts stop       # end it
tts playback   # is anything playing? paused or playing, and which file
```

Run these immediately when the user asks — "stop", "pause", "shut up", "para", "silencio"
all mean run the command now, before answering anything else. Starting a new playback
stops the previous one automatically, so you never stack two voices.

`pause` and `resume` are POSIX-only (Linux, macOS, WSL). On native **Windows** they report
that they are unsupported — there, `stop` is the control, and you should say so rather than
suggesting pause.

### The terminal title shows a speaker icon on its own

While something is playing, the terminal's tab/window title becomes `🔊 0:12 <file>`, and
goes back to normal the moment it stops. **You do not have to do anything for this** — the
background runner that owns the playback sets it and clears it, including when the user
runs `tts stop` or the audio simply ends. Do not print escape sequences yourself, and do
not try to "restore" the title afterwards; you would be fighting the process that already
handles it.

It is skipped when there is no terminal to write to (your command output piped into
another tool, a status-line hook, CI). A user who keeps their own tab titles can turn it
off with `tts config --set terminal_title=false` — if they ask for that, it is a config
change, not something to work around per call.

Worth mentioning once to a user who says they lose track of whether audio is still
playing: the tab title is the answer, and unlike a chat message it updates itself.

### Check for a status-bar hook first

Some hosts (currently Claude Code and Qwen Code — see `local-tts-configure` for why not
others) can run a script that paints your own status bar, refreshed on a real timer. If one
is installed and live, playback progress already has a genuine, continuously-updating home
that isn't a chat message. Check once, right before you would otherwise print a status
line:

```bash
tts hooks --status
```

Prints `active` and exits 0 if a hook is currently being called by a live session;
`inactive` and exits 1 otherwise (nothing installed, or installed somewhere that isn't the
session you're in right now — this is what "for that agent" actually means, since the
check is host-agnostic and just tests whether *some* installed hook is being invoked).

**If active:** do not print the chat status line at all — it is genuinely redundant, the
bar the user is looking at is real and moving. The first time this happens in a session,
say once that progress is in their status bar; after that, stay silent about it and just
speak.

**If inactive:** fall back to exactly the behavior below — this is the only way most hosts
can show anything, so it stays the default.

### Present a status, not the raw command

Whether the human running you sees the `tts` invocation itself (the command, its raw
stdout/stderr) depends on your host, not on this skill — some show every tool call, some
keep them collapsed. Either way, **your own reply text is yours to compose**, and it should
never just be the pasted command or its raw output. Write a short status line instead.

A chat reply cannot repaint itself, and there is no live position to show anyway — most
players expose no progress interface, so anything that looked like it was "filling up"
would be fabricated, not measured. Don't build a progress bar and don't chase one with
follow-up messages. **Say it once, with the icon and the duration `tts -b` already printed,
and stop:**

```
🔊 0:12 · /tmp/local-tts-a1b2c3d4.wav
```

That is the whole status — one line, sent once, when playback starts. No bar, no midpoint
check, no "still playing" follow-up, no completion notice. If the user wants to know how
far along it is, *then* run `tts playback` and relay what it says — on request only, never
proactively:

```bash
tts playback
# playing [###########---------] 0:03 / 0:05 (pid 4123): /tmp/local-tts-a1b2c3d4.wav
```

That bar is real (computed from elapsed time against the file's duration), which is exactly
why it belongs behind an explicit ask rather than in the default status — showing it
unprompted implies an ongoing display this is not.

### Always tell the user where the file is

Every `-b` run prints the path. **Say it in your reply**, every time — the user may want to
replay it, keep it, or send it on. Combined with the status line above, a full first reply
looks like:

> 🔊 0:12 · `/tmp/local-tts-a1b2c3d4.wav`
> Say "stop" or "pause" any time.

Default to a temp folder, which is what `-b` does on its own (`/tmp` on Linux/macOS,
`%TEMP%` on Windows). Only write elsewhere when the user asks for a specific location:

```bash
tts -b --lang es -o ~/audio/resumen.wav -f resumen.md     # their location, still plays
```

Temp files survive until the OS clears them, so a path you printed stays valid for the rest
of the session. Do not delete it yourself unless asked.

## Other commands

```bash
# read a file aloud; markdown syntax is stripped automatically for .md
tts -b --lang es -f notas.md

# the user asked for a file and no playback
tts --no-play --lang es -f notas.md -o notas.wav

# blocking playback, only if you deliberately want to wait for it to finish
tts --lang es "corto"
```

Long documents need no special handling: markdown is stripped for `.md` sources, and
backends that need short prompts get the text split at sentence boundaries and rejoined
into one file.

## Remembering the user's feedback

This is the important part. When the user reacts to a voice — **write it down** so every
agent and every future session benefits:

| The user says | Run |
| --- | --- |
| "use piper for Spanish" | `tts languages --set es=piper` |
| "use this voice for Spanish" | `tts languages --set es=piper:/path/to/voice.onnx` |
| "that accent is wrong, use the Mexican one" | `tts languages --set es-MX=piper:/path/to/es_MX-....onnx` |
| "English is fine as it is" | `tts languages --set en=llamacpp` |
| "stop using piper for German" | `tts languages --forget de` |

Confirm briefly after recording it ("noted — Spanish will use piper from now on"), then use
`--lang` for that language from then on. Never keep a language preference only in your own
context: the config file is the shared memory, and other agents read it too.

If the user's complaint is about **quality** rather than language (too fast, wrong gender,
robotic), that is a voice choice — list the alternatives with the
`local-tts-configure` skill and let them pick, then record the winner the same way.

## Platform notes

The `tts` commands above are identical on **Linux, macOS and Windows** — only the shell
quoting differs.

| | Linux / macOS | Windows (PowerShell) |
| --- | --- | --- |
| Run it | `tts --lang es "hola"` | `tts --lang es "hola"` |
| Check it exists | `command -v tts` | `Get-Command tts` |
| Quoting | single or double quotes | double quotes; `` ` `` escapes, not `\` |
| Paths in arguments | `~/notes.md` | `"$HOME\notes.md"` or a full path |

- On **Windows** playback uses PowerShell's built-in sound player, so it works with nothing
  installed.
- On **macOS** it uses `afplay`, also built in.
- On **Linux** it needs one of `ffplay`, `paplay`, `aplay`, `play`, `mpv` or `cvlc`. If
  `tts check` reports `players : none found`, always pass `-o file.wav` and tell the user
  the path instead of trying to play.
- In **WSL**, playback reaches out to Windows automatically. Write output under the Linux
  filesystem or `/mnt/c/...`, and convert Windows paths the user gives you:
  `C:\Users\x\a.md` becomes `/mnt/c/Users/x/a.md`.

Do not assume a shell. If the user is on Windows, do not emit `&&`-chained POSIX one-liners;
run the commands one at a time.

## Practicalities

- **Without `-b`, playback blocks** until the audio finishes. That is occasionally what you
  want; `-b` is the default choice.
- **Write what you say.** Print the text you are speaking as well, so the user has it if
  they miss a word or the audio fails.
- **Compose for the ear when the words are yours.** Speech runs ~150 words per minute, so
  keep your *own* spoken replies tight and skip code, paths and punctuation-heavy output.
  This is about what you choose to say — it is never a reason to shorten or skip text the
  user explicitly asked you to read.
- **Never read secrets aloud** — tokens, keys, passwords — even if they appear in the text
  you were asked to narrate. Skip them and say you did.
- Speech is written to a temp file and deleted after playing, unless `-o` or `--keep`.

### Keep phrases intact and select the language explicitly

Pass `--lang` for the language you are speaking so its voice and pronunciation memory
apply. Use normal punctuation and complete phrases. On local backends, every tone-tag
boundary may create a separate synthesis and conversion request; tags approximate
expression through rate and gain and do not guarantee natural emotional delivery.
Avoid tagging individual words or every sentence. For borrowed technical terms, use
the shared pronunciation dictionary instead of splitting the sentence between voices.
When the user reports robotic sound or latency, use the tuning skill and `tts calibrate`
to compare initial and repeated renders before adding more tags or changing settings.

## Cached speech and startup

Keep passing explicit --lang. An enabled audio cache and saved ending-silence setting
apply automatically to ordinary tts calls; do not manually add silence or bypass the
cache for routine speech. Use --no-cache for an intentional fresh comparison. For
slow first requests, check tts cache status and tts warm --help. Warming is silent;
keep-alive is bounded and consumes model memory. Do not start an indefinite process.
