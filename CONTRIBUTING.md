# Contributing to local-tts

Thanks for taking the time. This is a small project with a few firm rules; almost
everything else is negotiable in review.

---

## Contents

- [The one hard rule: no runtime dependencies](#the-one-hard-rule-no-runtime-dependencies)
- [Getting set up](#getting-set-up)
- [Running the tests](#running-the-tests)
- [What a change has to include](#what-a-change-has-to-include)
- [Adding a provider](#adding-a-provider)
- [Adding or changing a setting](#adding-or-changing-a-setting)
- [Documentation is part of the change](#documentation-is-part-of-the-change)
- [Code style](#code-style)
- [Commits and pull requests](#commits-and-pull-requests)
- [Releasing](#releasing)

---

## The one hard rule: no runtime dependencies

`local-tts` installs with **zero runtime dependencies** and imports only the standard
library. That is the whole point of the project: `pip install -e .` must never pull in
anything but `local-tts` itself, and the test suite must run with no test dependencies
either.

A backend's own weight (onnxruntime, numpy, torch, …) lives in *that backend's* venv,
behind a binary or an HTTP endpoint that `local-tts` shells out to or calls. If a feature
seems to need a library in this package, it almost certainly belongs on the other side of
that boundary — say so in the issue before writing the code.

Practical consequences:

- No `import numpy`, `import requests`, `import yaml` in `src/localtts/`. Use `wave`,
  `urllib.request`, `json`, `subprocess`, `array`.
- Audio maths that must happen in-process is done with `array` and `wave` (see
  `src/localtts/audio.py`), not with numpy.
- Python **≥ 3.9** is supported, so no `match`, no PEP 604 `int | None` annotations
  evaluated at runtime, no `dict | dict` merging.

## Getting set up

```bash
git clone https://github.com/rperez93/local-tts
cd local-tts
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip     # PEP 639 metadata needs pip >= 24.2
pip install -e .
tts --version
```

You do not need a working speech backend to develop most of the codebase — the tests fake
the subprocess and HTTP layers. You *do* need one to check that audio actually comes out;
`kokoro` is the default provider and the one to install first (see
[Providers](README.md#providers) in the README).

## Running the tests

```bash
python -m unittest discover -s tests -v
```

The suite has no test dependencies and uses local mock servers, not external speech
services or an audio device. They must all pass before
you open a pull request, and they should stay fast — if a test needs a real model, a real
player or a real API, it does not belong in this suite; fake the boundary instead. The
existing tests show the patterns: a stub binary on `PATH`, a local HTTP server, a
temporary config file.

A change that fixes a bug comes with a test that fails without the fix.

## What a change has to include

1. The code change.
2. A test that covers it.
3. The documentation that describes it (see below — this is not optional here).
4. Nothing else. Unrelated reformatting, renames and drive-by "improvements" in the same
   pull request make review much harder; open a second one.

## Adding a provider

1. Subclass `Provider` in `src/localtts/providers/<name>.py` and implement
   `synthesize(text, out_path, voice)` and `check()`. Most CLI-shaped backends can
   instead implement `build_command(...)` and inherit the rest.
2. Declare what the backend can genuinely do — `supports_tone_tags`, `realizes_speed`,
   `realizes_volume`, `default_format`. Be honest: these flags decide whether a `<tag>`
   is realized or silently stripped, and claiming a hook that does not exist produces
   wrong audio rather than an error.
3. Register it in `src/localtts/providers/__init__.py` (`REGISTRY` **and**
   `DESCRIPTIONS`).
4. Add its defaults to `config.DEFAULTS["providers"]`. A test asserts the registry and
   the defaults stay in sync, so a provider without defaults fails the suite.
5. Document it: a `### <name>` section in the README's Providers list, with the full
   settings table, and an entry in the `local-tts-configure` skill.

Comments in this codebase explain *why*, and provider comments in particular record what
was verified against the real binary (`piper.py` is the model to follow). If you claim a
flag exists, say where you checked.

## Adding or changing a setting

- Every setting gets a default in `config.DEFAULTS`, a row in the provider's README
  table, and an entry in the `local-tts-configure` skill, which documents *every* setting
  local-tts has and is expected to stay complete.
- Settings are reachable three ways — config file, `LOCALTTS_<PROVIDER>_<KEY>`, and
  `-s key=value` — and the precedence between them is tested. Do not add a fourth path.
- Renaming or removing a setting is a breaking change for existing config files. Say so
  in the pull request and explain the migration.

## Documentation is part of the change

For this project the documentation *is* the product as much as the code: people and
coding agents both drive it from prose. A behaviour change that lands without its docs is
incomplete, and any of these may need updating in the same pull request:

| File | Covers |
| --- | --- |
| `README.md` | The full reference: providers, settings, examples, troubleshooting. |
| `AGENT_INSTALL.md` | The step-by-step an agent follows to install and validate. |
| `src/localtts/agent_skills/local-tts-speak.md` | How an agent decides to speak. |
| `src/localtts/agent_skills/local-tts-configure.md` | Every setting, and the install steps per backend. |
| `src/localtts/agent_skills/local-tts-tune.md` | Diagnosing how speech *sounds*. |
| `src/localtts/agent_skills/local-tts-phonetics.md` | Getting one word pronounced right. |
| `src/localtts/agent_skills/local-tts-update.md` | Updating an existing install. |

Two things to check specifically, because they go stale quietly:

- **Which provider is the default.** It is `kokoro`. Any sentence that calls another
  backend "the default" is a bug.
- **Measured claims.** Timings, file sizes and "N× faster" numbers in the docs came from
  a real measurement. If you change one, re-measure it and say how; if you cannot,
  remove the number rather than let it drift.

## Code style

There is no formatter and no linter configured; match the file you are editing.

- Double quotes, 4-space indent, lines up to ~100 characters.
- `%`-formatting for messages, as the rest of the codebase does.
- Errors the user should see are `TTSError` with a message that says what to do next,
  usually including the exact `tts config --set …` command that fixes it.
- Comments explain intent and the reason a thing is not the obvious way. Do not narrate
  what the line already says.

## Commits and pull requests

- One logical change per pull request, against `main`.
- Commit subjects are written in the imperative and describe the behaviour, not the
  files: *"Remove an unhandled language tag instead of reading it as a tone"*, not
  *"update text.py"*.
- Commits, tags and release notes carry **no AI-assistant attribution** — no
  `Co-Authored-By` trailers for a tool, no "generated with" footers. Use assistants
  freely; the history just records the change.
- In the pull request description, say what changed, why, and how you verified it. If you
  measured something, include the measurement and the command that produced it.
- Say explicitly if the change breaks an existing config file, a documented flag, or a
  skill an agent may already have installed.

## Releasing

Maintainer task. The version appears in **two** places and nothing checks that they
agree, so update both:

- `pyproject.toml` → `version`
- `src/localtts/__init__.py` → `__version__`

Then a `Bump version to X.Y.Z` commit and a matching tag.

## v2 publishing

The PyPI distribution is `agents-local-tts` (Python import `localtts`, commands `tts`
and `local-tts`). Update both version declarations, test and build the wheel/sdist,
then tag and publish a GitHub release. `.github/workflows/python-publish.yml` tests,
checks version/tag consistency, builds distributions and publishes with PyPI trusted
publishing through the `pypi` GitHub environment. No API token is stored in the repo.
The PyPI publisher must match owner `rperez93`, repository `local-tts`, workflow
`python-publish.yml`, environment `pypi`. Manual dispatch must target the version tag.

Cache changes need key-invalidation, TTL, eviction, corruption and CLI integration
tests. Settings changes belong in `docs/settings.md`, README and the configure skill.
Native agent paths require primary documentation and installation/migration tests.
Do not claim a hot reload of loaded server startup state: request settings reload,
while startup-only changes wait for the next server start.
