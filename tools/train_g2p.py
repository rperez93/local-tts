#!/usr/bin/env python3
"""Train and measure a g2p language against a real phonemizer.

Run this on a machine that HAS a phonemizer; local-tts itself never does. It compares
`localtts.g2p` against that reference over a corpus, tells you where the rules are
wrong, and freezes what the rules cannot reach into a lexicon the package ships as data.

    # how good are the rules today?
    python tools/train_g2p.py --lang es-419 --corpus corpus/es.txt

    # see what to fix next, grouped so a single rule covers many words
    python tools/train_g2p.py --lang en-us --corpus corpus/en.txt --show 40

    # freeze the residue, so the words rules cannot reach still come out right
    python tools/train_g2p.py --lang en-us --corpus corpus/en.txt --freeze

Why both halves. Spanish is close to phonemic: rules reproduce espeak on every segment
and the lexicon stays empty. English is not -- "through" and "thought" share four
letters and sound nothing alike, and no rule recovers "colonel" from its spelling --
so there the lexicon carries the irregular words and the rules carry the rest.

Adding a language means writing `src/localtts/g2p/<base>.py` with `phonemes_for(word)`
and `phonemes(text, lexicon=None)`, then running this until the number stops moving.
A language with no rule module at all still works from a lexicon alone: `--freeze`
writes every word in the corpus, which is a coarse but honest start.
"""

import argparse
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from localtts import g2p                                        # noqa: E402

WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)


def reference(lang):
    """The phonemizer to match. kokoro-onnx's tokenizer, because that is what the model
    itself uses -- matching espeak directly would be matching something one step
    removed from what actually gets synthesized."""
    try:
        from kokoro_onnx.tokenizer import Tokenizer
    except ImportError:
        sys.exit("needs kokoro-onnx: run this from a venv that has it "
                 "(~/.local/share/kokoro-venv is the one the configure skill makes)")
    tok = Tokenizer()
    return lambda text: tok.phonemize(text, lang=lang).strip()


def words_of(text):
    return sorted({m.group(0).lower() for m in WORD.finditer(text)})


def strip_stress(ipa):
    return ipa.replace("ˈ", "").replace("ˌ", "")


def shape(word):
    """A crude signature for grouping mismatches, so one rule can be seen to cover
    many words at once rather than reading 200 lines one by one."""
    return "".join("V" if unicodedata.normalize("NFD", c)[0] in "aeiou" else "C"
                   for c in word)


def measure(lang, words, sentences, ref):
    exact = segments = 0
    misses = []
    for word in words:
        want, got = ref(word), g2p.phonemes_for(word, lang)
        if got is None:
            misses.append((word, want, "(no rules)"))
            continue
        if got == want:
            exact += 1
            segments += 1
        else:
            if strip_stress(got) == strip_stress(want):
                segments += 1
            misses.append((word, want, got))
    sent_ok = sum(1 for s in sentences if g2p.phonemes(s, lang) == ref(s))
    return exact, segments, sent_ok, misses


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lang", required=True, help="language tag, e.g. es-419, en-us")
    parser.add_argument("--corpus", required=True, help="a plain text file, one or more lines")
    parser.add_argument("--show", type=int, default=20, help="how many mismatches to print")
    parser.add_argument("--freeze", action="store_true",
                        help="write the mismatching words into the shipped lexicon")
    parser.add_argument("--freeze-all", action="store_true",
                        help="write EVERY word, for a language with no rule module yet")
    parser.add_argument("--sentence-forms", action="store_true",
                        help="report words whose sentence form differs from their "
                             "isolated one -- the reduced and demoted function words")
    args = parser.parse_args()

    text = open(args.corpus, encoding="utf-8").read()
    words = words_of(text)
    sentences = [line.strip() for line in text.split("\n") if line.strip()]
    ref = reference(args.lang)

    exact, segments, sent_ok, misses = measure(args.lang, words, sentences, ref)
    total = len(words) or 1
    print("%s -- %d words, %d sentences" % (args.lang, len(words), len(sentences)))
    print("  segments (the sounds)      %4d/%d  %5.1f%%" % (segments, total, 100*segments/total))
    print("  words with stress marks    %4d/%d  %5.1f%%" % (exact, total, 100*exact/total))
    print("  whole sentences            %4d/%d  %5.1f%%"
          % (sent_ok, len(sentences) or 1, 100*sent_ok/(len(sentences) or 1)))

    if misses and args.show:
        print("\n  mismatches, grouped by shape -- one rule often covers a whole group:")
        groups = {}
        for word, want, got in misses:
            groups.setdefault(shape(word), []).append((word, want, got))
        for sig, rows in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:args.show]:
            word, want, got = rows[0]
            extra = "  (+%d more like it)" % (len(rows) - 1) if len(rows) > 1 else ""
            print("    %-14s want %-22s got %-22s%s" % (word, want, got, extra))

    if args.sentence_forms:
        # A word said alone is not the word said in a sentence: English "to" is tuː
        # alone and tə in running speech, Spanish "el" is ˈel alone and el in a phrase.
        # Discovering the list beats guessing it, and it is the difference between every
        # word being right and the sentence being right.
        print("\n  words whose sentence form differs from their isolated one:")
        seen = {}
        for line in sentences:
            got_ref = ref(line).split()
            words_in = [m.group(0) for m in WORD.finditer(line)]
            if len(got_ref) != len(words_in):
                continue                      # alignment is not one-to-one; skip
            for word, in_sentence in zip(words_in, got_ref):
                alone = ref(word)
                bare = in_sentence.strip(".,;:!?")
                if alone != bare and word.lower() not in seen:
                    seen[word.lower()] = (alone, bare)
        for word, (alone, bare) in sorted(seen.items())[:40]:
            print("    %-12s alone %-16s in a sentence %s" % (word, alone, bare))
        print("    (%d words)" % len(seen))

    if args.freeze or args.freeze_all:
        chosen = words if args.freeze_all else [w for w, _, _ in misses]
        table = {w: ref(w) for w in chosen}
        name = args.lang.strip().lower().replace("-", "_")
        path = os.path.join(g2p.LEXICON_DIR, "%s.json" % name)
        payload = {
            "_note": ("Frozen from %s by tools/train_g2p.py. Data, not code: local-tts "
                      "has no runtime dependency and cannot regenerate this itself."
                      % args.corpus),
            "language": args.lang,
            "words": table,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=0, sort_keys=True)
        print("\n  froze %d words -> %s" % (len(table), os.path.relpath(path, ROOT)))


if __name__ == "__main__":
    main()
