---
name: local-tts-phonetics
description: Find the right pronunciation for one word and make it stick — look the transcription up on the internet (Wiktionary and friends), hear it with `tts pronounce`, keep the winner in the pronunciation dictionary. TRIGGER whenever a specific word comes out wrong rather than the voice sounding wrong: "it mispronounces my name", "it says GIF wrong", "how do I make it say <word> properly", "it reads pull request in Spanish", "lo pronuncia mal", "dice mal mi nombre", "cómo hago que pronuncie X". Also for tuning an /IPA/ entry that is already there but not quite right, for deciding between respelling and IPA, and for wiring a script that generates transcriptions (`phonetics_hooks`) when there are too many words to type by hand. For speech where *every* word sounds wrong — robotic, too fast, noisy — use local-tts-tune instead; for nothing playing at all, local-tts-configure.
---

# Getting one word said properly

This is for a **named word** coming out wrong: a person's name, a brand, an acronym, a
borrowed term inside another language. If everything sounds wrong, it is not this — pacing
and artifacts are `local-tts-tune`, and silence is `local-tts-configure`.

## The rule

**Never invent a transcription from spelling.** IPA is not a spelling reform, and a
guessed transcription fails in the way that is hardest to debug: the word still comes out,
just mangled, and nothing anywhere says why. Look it up, hear it, keep it — in that order,
every time.

## The loop

### 1. Decide which kind of entry this is

Two kinds live in the same `pronunciations` table, and picking wrong wastes the rest of
the loop:

| Use | When | Example |
| --- | --- | --- |
| **respelling** | the word just needs re-spelling in the *same* language, and it must work on every backend | `pronunciations.kubectl="cube cuddle"` |
| **`/IPA/`** | the word belongs to *another* language, or a respelling cannot get it close | `pronunciations.'pull request'=/pˈʊl ɹᵻkwˈɛst/` |

A respelling works everywhere and needs no server. IPA needs a backend with a phonemizer:
kokoro with a **running, current** server, or rvc over a kokoro base. Check before
promising anything:

```bash
tts check | grep phonetics      # which backends accept phonemes right now
tts servers                     # and whether that server's script is this version
```

If the answer is "no backend here accepts phonemes", stop and fix that first (the
persistent server in `local-tts-configure`) or fall back to a respelling. Do not write an
IPA entry that nothing can read and report success.

### 2. Look the transcription up — do not derive it

In order of how much they can be trusted:

1. **Wiktionary** — `https://en.wiktionary.org/wiki/<word>`. Read the pronunciation under
   the heading for the language the word comes *from*, which is the whole point: the
   English transcription is what makes "pull request" sound English inside Spanish. Prefer
   the IPA in slashes (phonemic) over the one in brackets (narrow phonetic).
2. **A dictionary for that language** — Merriam-Webster, DLE/RAE, Larousse, Duden. Some
   print a respelling rather than IPA; that is still useful as the respelling entry.
3. **`espeak-ng`**, when the word is not in any dictionary (a made-up product name, a
   username):

   ```bash
   espeak-ng --ipa -q -v en "pull request"
   espeak-ng --ipa -q -v es "jarvis"
   ```

   This is a machine's guess from spelling, so treat it as a *starting point* to hear,
   never as the answer.
4. **Ask the user to say it**, in words: "JAR-viss, stress on the first part?" A person's
   own name is theirs, and there is no source of truth on the internet for it.

For a name, ask before searching the web for a person: pronunciation pages for individuals
are often about someone else with the same name.

### 3. Hear it

```bash
tts pronounce "pull request" --lang es --ipa "/pˈʊl ɹᵻkwˈɛst/"
```

It renders the sentence twice — as it is said now, then with the candidate — plays both
back to back, and prints the command that keeps the winner. In context, when the word
alone is not the problem:

```bash
tts pronounce croissant --lang es --sentence "quiero un croissant, por favor"
```

Read the `phonemes` line before you listen to anything:

| It says | What it means |
| --- | --- |
| `every one is in this model's vocabulary` | the model can say what you asked for |
| `no token for 'r', "'"` | those characters are **dropped** and the word comes out mangled — fix the transcription, do not judge it by ear |
| `this backend cannot say which it has a token for` | the server predates `/vocab`; run `tts servers --refresh` |

The first render includes any server start, so its time is not comparable with the
second's. Neither number is the point here — the ear is.

### 4. Fix what the phonemes line named

Nearly every rejected character is one of these, and they look right in a terminal:

| Wrote | Wanted | |
| --- | --- | --- |
| `r` | `ɹ` | English *r* is not a trill |
| `'` | `ˈ` | primary stress is U+02C8, not an apostrophe |
| `,` | `ˌ` | secondary stress is U+02CC |
| `:` | `ː` | length is U+02D0, not a colon |
| `g` | `ɡ` | IPA *g* is U+0261 |
| `n~`, `~` | `ɲ`, a combining tilde | Spanish *ñ* and nasal vowels |

Also: no slash inside the value (`/a/ y /b/` is refused), and no stray spaces at the ends.

### 5. Keep it, scoped

```bash
tts config --set 'pronunciations.pull request=/pˈʊl ɹᵻkwˈɛst/'        # every language
tts config --set 'pronunciations.es:pull request=/pˈʊl ɹᵻkwˈɛst/'     # Spanish only
tts config --set 'pronunciations.jarvis='                              # remove it
```

`tts pronounce` prints exactly this line, with the `--lang` you used already folded in;
prefer copying it over composing your own. Scope to a language when the same word is said
differently in two — an exact tag beats its base language, so `es-MX` wins over `es`.

Then confirm the whole sentence, not the word alone, because a transcription that is right
in isolation can still land wrong in a phrase:

```bash
tts --lang es "Ya subí el pull request al repositorio."
```

### 6. Report what you changed

Say the word, the transcription, its scope, and where it came from ("Wiktionary's English
entry"). A user who knows the source can correct it; one handed only `/pˈʊl ɹᵻkwˈɛst/`
cannot.

## When there are too many words to type

If the answers are *generated* — a lexicon, a house glossary of product names, a real
transcriber — they do not belong in the dictionary at all. Point the user at
`phonetics_hooks`: an executable that receives one utterance's table on stdin as JSON and
prints the table to use.

```bash
tts config --set phonetics_hooks='["~/bin/lexicon.py"]'
```

```python
#!/usr/bin/env python3
import json, sys
call = json.load(sys.stdin)   # {"text", "lang", "provider", "phonetics"}
table = call["phonetics"]
for word, ipa in my_glossary.items():
    if word in call["text"].lower():
        table[word] = ipa
print(json.dumps({"phonetics": table}))
```

Worth saying out loud when you set one up: it runs with the user's own privileges, it can
only change *how a word is transcribed* and never what is said, and any failure (bad exit,
non-JSON output, slower than `phonetics_hook_timeout`) leaves the table alone with one line
on stderr. `tts check` counts the hooks it will run. It is unrelated to `tts hooks`, which
is the status-bar hook.

## Failure modes worth recognizing

| Symptom | Cause | Fix |
| --- | --- | --- |
| The entry does nothing at all | the backend has no phonemizer | `tts check`'s phonetics line; start kokoro's server |
| It did nothing, and the server *is* up | the server's script predates phonetics | `tts servers --refresh` |
| The word is mangled rather than merely wrong | a phoneme the model has no token for | `tts pronounce` names the character |
| Right in isolation, wrong in a sentence | stress, or a respelling fighting the IPA | check for a second entry for the same word |
| Correct in one language, wrong in another | a bare key applies to every language | scope it with `<lang>:<word>` |
| An `<en>…</en>` span in old text | removed, not read aloud | rewrite it as a dictionary entry |

## Save comparisons for review

`tts pronounce WORD --lang CODE --ipa '/…/' --no-play` retains both recordings and
prints their directory. The trial overrides the current entry only in memory, at the
selected language scope. It never saves the candidate automatically. A vocabulary
check establishes token support, not pronunciation accuracy; compare the recordings
before persisting the winner. Use `tts servers --refresh` after upgrading if isolated
word trials or overlapping phrase entries appear to ignore the dictionary.

## Distinguish Spanish R/RR from English R

Spanish tap `ɾ`, Spanish trill `r`, and English approximant `ɹ` are different sounds.
Check the actual model vocabulary and phonemizer before replacing any of them. The
installed Kokoro model supports all three; an ASCII `r` is not inherently an error
for Spanish. If pero/perro already produce distinct tap/trill phonemes, adding the
same dictionary entries will not repair weak articulation. Compare the identical
base waveform before and after conversion to locate the loss, then test conversion
settings or the base voice one at a time. Do not globally rewrite Spanish R as English R.

## Cache-aware trials

Pronunciation trials and calibration render directly, so they do not reuse cached
CLI speech. Normal speech cache keys include pronunciation entries and model settings.
After replacing a remote model at an unchanged URL, change cache_revision or clear
the cache. Token support and recognizer scores still do not establish naturalness.
