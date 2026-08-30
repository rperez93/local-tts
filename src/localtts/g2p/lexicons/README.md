# Frozen lexicons

Data, not code. Each file is a word → IPA table produced by `tools/train_g2p.py` on a
machine that has a real phonemizer, and read here as JSON.

local-tts has no runtime dependency, so it cannot regenerate these itself. That is the
whole reason they exist: a language whose spelling does not predict its sounds needs its
irregular words written down.

| File | Words | Why it exists |
| --- | --- | --- |
| `en_us.json` | 227 | English is not phonemic. "through" and "thought" share four letters and sound nothing alike; "colonel" keeps one sound from its spelling. Rules reach about a quarter of running text on their own. |

Spanish ships no lexicon: its rules reproduce the reference on every segment, so there
would be nothing to put in one.

## Regenerating, or adding a language

```bash
# how good are the rules today, and what should be fixed next
python tools/train_g2p.py --lang en-us --corpus corpus/en.txt --show 30

# which words change shape inside a sentence, rather than guessing
python tools/train_g2p.py --lang en-us --corpus corpus/en.txt --sentence-forms

# freeze what the rules cannot reach
python tools/train_g2p.py --lang en-us --corpus corpus/en.txt --freeze
```

A language with no rule module works from a lexicon alone (`--freeze-all` writes every
word in the corpus). That is coarse — it only knows the words it was given — but it is
honest, and it is a real starting point before writing any rules.
