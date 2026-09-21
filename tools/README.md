# Optional speech experiments

These tools retain audio and reports without changing preferences or playing audio.
Run rendering scripts with `PYTHONPATH=src` from the repository root. They require
configured local speech servers; they are experiment utilities, not runtime dependencies.
Output directories must be new. Each output contains `report.json`.

```sh
PYTHONPATH=src python tools/run_sound_sweep.py /tmp/my-sweep
PYTHONPATH=src python tools/refine_speech.py /tmp/my-refinement \
  --baseline /tmp/my-sweep/report.json \
  --kokoro-script /path/to/kokoro_server.py
PYTHONPATH=src python tools/compare_piper.py /tmp/my-piper-sweep \
  --model-directory /path/to/piper-trial-models
```

The refinement script imports the configured server's phonemizer; use an interpreter
with that server's dependencies. The Piper comparison expects es_MX-claude-high.onnx
and es_AR-daniela-high.onnx plus their adjacent JSON files, and the configured default
English Piper voice. It does not download them.

Optional ASR evaluation requires faster-whisper in a separate environment:

```sh
/path/to/evaluation-venv/bin/python tools/evaluate_speech.py /tmp/my-sweep/report.json \
  --model-cache /path/to/recognizer-model-cache --output /tmp/my-sweep/asr.json
```

The recognizer model downloads if absent. Speech stays local. Word and character
error rates are proxies for intelligibility, not phonetic or naturalness verdicts.
See `docs/unattended-calibration-2026-09-20.md` for measured results and limitations.

## Settings screenshots

`python tools/capture_settings.py` refreshes `assets/settings.png` and
`assets/settings-help.png` from a real editor in an isolated tmux server with
a disposable config. It requires tmux, Pillow and DejaVu Sans Mono (or `--font`).
These are documentation tools only; the CLI has no runtime dependencies.
