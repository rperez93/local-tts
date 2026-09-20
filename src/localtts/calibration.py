"""Repeatable, non-playing audio measurements; no quality score is inferred."""

import argparse
import array
import json
import math
import os
import sys
import tempfile
import time
import wave

from localtts import config, providers, text as textutil
from localtts.errors import TTSError

SAMPLES = {
    "en": "The update is ready. I reviewed the code, and the tests are working correctly.",
    "es": "La actualización está lista. Revisé el código y las pruebas funcionan correctamente.",
    "fr": "La mise à jour est prête. Le code a été vérifié et les tests fonctionnent correctement.",
    "it": "L'aggiornamento è pronto. Ho controllato il codice e i test funzionano correttamente.",
    "pt": "A atualização está pronta. Revisei o código e os testes estão funcionando corretamente.",
    "ja": "更新の準備ができました。コードを確認し、テストは正常に動作しています。",
    "zh": "更新已准备就绪。我检查了代码，测试运行正常。",
    "hi": "अपडेट तैयार है। मैंने कोड की जाँच की और परीक्षण सही ढंग से काम कर रहे हैं।",
}

# Contrasts and connected speech, rather than an alphabet recited in isolation.
# These are diagnostic prompts, not proof that a model realizes every phoneme.
SOUND_SAMPLES = {
    "es": [
        ("r_rr", "El perro corre por la carretera. El carro rojo es caro, pero la reparación es rápida."),
        ("r_contrasts", "Digo pero, digo perro. Digo caro, digo carro. Digo coro, digo corro."),
        ("vowels", "Ana prepara la casa. Elena bebe té. Iris mira. Olga compra cocos. Hugo usa uvas."),
        ("stops", "Paco tapa la copa. Beto bebe poco. Tanto trabajo cuesta tiempo. Diego guarda dos gatos."),
        ("fricatives", "Sofía sale de casa. José deja la caja junto al jardín. Felipe prepara café fresco."),
        ("nasals_laterals", "La niña mañana limpia la ventana. Mi hermano llama a Elena y lleva limones."),
        ("clusters", "Tres trenes cruzan el puente. Clara prepara platos grandes con frutas frescas."),
        ("stress", "El público publicó el anuncio. Yo practico y ella practicó. El médico explicó la solución."),
        ("questions", "¿Puedes revisar el resultado? Sí, ya está listo. ¡Qué buena noticia! Lo entregamos mañana."),
    ],
    "en": [
        ("r_l", "The red lorry rolls along the road. Laura rarely reads the wrong line."),
        ("vowels", "The sheep is on the ship. The full pool is cool. A cat sits beside the bed."),
        ("stops", "Pat packed a big bag. Kate took two cups. Good dogs dig deep holes."),
        ("fricatives", "Three thin threads pass through the cloth. These shoes fit very well."),
        ("clusters", "Please bring fresh fruit and strong string. The next script starts promptly."),
        ("questions", "Could you review the result? Yes, everything is ready. What a wonderful surprise!"),
    ],
}


def measure_wav(path):
    """Measure PCM16 frames, preserving stereo timing and reporting silence honestly."""
    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise TTSError("calibration requires 16-bit PCM WAV audio")
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = array.array("h", wav.readframes(wav.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    frames = len(samples) // channels
    if not frames:
        raise TTSError("calibration received empty audio")
    peaks = [max(abs(v) for v in samples[i:i + channels])
             for i in range(0, len(samples), channels)]
    peak = max(peaks)
    threshold = max(96, peak * 0.02)
    active = [i for i, value in enumerate(peaks) if value >= threshold]
    rms = math.sqrt(sum(v * v for v in samples) / len(samples))
    return {
        "duration_s": frames / rate,
        "sample_rate": rate,
        "channels": channels,
        "peak_dbfs": 20 * math.log10(peak / 32768) if peak else None,
        "rms_dbfs": 20 * math.log10(rms / 32768) if rms else None,
        "clipped_fraction": sum(v <= -32768 or v >= 32767 for v in samples) / len(samples),
        "leading_silence_s": active[0] / rate if active else frames / rate,
        "trailing_silence_s": (frames - active[-1] - 1) / rate if active else frames / rate,
        "silent": not active,
    }


def render_sample(provider, text, path, voice=None):
    start = time.perf_counter()
    emissions = []
    previous_sink = provider.on_part
    provider.on_part = lambda part: emissions.append(time.perf_counter() - start)
    try:
        textutil.synthesize_chunked(provider, text, path, voice=voice)
    finally:
        provider.on_part = previous_sink
    elapsed = time.perf_counter() - start
    result = measure_wav(path)
    result.update(render_s=elapsed, first_fragment_s=emissions[0] if emissions else elapsed,
                  fragments=len(emissions) or 1, realtime_factor=elapsed / result["duration_s"])
    return result


def command(argv):
    parser = argparse.ArgumentParser(prog="tts calibrate", description=(
        "Save repeatable speech samples and measurements without playing or changing settings. "
        "First-fragment time measures audio readiness, not audible playback latency."))
    parser.add_argument("--lang", action="append", help="language to measure; repeatable (default: remembered languages)")
    parser.add_argument("--provider", action="append", choices=providers.names(), help="compare providers; repeatable")
    parser.add_argument("--text", help="same test text for each selected language; use one language for pronunciation tests")
    parser.add_argument("--suite", choices=("basic", "sounds"), default="basic",
                        help="sounds tests vowels, consonants and prosody in English/Spanish")
    parser.add_argument("--runs", type=int, default=2, help="renders per case (default: 2, initial and repeated)")
    parser.add_argument("--output", help="parent directory for a new calibration folder (default: temporary directory)")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.text and args.suite != "basic":
        parser.error("use either --text or --suite sounds")
    cfg = config.load()
    languages = args.lang or sorted(cfg.get("languages") or {})
    if not languages:
        raise TTSError("record a language with `tts languages --set` or pass --lang")
    cases = []
    for lang in languages:
        _, base_language = config.normalize_language(lang)
        sentences = (SOUND_SAMPLES.get(base_language) if args.suite == "sounds" else
                     [("custom" if args.text else "basic", args.text or SAMPLES.get(base_language))])
        if not sentences or any(not sentence or not sentence.strip() for _, sentence in sentences):
            raise TTSError("no calibration sample for %s; pass --text" % lang)
        entry = config.language_entry(cfg, lang) or {}
        for name in args.provider or [entry.get("provider") or cfg["provider"]]:
            voice = entry.get("voice") if entry.get("provider") == name else None
            for sample, sentence in sentences:
                cases.append((lang, name, voice, sample, sentence))
    work = tempfile.mkdtemp(prefix="local-tts-calibration-", dir=args.output)
    report = {"note": "Measurements do not establish naturalness or pronunciation accuracy. Initial runs may already be warm.", "samples": []}
    failed = False
    for case, (lang, name, voice, sample, sentence) in enumerate(cases):
        for run in range(args.runs):
            row = {"language": lang, "provider": name, "voice": voice, "sample": sample,
                   "run": run + 1, "text": sentence}
            # A fresh provider per render matches independent agent invocations.
            provider = providers.build(name, cfg, lang=lang)
            path = os.path.join(work, "%03d-%02d.%s" % (case, run + 1, provider.default_format))
            spoken = textutil.apply_pronunciations(sentence, cfg.get("pronunciations"), lang)
            row.update(path=path, spoken_text=spoken)
            try:
                row.update(render_sample(provider, spoken, path, voice=voice))
                print("%s / %s #%d: render %.2fs, first fragment %.2fs, audio %.2fs" % (
                    lang, name, run + 1, row["render_s"], row["first_fragment_s"], row["duration_s"]), flush=True)
            except (TTSError, OSError, wave.Error, EOFError) as exc:
                row["error"] = str(exc)
                failed = True
                print("%s / %s: %s" % (lang, name, exc), file=sys.stderr, flush=True)
            report["samples"].append(row)
            with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
    print(os.path.join(work, "report.json"))
    return 1 if failed else 0
