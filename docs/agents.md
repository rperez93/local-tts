# Coding-agent support

Install with `tts skills --install`; inspect first with `tts skills --dry-run`.
Select explicit targets with `tts skills --install codex claude-code opencode cursor`.
Each target receives all five bundled native skills. Installation detects existing
agent directories by default; explicit targets can be installed before first launch.

| Agent | Global skill root | Primary documentation |
| --- | --- | --- |
| Codex | `~/.agents/skills` | [OpenAI](https://learn.chatgpt.com/docs/build-skills) |
| Claude Code | `~/.claude/skills` | [Anthropic](https://code.claude.com/docs/en/skills) |
| OpenCode | `~/.config/opencode/skills` (XDG override supported) | [OpenCode](https://opencode.ai/docs/skills) |
| Cursor | `~/.cursor/skills` | [Cursor](https://prod.cursor.com/docs/skills) |
| Gemini CLI | `~/.gemini/skills` | [Gemini](https://geminicli.com/docs/cli/skills/) |
| Windsurf / Cascade | `~/.codeium/windsurf/skills` | [Cascade](https://docs.windsurf.com/windsurf/cascade/skills) |
| GitHub Copilot CLI | `~/.copilot/skills` | [GitHub](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills) |
| Qwen Code | `~/.qwen/skills` | [Qwen](https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/skills.md) |

Paths checked against official documentation on 2026-09-20. Installer tests cover
all targets, full install/uninstall, and preservation of unrelated legacy content.
This validates the installation contract, not live execution inside every agent UI.
Agent versions, permissions, remote execution and sandboxing can affect discovery or
whether audio plays on the user's machine. A cloud agent needs its own installation
and audio route; copying a local skill does not forward sound to a remote browser.

A complete install migrates only local-tts's marked sections from old Codex, Cursor,
Windsurf and Copilot files after writing native skills, retaining a `.local-tts.bak`.
Partial installs leave legacy sections in place. Unrelated instructions survive.
Restart the agent session to refresh its skill catalog.

Skills and live status hooks are separate. `tts hooks` lists which live status-bar
integrations are available; unsupported hooks do not mean speech skills are unsupported.

Agents should call `tts -b --lang CODE`, using the user's saved voice. Cache policy,
ending silence, and playback settings apply automatically. Skills cover configuration,
speaking, tuning/calibration, pronunciation trials, and updates; they do not require
an additional performance skill with overlapping triggers.
