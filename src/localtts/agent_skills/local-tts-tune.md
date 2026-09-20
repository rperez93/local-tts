---
name: local-tts-tune
description: Tune how local-tts *sounds* — pacing, pauses, emphasis, the gap between fragments, which voice reads a borrowed word, and the noise/robotic artifacts that come from the wrong player or a missing ffmpeg. TRIGGER whenever the user says speech sounds wrong rather than being broken: "it sounds robotic", "too fast", "too slow", "weird", "noisy", "crackly", "the pauses are too long", "it sounds choppy", "make it sound better/more natural", "suena raro", "habla muy rápido", "suena robótico", or when they ask to tune, adjust or improve voice quality. Diagnoses by measurement first, changes one thing at a time, and confirms by ear only where a measurement cannot decide.
---

# Tuning how `local-tts` sounds

This is for speech that **works but sounds wrong**. If nothing plays at all, or a backend
is missing, use `local-tts-configure` instead — that is a different problem.

## The rule that makes this work

**Measure before you ask, and change one thing at a time.** Most "it sounds bad" reports
have a cause you can find without the user's ears, and every listening test you can avoid
is one you should. When you do need their ear, play exactly two versions and ask which —
never three, never "how does this sound now" with nothing to compare against.

Never change several settings and then ask. If it improves you will not know which one did
it, and if it gets worse you will not know what to undo.

## Start here: what does `tts check` already know?

```bash
tts check
```

Three lines at the bottom answer most of this before you touch anything:

```
players     : powershell.exe, ffplay  -> using powershell.exe
tone shaping: ffmpeg atempo (best quality)
streaming   : on -- each fragment plays as it is synthesized
```

| Line says | Then |
| --- | --- |
| `tone shaping: built-in WSOLA` | ffmpeg is missing. Every tone tag that changes pacing is retimed in pure Python. This can introduce artifacts when rate-changing tags are used. Compare plain and tagged speech before attributing the problem to retiming; install ffmpeg if the evidence supports it. |
| `players` lists more than one | The `-> using X` half tells you which is actually in play. A wrong pick here is the most common cause of "noisy" or "crackly". |
| `player tuning:` present | Someone has already tuned this machine; read it before adding more. |

## Symptom → cause

Work down this table. The first column is what the user actually says.

| "It sounds…" | Most likely cause | First thing to try |
| --- | --- | --- |
| robotic, buzzy, warbly | no ffmpeg, so tone tags retime in the fallback stretcher | install ffmpeg (ask) |
| noisy, crackly, distorted | the wrong audio player for this machine | the two-player A/B below |
| like the wrong language / wrong accent | language memory or per-language voice | `tts languages`, `<provider>.language_voices` |
| too fast / too slow | `delivery.speed`, or the provider's own rate | `speed` in `rvc.delivery.<lang>` |
| choppy, gappy, "stalls between words" | `pause_ms` / `trim_ms`, or fragments carrying their own dead air | the pacing section below |
| flat, no emotion | the selected voice/model or a backend with limited expressive control | `local-tts-speak` covers writing tags |
| a borrowed English word said with the wrong phonetics | language tags off, or that language not configured | the borrowed-word section below |
| a borrowed word said with the wrong phonetics | no IPA entry for it in the dictionary | `pronunciations.<word>=/…/` |

## Isolating a noisy player (2 plays, no guessing)

Noise can originate in synthesis, voice conversion, retiming, or playback. Isolate the
player by playing the same file two ways. Use **plain untagged text**, so tone shaping is out of the picture:

```bash
tts -p piper --no-play -o /tmp/ab.wav "The quick brown fox jumps over the lazy dog."
ffplay -nodisp -autoexit -loglevel error /tmp/ab.wav
powershell.exe -NoProfile -NonInteractive -Command "(New-Object Media.SoundPlayer '$(wslpath -w /tmp/ab.wav)').PlaySync()"
```

- **Only one is noisy** → it is the player. `tts config --set player=windows` (or
  `player=ffplay`, whichever was clean). Done; stop here.
- **Both noisy** → not the player. Continue down the table.
- **Neither noisy** → the problem is specific to tagged or mixed-language text, not to
  plain speech. Go to the pacing or borrowed-word sections.

If a player is *nearly* right, tune it per machine rather than abandoning it — but verify
by ear, since these are machine-specific and some combinations make things worse:

```bash
tts config --set 'player_args.ffplay=-af aresample=48000'
tts config --set player_env.PULSE_LATENCY_MSEC=90
tts config --set player_args.ffplay=          # empty removes it
```

## Pacing: speed, pauses, and dead air

These live per language, so Spanish and English can differ. Measure the selected voice and use listening feedback
to choose the rate and gaps; do not impose a language-wide speed assumption:

```bash
tts config --set 'rvc.delivery.es={"speed": 1.0, "pause_ms": 45, "pause_tone_ms": 130, "trim_ms": 10}'
```

| Setting | What it controls | Raise it when | Lower it when |
| --- | --- | --- | --- |
| `speed` | overall rate, folded into the base provider's own rate control | too slow | too fast |
| `pause_ms` | gap between fragments delivered the same way | words run together | it stalls between phrases |
| `pause_tone_ms` | gap where the tone changes — the breath | a mood change lands too abruptly | tagged speech feels disjointed |
| `trim_ms` | silence left at each fragment edge before the pause is applied | onsets sound clipped | gaps are longer than `pause_ms` says |

**`trim_ms` is the one people miss.** Every fragment arrives with its own lead-in and tail
— the synthesizer's padding, plus whatever conversion adds. Joined, eight fragments carry
eight lots of it, so the real gap is that dead air *plus* your pause, and `pause_ms`
controls neither. Measure it rather than guessing:

```bash
python3 - <<'EOF'
import wave, array, sys
path = "/tmp/out.wav"
with wave.open(path, "rb") as w:
    a = array.array("h"); a.frombytes(w.readframes(w.getnframes())); rate = w.getframerate()
peak = max(abs(v) for v in a); thr = max(96, int(peak * 0.02))
lead = next(i for i, v in enumerate(a) if abs(v) >= thr)
tail = next(i for i, v in enumerate(reversed(a)) if abs(v) >= thr)
print("lead %.3fs  tail %.3fs  total %.2fs" % (lead/rate, tail/rate, len(a)/rate))
EOF
```

A useful whole-utterance check: synthesize the same sentence with and without tags and
compare durations. Tagged should be modestly longer (borrowed words genuinely take longer
said correctly); **much** longer means dead air is accumulating, and `trim_ms` is the knob.

## Borrowed words in another language

```console
$ tts --lang es "Terminé el pull request ya"
```

If the borrowed words still sound Spanish, check in this order:

1. **Is there an IPA entry?** `tts config --show | grep -A5 pronunciations`. A borrowed
   word keeps its own sound because the dictionary holds its transcription:
   `tts config --set 'pronunciations.pull request=/pˈʊl ɹᵻkwˈɛst/'`.
2. **Can the backend use it?** `tts check` prints a `phonetics:` line naming which
   backends accept IPA and which ignore it. Only a backend with a phonemizer can --
   kokoro with `server_url`, and rvc over a kokoro base. If theirs cannot, the fix is
   the persistent server (see `local-tts-configure`), not another entry.
3. **Is the transcription right?** `tts pronounce "pull request" --lang es --ipa "/…/"`
   plays it as it is said now and as the candidate would say it, and names any phoneme
   the model has no token for -- one that is dropped in silence otherwise, leaving the
   word mangled rather than merely wrong. `espeak-ng --ipa -q -v en "pull request"` is a
   starting point when no dictionary has the word. Use the language the word comes
   *from*.

If the problem is **one word** rather than how everything sounds, that whole loop --
looking the transcription up, hearing it, keeping it -- is the `local-tts-phonetics`
skill; hand it over rather than working through it here.

A sound the model was never trained on comes out as the nearest one it has -- that is a
limit of the voice, not of the entry, and no dictionary change fixes it.

## Emphasis

```bash
tts config --set 'rvc.delivery.es={"emphasis_lengthen": 2}'
```

N IPA length marks on the vowel carrying primary stress (`kˈasa` → `kˈaːsa`). Needs a
kokoro base with the persistent server, which is where the phonemizer lives. The effect is
**subtle on a long sentence and clearest on short, emphatic spans** — measured on an
isolated word, 0.576s plain, 0.640s with one mark, 0.661s with two. Do not raise it past 2
without the user actually asking; verify by ear before recommending it, and if they cannot
hear a difference on a full paragraph, that is expected rather than a fault.

## Pronunciation, not pacing

If a specific *word* is wrong — a name, a product, an acronym — no amount of pacing fixes
it. That is the dictionary, and it is much cheaper than changing voices:

```bash
tts config --set pronunciations.jarvis="JAR-viss"
tts config --set pronunciations.kubectl="cube cuddle"
tts config --set pronunciations.es:jarvis="yarvis"   # Spanish only
```

## Automatic first pass

Before involving the user at all, run these and act on anything conclusive:

```bash
tts check                                  # ffmpeg? which player? streaming?
tts config --show | grep -A12 delivery     # what is already set
tts languages                              # is the language even recorded
```

Then synthesize one representative sentence in the user's own language — ideally one they
complained about, verbatim — and measure it: total duration, edge silence, fragment count
via `--dry-run`. Fix anything the measurements settle outright:

- `tone shaping: built-in WSOLA` → offer ffmpeg
- edge silence much larger than `trim_ms` → lower `trim_ms` is *not* the fix; the trim is
  already applied at join time, so a large residue means the fragment is genuinely quiet
  at the edges — look at the base voice instead
- tagged duration far longer than untagged → dead air, not pronunciation
- a fragment that is only punctuation in `--dry-run` → report it; that is a bug, not a
  setting

Only after that, and only for what measurement cannot decide, run one two-way listening
test. State plainly what changed between the two.

## Finish

Re-run `tts check`, play one real sentence in the user's language, and tell them exactly
which settings you changed and what each one does — they will want to undo or extend them
later, and a setting they cannot name is a setting they cannot keep. If you changed
nothing because the measurements were clean, say that too; "I could not reproduce it, here
is what I measured" is a real answer.

## Record a reproducible baseline

Use `tts calibrate --provider kokoro --provider rvc` when those local backends are
configured. It saves initial and repeated renders of every remembered language and
prints the report path; it does not play or change settings. Select a single language
and the user's actual sentence with `--lang es --text '...'` for pronunciation work.
Review errors as well as timing, silence and clipping. First-fragment time measures
readiness, not audible playback latency. Warmup can reduce initial model-loading delay;
it cannot repair pronunciation or establish naturalness. Do not treat low latency,
no clipping, or a passing test suite as proof that speech sounds human.

Compare the same sentence in the base and converted voices before attributing robotic
sound to a missing dictionary. Change one variable and play exactly two versions.
Persist only supported pronunciation entries or settings backed by the comparison.

## Startup latency and abrupt endings

Separate cold model loading, synthesis, cache lookup, player startup, and playback
queueing. Compare --no-play renders before blaming the player. tts warm --lang CODE
loads the configured model; --keep-alive SECONDS keeps it resident for a bounded period.
tts cache status shows bounded audio-cache usage; --verbose reports hit/miss.
A cache hit does not prove playback has begun or that the user heard it.
For endings, compare the exact WAV with and without trailing silence. If the padded
version is preferred, save ending_silence_ms=350 (or the measured preferred value).
It applies once to the final WAV fragment; it cannot restore missing synthesized sounds.
