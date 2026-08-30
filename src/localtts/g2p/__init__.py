"""Text to phonemes, with no runtime dependency.

local-tts imports only the standard library, so it cannot call a phonemizer. This
package is how it transcribes anyway: rules per language, plus a frozen lexicon for
what rules cannot reach.

The split is not arbitrary. Spanish is close to phonemic, so rules alone reproduce
espeak on 100% of segments and need no lexicon. English is not: "through" and "thought"
share four letters and sound nothing alike, and no rule recovers "colonel" from its
spelling. There the lexicon carries the irregular words and rules carry the rest.

A lexicon is data, generated once by `tools/train_g2p.py` on a machine that does have a
phonemizer, and read here as JSON. Nothing at runtime imports anything.

    from localtts import g2p
    g2p.phonemes("Ya subí el pull request", "es-419")

Languages with neither rules nor a lexicon return None, which callers read as "cannot
transcribe this" rather than as a bad transcription.
"""

import json
import os

from localtts.g2p import backend

#: Rule modules, by the base of the language tag.
_RULES = {}

#: Frozen exception lexicons, loaded lazily and kept.
_LEXICONS = {}

LEXICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lexicons")


def _base(lang):
    return (lang or "").strip().lower().replace("_", "-").split("-")[0]


def _rules_for(lang):
    base = _base(lang)
    if base not in _RULES:
        try:
            module = __import__("localtts.g2p.%s" % base, fromlist=["*"])
        except ImportError:
            module = None
        _RULES[base] = module
    return _RULES[base]


def lexicon_for(lang):
    """The frozen word -> IPA table for `lang`, or {} when there is none.

    Looked up by the full tag first ("en-us"), then the base ("en"), so a regional
    lexicon can sit beside a general one without either shadowing the other.
    """
    tag = (lang or "").strip().lower().replace("-", "_")
    for name in (tag, _base(lang)):
        if not name:
            continue
        if name in _LEXICONS:
            if _LEXICONS[name]:
                return _LEXICONS[name]
            continue
        path = os.path.join(LEXICON_DIR, "%s.json" % name)
        try:
            with open(path, encoding="utf-8") as handle:
                _LEXICONS[name] = json.load(handle).get("words") or {}
        except (OSError, ValueError):
            _LEXICONS[name] = {}
        if _LEXICONS[name]:
            return _LEXICONS[name]
    return {}


def supported():
    """Language bases this package can transcribe, from rules or a lexicon."""
    found = set()
    for name in os.listdir(LEXICON_DIR):
        if name.endswith(".json"):
            found.add(name[:-5].split("_")[0])
    here = os.path.dirname(os.path.abspath(__file__))
    for name in os.listdir(here):
        # backend.py is the optional phonemizer, not a language.
        if name.endswith(".py") and not name.startswith("_") and name != "backend.py":
            found.add(name[:-3])
    return sorted(found)


def phonemes(text, lang):
    """IPA for `text`, or None when this language cannot be transcribed here.

    A real phonemizer wins when the install has one: it is what the model was trained
    against, it covers every language espeak knows, and it does not need a rule module
    written for each. The rules are the fallback, which is what upstream always uses --
    they exist because local-tts itself may not depend on anything.

    None rather than a guess: a caller that gets a string will send it to the model as
    phonemes, and a wrong transcription is worse than falling back to the text, which
    the backend's own phonemizer can still handle.
    """
    said = backend.phonemes(text, lang)
    if said is not None:
        return said
    module = _rules_for(lang)
    if module is None:
        return None
    return module.phonemes(text, lexicon=lexicon_for(lang))


def phonemes_for(word, lang):
    """IPA for a single word, or None.

    Order: a real phonemizer, then the frozen lexicon, then the rules. The lexicon sits
    below the library on purpose -- it was frozen *from* that library, so where both
    have an answer they agree, and where they differ the library is the newer one.
    """
    said = backend.phonemes(word, lang)
    if said is not None:
        return said
    table = lexicon_for(lang)
    hit = table.get(word.strip().lower())
    if hit:
        return hit
    module = _rules_for(lang)
    return module.phonemes_for(word) if module else None


def using_library():
    """Whether a real phonemizer is doing the work, for `tts check` to report."""
    return backend.available()
