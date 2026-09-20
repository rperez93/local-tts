# Local calibration, 20 September 2026

Measured on the configured machine using `tts calibrate --provider kokoro --provider rvc`.
English uses bm_george / Jarvis, US English af_heart / Cortana English, and Spanish
 ef_dora / Cortana Spanish. Preferences were not changed.

| Profile | Base render, repeated | RVC render, initial | RVC render, repeated |
| --- | ---: | ---: | ---: |
| English | 1.16 s | 11.15 s | 1.53 s |
| US English | 0.89 s | 3.54 s | 1.49 s |
| Spanish | 0.98 s | 3.20 s | 1.47 s |

The initial renders may include server or model loading; this is not an isolated
cold-start benchmark. All six repeated samples had zero hard-clipped PCM16 samples.
Leading/trailing silence ranged from 33 to 103 ms. These findings do not establish
naturalness or pronunciation accuracy. Audio and raw measurements are in
`/tmp/local-tts-calibration-uvuzcp3f/report.json` on the calibration machine.

A separate playback measurement used three 200 ms silent clips with the automatically
selected Windows player. Three launches took 4.24 s; one launch of the identical combined
600 ms signal took 1.59 s. This supports batching contiguous ready fragments. It does
not measure time to audible onset or prove that another player sounds cleaner. Raw
measurements: `/tmp/local-tts-player-benchmark-viq5wc4p/report.json`.

Changes supported by inspection and regression tests:

- Regional pronunciation overrides no longer depend on dictionary insertion order.
- Provider voice/model selection normalizes language case and underscores consistently.
- Ready same-format fragments share one player invocation without extra silence or
  waiting for future synthesis.
- `tts calibrate` retains samples and reports initial/repeated render measurements.
- Bundled agent instructions favor complete phrases, explicit language selection, and
  measured two-version comparisons rather than excessive tone tags.

Still to verify: perceived naturalness in all three configured profiles, technical-term
pronunciation improvements, any chosen per-language voice/conversion adjustments, and
refreshing installed agent skills after the tuning changes are finalized. No dictionary
pack or voice change has been declared calibrated merely from the objective metrics.

## Pronunciation and agent follow-up

The live phonemizer reproduced an isolated-word failure: `merge` and `merge.` both
returned the original text with `is_phonemes=False` despite a matching IPA entry.
The refreshed algorithm returns `mɜːdʒ` and `mɜːdʒ.` with `is_phonemes=True`. It also
prevents overlapping phrase/word entries from overwriting the same phoneme offsets
and avoids phonemizing sentences that contain no dictionary matches.

`tts pronounce` now puts a trial at the selected language scope, overriding an existing
scoped respelling in memory. Previously the trial could silently lose to the existing
entry. `--no-play` now retains both recordings and prints their directory, and substring
matches such as `cat` in `concatenate` are rejected.

The pronunciation candidate for `merge`, /mɜːdʒ/, comes from the UK pronunciation in
[Wiktionary](https://en.wiktionary.org/wiki/merge#Pronunciation), omitting the optional
affricate tie bar for the model's vocabulary. It is a trial, not a saved preference or
a claim that a UK pronunciation suits every language profile. All its phonemes were
accepted by the installed model. Spanish in-sentence recordings:
`/tmp/local-tts-pronounce-f4vg4w6a`; isolated-word recordings after the server refresh:
`/tmp/local-tts-pronounce-oglmihfn`.

The Kokoro server script was refreshed with its prior version retained as
`~/.local/share/kokoro-venv/kokoro_server.py.bak`. All five installed TTS skills in
`~/.agents/skills` now match the bundled versions. The installer uses Codex's
[native skill directory](https://learn.chatgpt.com/docs/build-skills), with regression
tests for migrating the old managed AGENTS.md section, preserving unrelated text and
a backup, reinstalling, uninstalling, and dry runs. No legacy block was present on this
machine, so this refresh did not modify its AGENTS.md.

Validation: 331 tests passed. Subjective voice comparison and persistence of any
preferred pronunciation candidates remain outstanding; the earlier Spanish A/B
question has not yet received an answer.

## Audible playback verification

The user reported hearing none of the original Windows-player comparisons. Successful
player exit codes were therefore insufficient evidence of audible playback. The user
confirmed listening on the same Windows machine and hearing the subsequent ffplay/WSLg
test. However, they reported prior ffplay problems in WSL and requested retrying
PowerShell, suggesting startup lag may explain the missed audio. The brief ffplay
default change was reverted to explicit `player=windows`. Voice and pronunciation
choices remain unchanged. The user confirmed hearing the timed PowerShell replay (6.72 s overall for a 4.95 s recording). PowerShell remains the preferred player; the Spanish voice comparison is now being repeated through that confirmed route.
Previous Windows startup timings describe process behavior; they do not establish
that the user heard those earlier tests.

## Confirmed Spanish preference

After hearing both samples through the confirmed PowerShell route, the user selected
B (the current Cortana Spanish RVC conversion) as more natural than A (base Kokoro).
Retained and recorded `es=rvc` in shared language settings. This supports keeping the
Spanish converted voice; it does not yet validate technical-term IPA candidates or
the two English profiles.

## Confirmed English preference

The user preferred the Jarvis voice in the English A/B comparison. Retained and
recorded `en=rvc`; the existing `rvc.language_models.en=jarvis` selects that voice.
This is a voice preference, not a claim that all pronunciation or prosody issues
are resolved. The separate US English profile remains under comparison.

## Confirmed US English preference

The user selected A (base Kokoro) over the Cortana RVC conversion for US English.
Saved `en-US=kokoro:af_heart`. English retains Jarvis and Spanish retains Cortana RVC.
This removes voice conversion from the US English path in accordance with the user's
listening preference; the repeated baseline render measured 0.89 s for Kokoro versus
1.49 s for the converted voice.

## Merge R-sound refinement

The user slightly preferred the IPA candidate but said its R was not crisp and clear.
The first candidate /mɜːdʒ/ used the non-rhotic UK pronunciation. Wiktionary lists
US /mɝd͡ʒ/, but the installed Kokoro vocabulary lacks ɝ. Its own tokenizer returns
`mˈɜːdʒ` for both en-us and en-gb, so simply changing the tokenizer language does not
add an explicit R here. A controlled trial adds the supported English approximant ɹ:
A /mɜːdʒ/, B /mɜːɹdʒ/. This is a model-compatible approximation for listening,
not a claim that the source dictionary transcribes the word that way. No candidate
is saved until this refinement is evaluated.

## Spanish R/RR diagnosis

The user reports that R and especially RR still need work. The installed phonemizer
produces `pˈeɾo` / `pˈero` for pero/perro and `kˈaɾo` / `kˈaro` for caro/carro.
The model vocabulary contains Spanish trill r, tap ɾ, and English approximant ɹ.
Thus a missing Spanish R token is not the explanation. A new comparison uses the
identical base waveform before and after RVC, preserving the current emphasis setting,
to isolate whether conversion weakens the consonants. No global R replacement or
pronunciation preference has been saved.

## Unattended follow-up completed

The user authorized further independent iteration while away. See
[the unattended report](unattended-calibration-2026-09-20.md) for 168 variants,
holdout results, the retained English retrieval adjustment, rejected Spanish trials,
and the persistent audio archive. Earlier outstanding listening notes above describe
past stages. Spanish R/RR remains a documented model/articulation limitation.
