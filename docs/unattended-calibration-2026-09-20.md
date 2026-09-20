# Unattended pronunciation calibration — 20 September 2026

The user authorized independent iteration while away. Tested 168 recorded variants
across Spanish, English and US English, including a separate set of fresh sentences.
Kept the user's selected voices: Spanish Cortana RVC, English Jarvis RVC, and US
English Kokoro af_heart. Playback remains Windows PowerShell.

## Decision

Saved `rvc.conversion.en={"index_rate":0.35}`; the server's previous retrieval rate
was 0.88. This reduced English recognition errors on both the initial sound suite
and fresh sentences. It retains the Jarvis model but may let more of the source
voice's timbre through. This is a reversible intelligibility adjustment, not a
verified improvement in naturalness. Spanish retains 0.88, protect 0.20, speed 1.0,
and emphasis_lengthen 2. No pronunciation dictionary entries were added.

To undo only the new English adjustment:

```sh
tts config --set rvc.conversion.en=
```

## Method and results

Nine Spanish prompts covered tap/trill contrasts, vowels, stops, fricatives,
nasals/laterals, clusters, stress and questions. Six English prompts covered R/L,
vowels, stops, fricatives, clusters and questions. Six new holdout sentences added
R with different vowels, pera/perra, initial R, ñ, ch, ll, j and connected speech.
Conversion variants used identical source WAVs wherever the base was held fixed.

An isolated optional faster-whisper installation ran multilingual `small` locally
on CPU/int8, with no reference prompt or audio upload. Scores count normalized
word/character edit errors; punctuation and case are ignored, accents preserved.
Character scores omit word spaces. Numerals are not expanded. Recognition can
hallucinate and does not measure trill quality, speaker identity or naturalness.

| Language / set | RVC retrieval rate | Word errors | Character errors |
| --- | ---: | ---: | ---: |
| Spanish initial | 0.88 | 14 / 128 | 12 / 581 |
| Spanish initial | 0.35 | 10 / 128 | 11 / 581 |
| Spanish holdout | 0.88 | 3 / 54 | 3 / 233 |
| Spanish holdout | 0.35 | 4 / 54 | 8 / 233 |
| English initial | 0.88 | 13 / 81 | 29 / 347 |
| English initial | 0.35 | 11 / 81 | 19 / 347 |
| English holdout | 0.88 | 3 / 47 | 4 / 221 |
| English holdout | 0.35 | 0 / 47 | 0 / 221 |
| US English base | no conversion | 0 / 81 | 0 / 347 |

Spanish lower retrieval lost the pera/perra distinction on a holdout where the
current settings preserved it. This outweighed the small initial aggregate gain.
English 0.65 also helped but introduced “rarely” → “really” on the holdout.
English protect=0 was less effective than the chosen retrieval adjustment.

Other rejected trials: Spanish speed 0.9, added trill length, neutral emphasis,
Kokoro em_alex, and Piper es_MX-claude-high / es_AR-daniela-high as the source for
Cortana. None consistently outperformed the current converted Spanish voice.
Piper Lessac feeding Jarvis produced a long recognizer repetition on the stops
sample; this cannot be interpreted as a literal transcript or reliable score.
No alternative engine or downloaded trial voice was made the default.

## Remaining R/RR limitation

Kokoro already emits distinct tap `ɾ` and trill `r` phonemes, and supports both.
Weak articulation is therefore not repaired by adding identical dictionary entries.
Both the base and conversion can lose contrasts in these samples. The tests do
not establish a reliable RR fix. English `ɹ` must not replace Spanish R globally.
The earlier “merge” candidates remain unsaved because feedback was mixed.

## Software fixes and reproducibility

RVC now accepts per-language pitch, index_rate and protect overrides. The refreshed
server advertises support, validates ranges, applies overrides under its lock and
restores startup values after every request, including failures. Previously,
index/protect request values were ignored and pitch could leak into later calls.
Old servers fail explicitly when advanced overrides are requested.

`tts calibrate --suite sounds --lang es --provider kokoro --provider rvc` retains
recordings and metrics without playing or changing preferences. Optional scripts in
`tools/` reproduce controlled experiments; only the ASR script requires extra
packages, installed separately from the dependency-free CLI.

All recordings and full reports are preserved at:
`~/.local/share/local-tts/calibration/2026-09-20-unattended/`.
The four subdirectories are `local-tts-sound-sweep-01`, `-02`, `-03`, and
`local-tts-sound-holdout`; each contains `report.json` and `asr.json` with absolute
recording paths. A pre-adjustment config backup is in the archive root.

Useful retained comparisons: the holdout's `en-stops-current.wav` versus
`en-stops-index035.wav`, and `es-contrasts-current.wav` versus
`es-contrasts-index035.wav`. Playback was intentionally not repeated while the
user was away.

References: [RVC retrieval guidance](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/wiki/FAQ-%28Frequently-Asked-Questions%29),
[RVC conversion controls](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/blob/main/docs/en/cli.md),
[local recognizer](https://github.com/SYSTRAN/faster-whisper).

Validation: 340 tests passed in 56.27 seconds with localhost socket access for the
mock servers. An initial sandboxed run could not create those sockets; the permitted
rerun passed. `git diff --check` is clean. `tts check` reports Windows playback,
ffmpeg tone shaping, and both running servers with current scripts. The real CLI
rendered a final English holdout sentence using the persisted setting without playback.

## Listening feedback after return

The user can distinguish pera/perra in the retained Spanish voice and prefers Jarvis
B (index_rate 0.35). These are listening confirmations, not just ASR findings.
They report abrupt playback endings. The English comparison WAV has approximately
105 ms of trailing near-silence and ends at sample zero; the fresh Spanish example
has approximately 53 ms and a final PCM16 value of -9. A separate playback trial
adds 350 ms of silence to an unchanged copy of the Spanish recording. This is a
candidate mitigation pending listening feedback, not yet a permanent setting.

The user preferred the padded ending on replay. Saved `ending_silence_ms=350`.
The CLI applies this once at the end of saved WAVs and to a private copy of the
final streamed fragment before publication. Earlier fragments are unchanged and
are not held back. Reset with `tts config --set ending_silence_ms=0`.

Ending-padding validation: 343 regression tests passed. The real CLI's confirmation
recording `/tmp/local-tts-q_glra8p.wav` contains approximately 390 ms of trailing
near-silence, including the added 350 ms.
