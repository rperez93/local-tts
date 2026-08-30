"""Grapheme to phoneme for English, in pure Python.

English is not phonemic, and this module does not pretend otherwise. "through" and
"thought" share four letters and sound nothing alike; "colonel" has no phoneme in
common with how it is spelled after the first. Rules cannot recover those, and no
amount of iterating makes them.

So the work is split. Rules handle what is regular -- the great majority of running
text -- and a frozen lexicon carries the words that are not. The lexicon is data,
generated once by `tools/train_g2p.py` on a machine with a real phonemizer, so nothing
here imports one.

Read the accuracy in that light: the number that matters is rules *plus* lexicon, and
the rules' own share is reported separately so it is clear how much the lexicon is
doing.
"""

import re

VOWELS = "aeiouy"

#: Function words carry no stress of their own in running speech, the same way they do
#: not in Spanish. Cheap to list and it removes a whole class of mismatch.
UNSTRESSED_WORDS = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "but",
    "as", "is", "was", "were", "be", "been", "am", "are", "he", "she", "it",
    "we", "they", "you", "i", "his", "her", "its", "our", "their", "your", "my",
    "that", "this", "with", "from", "by", "if", "so", "than", "then",
}

#: Longest first, so "tion" wins over "ti" and "igh" over "gh". Order is the whole
#: mechanism: each is tried in turn against the remaining text.
DIGRAPHS = [
    ("ought", "ˈɔːt"), ("aught", "ˈɔːt"),
    ("ui", "ɪ"), ("ea", "iː"), ("ew", "uː"), ("oe", "oʊ"),
    ("tion", "ʃən"), ("sion", "ʒən"), ("ture", "tʃɚ"), ("ough", "ˈʌf"),
    ("igh", "ˈaɪ"), ("eigh", "ˈeɪ"), ("dge", "dʒ"), ("tch", "tʃ"),
    ("ch", "tʃ"), ("sh", "ʃ"), ("th", "θ"), ("ph", "f"), ("wh", "w"),
    ("ck", "k"), ("ng", "ŋ"), ("qu", "kw"), ("wr", "ɹ"), ("kn", "n"),
    ("gn", "n"), ("mb", "m"), ("ee", "iː"), ("ea", "iː"), ("oo", "uː"),
    ("ou", "aʊ"), ("ow", "aʊ"), ("oi", "ɔɪ"), ("oy", "ɔɪ"), ("au", "ɔː"),
    ("aw", "ɔː"), ("ai", "eɪ"), ("ay", "eɪ"), ("oa", "oʊ"), ("ie", "iː"),
    ("ei", "iː"), ("ar", "ɑːɹ"), ("or", "ɔːɹ"), ("er", "ɚ"), ("ir", "ɜː"),
    ("ur", "ɜː"), ("all", "ˈɔːl"),
]

SINGLES = {
    "a": "æ", "e": "ɛ", "i": "ɪ", "o": "ɑː", "u": "ʌ", "y": "i",
    "b": "b", "c": "k", "d": "d", "f": "f", "g": "ɡ", "h": "h", "j": "dʒ",
    "k": "k", "l": "l", "m": "m", "n": "n", "p": "p", "r": "ɹ", "s": "s",
    "t": "t", "v": "v", "w": "w", "x": "ks", "z": "z",
}


def _silent_e(word):
    """A final e that is not pronounced, and lengthens the vowel before it."""
    return len(word) > 3 and word.endswith("e") and word[-2] not in VOWELS


def phonemes_for(word, lexicon=None):
    """IPA for one word. The lexicon wins: it exists for what rules get wrong."""
    lower = word.strip().lower()
    if lexicon and lower in lexicon:
        return lexicon[lower]
    if not lower:
        return ""

    text = lower
    if _silent_e(text):
        text = text[:-1]
    # A doubled consonant is one sound: "letter" is lˈɛɾɚ, not lˈɛttɚ.
    text = re.sub(r"([bcdfglmnprstz])\1", r"\1", text)

    out, i = [], 0
    while i < len(text):
        for spelling, sound in DIGRAPHS:
            if text.startswith(spelling, i):
                out.append(sound)
                i += len(spelling)
                break
        else:
            char = text[i]
            if char == "c" and i + 1 < len(text) and text[i+1] in "eiy":
                out.append("s")
            elif char == "g" and i + 1 < len(text) and text[i+1] in "eiy":
                out.append("dʒ")
            elif char == "s" and 0 < i < len(text) - 1 and text[i-1] in VOWELS \
                    and text[i+1] in VOWELS:
                out.append("z")            # "rose", not "roce"
            else:
                out.append(SINGLES.get(char, char))
            i += 1

    ipa = "".join(out)
    # Intervocalic t and d flap in American English: "letter" -> lˈɛɾɚ.
    ipa = re.sub(r"(?<=[ɑæɛɪiʌʊuoɔeaəɚɜ])[td](?=[ɑæɛɪiʌʊuoɔeaəɚɜ])", "ɾ", ipa)
    # One primary stress per word. Rules cannot place English stress reliably -- it is
    # lexical, not positional -- so the first vowel is the honest default, and the
    # lexicon corrects the words where it matters.
    if lower in UNSTRESSED_WORDS:
        return ipa.replace("ˈ", "").replace("ˌ", "")
    if "ˈ" not in ipa:
        m = re.search(r"[ɑæɛɪiʌʊuoɔeaəɚɜ]", ipa)
        if m:
            ipa = ipa[:m.start()] + "ˈ" + ipa[m.start():]
    return ipa


#: A word said alone is not the word said in a sentence. These were found by
#: `tools/train_g2p.py --sentence-forms`, which compares each word on its own against
#: the same word inside a line, rather than guessing which ones reduce.
SENTENCE_FORMS = {
    "to": "tə", "the": "ðɪ", "i": "aɪ", "could": "kʊd", "would": "wʊd",
    "will": "wɪl", "through": "θɹuː",
}

#: These keep their vowel but drop to a secondary stress.
DEMOTED = {
    "having", "might", "should", "under", "whenever", "where", "without",
}


def phonemes(text, lexicon=None):
    """IPA for a whole sentence.

    Not the words joined. English reduces its function words in running speech -- "to"
    is tuː alone and tə in a sentence -- and a word ending in ɚ grows a linking ɹ before
    a vowel ("number of" is nˈʌmbɚɹ əv). Getting every word right and stopping there
    leaves the sentence audibly wrong, the same way it did in Spanish.
    """
    lexicon = lexicon or {}
    pieces = re.findall(r"[A-Za-z']+|[^A-Za-z']+", text)
    out = []
    for index, piece in enumerate(pieces):
        if not re.match(r"[A-Za-z]", piece):
            out.append(piece)
            continue
        lower = piece.lower()
        if lower in SENTENCE_FORMS:
            ipa = SENTENCE_FORMS[lower]
        else:
            ipa = phonemes_for(piece, lexicon)
            if lower in DEMOTED:
                ipa = ipa.replace("ˈ", "ˌ")
        # Linking r: an ɚ before a vowel in the next word is realized ɚɹ.
        following = "".join(pieces[index+1:index+3])
        if ipa.endswith("ɚ") and re.match(r"\W*[aeiouAEIOU]", following):
            ipa += "ɹ"
        out.append(ipa)
    return "".join(out)
