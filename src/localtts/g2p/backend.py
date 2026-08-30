"""The real phonemizer, when this install has one.

Upstream local-tts has no runtime dependencies, so `localtts.g2p` transcribes with
hand-written rules: exact for Spanish, partial for English, nothing for anything else.
This module is the other option -- espeak through `phonemizer`, which is what the
reference implementation uses and what the model was trained against.

It is optional on purpose. The import is guarded, so a checkout without the package
behaves exactly as upstream does; with it, every language espeak knows works, and the
rules become a fallback rather than the whole story.

    pip install phonemizer espeakng-loader     # 22 MB, espeak binary included

Why a fork would want this: the rules took twenty-two rounds to reach espeak's output
for Spanish and still only reach 44% of English sentences, because English stress is
lexical and spelling does not carry it. A library that already solved that is worth
22 MB in an install that is allowed to have dependencies.
"""

_BACKENDS = {}
_AVAILABLE = None


def available():
    """Whether a real phonemizer can be imported here. Cached: the answer cannot change
    within a process, and the import itself is not cheap."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            import espeakng_loader                                   # noqa: F401
            from phonemizer.backend import EspeakBackend             # noqa: F401
            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def _backend(lang):
    """One backend per language, built once. Loading espeak's data for a language is
    the expensive part and it does not change between calls."""
    if lang not in _BACKENDS:
        import espeakng_loader
        from phonemizer.backend import EspeakBackend
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
        _BACKENDS[lang] = EspeakBackend(lang, preserve_punctuation=True,
                                        with_stress=True)
    return _BACKENDS[lang]


def phonemes(text, lang):
    """IPA for `text`, or None when no phonemizer is installed or the language is one
    espeak does not know.

    None rather than a guess, for the same reason the rules return it: a caller that
    gets a string hands it to the model as phonemes, and a wrong transcription is worse
    than falling back to the text the backend can still read for itself.
    """
    if not available() or not (text or "").strip():
        return None
    try:
        said = _backend(lang).phonemize([text])[0]
    except Exception:
        return None
    return said.strip() or None
