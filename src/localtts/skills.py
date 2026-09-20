"""Install the local-tts agent skills into whichever coding agents are present.

All current targets use native `<root>/skills/<name>/SKILL.md` files. Legacy
delimited instruction sections are migrated with backups, preserving unrelated text.
"""

import os
import re
import sys
from pathlib import Path

from localtts import config
from localtts.errors import TTSError

SKILLS = ("local-tts-speak", "local-tts-configure", "local-tts-update",
          "local-tts-tune", "local-tts-phonetics")

LEGACY = {
    "codex": ".codex/AGENTS.md",
    "cursor": ".cursor/rules/local-tts.mdc",
    "windsurf": ".codeium/windsurf/memories/local-tts.md",
    "copilot": "${CONFIG}/github-copilot/local-tts-instructions.md",
}

BEGIN = "<!-- BEGIN local-tts skills -->"
END = "<!-- END local-tts skills -->"

#: name -> (kind, path relative to home, human label)
#: kind "skill" => a skills/<name>/SKILL.md directory tree
#: kind "doc"   => a single markdown file that gets a delimited section
#:
#: "${CONFIG}" expands per platform: %APPDATA% on Windows, $XDG_CONFIG_HOME or
#: ~/.config elsewhere. Most of these agents keep a plain dotfolder in the home
#: directory on every OS, which needs no expansion.
AGENTS = {
    "claude-code": ("skill", ".claude", "Claude Code"),
    "gemini": ("skill", ".gemini", "Gemini CLI"),
    "opencode": ("skill", "${XDG_CONFIG}/opencode", "OpenCode"),
    "codex": ("skill", ".agents", "Codex CLI"),
    "cursor": ("skill", ".cursor", "Cursor"),
    "windsurf": ("skill", ".codeium/windsurf", "Windsurf"),
    "copilot": ("skill", ".copilot", "GitHub Copilot CLI"),
    "qwen": ("skill", ".qwen", "Qwen Code"),
}

#: A target counts as present when one of these directories exists. Several agents use a
#: different location on Windows, so every candidate is checked.
MARKERS = {
    "claude-code": (".claude",),
    "gemini": (".gemini",),
    "opencode": ("${XDG_CONFIG}/opencode", ".config/opencode"),
    "codex": (".codex",),
    "cursor": (".cursor",),
    "windsurf": (".codeium/windsurf", ".windsurf"),
    "copilot": (".copilot", "${CONFIG}/github-copilot", ".config/github-copilot",
                "${CONFIG}/GitHub Copilot"),
    "qwen": (".qwen",),
}


def skills_dir():
    return Path(__file__).resolve().parent / "agent_skills"


def read_skill(name):
    path = skills_dir() / ("%s.md" % name)
    if not path.exists():
        raise TTSError("bundled skill not found: %s" % path)
    return path.read_text(encoding="utf-8")


def split_frontmatter(body):
    """Return (metadata dict, prose). Only the keys this installer needs are parsed."""
    if not body.startswith("---\n"):
        return {}, body
    end = body.find("\n---\n", 3)
    if end == -1:
        return {}, body
    header, prose = body[4:end], body[end + 5:]
    meta = {}
    for line in header.splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, prose.lstrip("\n")


def home(base=None):
    return Path(base) if base else Path.home()


def config_root(base=None):
    """Where per-user tool config lives; same rule the CLI uses for its own config."""
    return Path(base) / ".config" if base else config.config_root()


def resolve(relative, base=None):
    """Turn a possibly ${CONFIG}-prefixed relative path into an absolute one."""
    text = str(relative)
    if text.startswith("${XDG_CONFIG}"):
        root = Path(base) / ".config" if base else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return root / text[len("${XDG_CONFIG}"):].lstrip("/\\")
    if text.startswith("${CONFIG}"):
        return config_root(base) / text[len("${CONFIG}"):].lstrip("/\\")
    return home(base) / text


def detect(base=None):
    """Which agents are installed on this machine, as {name: root path}."""
    found = {}
    for name, markers in MARKERS.items():
        for marker in markers:
            candidate = resolve(marker, base)
            if candidate.is_dir():
                found[name] = candidate
                break
    return found


def target_paths(name, agent, base=None):
    """Where skill `name` lands for `agent`."""
    kind, relative, _ = AGENTS[agent]
    root = resolve(relative, base)
    if kind == "skill":
        return root / "skills" / name / "SKILL.md"
    return root


def _render_doc_section(names):
    """One markdown block holding every skill, for agents without a skill mechanism."""
    parts = [
        BEGIN,
        "",
        "# local-tts — speaking to the user out loud",
        "",
        "These instructions are installed and updated by `tts skills --install`.",
        "Do not edit inside the markers; edits are overwritten. Remove the block to opt out.",
        "",
    ]
    for name in names:
        meta, prose = split_frontmatter(read_skill(name))
        parts.append("## %s" % meta.get("name", name))
        if meta.get("description"):
            parts.append("")
            parts.append("*%s*" % meta["description"])
        parts.append("")
        # Demote headings so they nest under the section heading above.
        parts.append(re.sub(r"^(#{1,5}) ", r"#\1 ", prose.strip(), flags=re.M))
        parts.append("")
    parts.append(END)
    return "\n".join(parts) + "\n"


def install(agent, base=None, names=SKILLS, dry_run=False):
    """Install every skill for one agent. Returns the list of paths written."""
    if agent not in AGENTS:
        raise TTSError("unknown agent %r (known: %s)" % (agent, ", ".join(sorted(AGENTS))))
    kind = AGENTS[agent][0]
    written = []

    if kind == "skill":
        for name in names:
            path = target_paths(name, agent, base)
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(read_skill(name), encoding="utf-8")
            written.append(path)
        if agent in LEGACY and set(names) == set(SKILLS):
            legacy = resolve(LEGACY[agent], base)
            if _remove_legacy_codex_section(legacy, dry_run=dry_run):
                written.append(legacy)
        return written

    path = target_paths(names[0], agent, base)
    section = _render_doc_section(names)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if BEGIN in existing and END in existing:
            head, _, rest = existing.partition(BEGIN)
            _, _, tail = rest.partition(END)
            updated = head.rstrip("\n") + ("\n\n" if head.strip() else "") + section + tail.lstrip("\n")
        elif existing.strip():
            updated = existing.rstrip("\n") + "\n\n" + section
        else:
            updated = section
        path.write_text(updated, encoding="utf-8")
    written.append(path)
    return written


def _remove_legacy_codex_section(path, dry_run=False):
    """Retire only our delimited block after native skills have been installed."""
    if not path.exists():
        return False
    existing = path.read_text(encoding="utf-8")
    start = existing.find(BEGIN)
    end = existing.find(END, start + len(BEGIN)) if start >= 0 else -1
    if start < 0 or end < 0:
        return False
    if not dry_run:
        # Keep the exact former file once, including any edits inside our block.
        backup = path.with_name(path.name + ".local-tts.bak")
        if not backup.exists():
            backup.write_text(existing, encoding="utf-8")
        remainder = existing[:start] + existing[end + len(END):]
        if remainder.strip():
            path.write_text(remainder, encoding="utf-8")
        else:
            path.unlink()
    return True


def uninstall(agent, base=None, names=SKILLS):
    """Remove what install() wrote. Returns the list of paths affected."""
    if agent not in AGENTS:
        raise TTSError("unknown agent %r" % agent)
    kind = AGENTS[agent][0]
    removed = []

    if kind == "skill":
        for name in names:
            path = target_paths(name, agent, base)
            if path.exists():
                path.unlink()
                removed.append(path)
                parent = path.parent
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
        if agent in LEGACY and set(names) == set(SKILLS):
            legacy = resolve(LEGACY[agent], base)
            if _remove_legacy_codex_section(legacy):
                removed.append(legacy)
        return removed

    path = target_paths(names[0], agent, base)
    if not path.exists():
        return removed
    existing = path.read_text(encoding="utf-8")
    if BEGIN not in existing or END not in existing:
        return removed
    head, _, rest = existing.partition(BEGIN)
    _, _, tail = rest.partition(END)
    remainder = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).strip()
    if remainder:
        path.write_text(remainder + "\n", encoding="utf-8")
    else:
        path.unlink()
    removed.append(path)
    return removed


def status(agent, base=None, names=SKILLS):
    """(installed, detail) for one agent."""
    kind = AGENTS[agent][0]
    if kind == "skill":
        paths = [target_paths(name, agent, base) for name in names]
        present = [p for p in paths if p.exists()]
        if not present:
            return False, "not installed"
        return len(present) == len(paths), "%d/%d skills" % (len(present), len(paths))
    path = target_paths(names[0], agent, base)
    if path.exists() and BEGIN in path.read_text(encoding="utf-8"):
        return True, "section present"
    return False, "not installed"
