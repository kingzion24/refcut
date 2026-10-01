# Bundled skills

Skills in this folder ship with RefCut. `skillset.py` installs each one into the machine's Claude skills
folder (`~/.claude/skills/<name>`) during setup and whenever RefCut starts, so both RefCut's own Claude
calls and Claude Code on that machine can use them. A skill you installed yourself under the same name
is never overwritten.

To bundle another skill, drop its folder (with a `SKILL.md`) here.

| Skill | Source | Used by RefCut for |
|---|---|---|
| `motion-broll` | [Barty-Bart/motion-graphics](https://github.com/Barty-Bart/motion-graphics) @ `e8d610a` (MIT, see `motion-broll/LICENSE`; Geist fonts OFL) | Talking video → **Add motion B-roll** |
| `agent-reach` | [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) @ `a19a171` (MIT, see `agent-reach/LICENSE`) | Not used by RefCut's own steps. It gives this machine's Claude Code a routing guide for reading the internet (web pages, YouTube transcripts, GitHub, X, Reddit…) when you research a video |

Local changes to `motion-broll`: `engine/beats.js` and `engine/render.js` use the OS temp folder and
proper `file://` URLs, and `engine/build.py` reads its files as UTF-8 (Windows support); `beats.js`
sizes its still sheet to the clip's frame, so vertical clips aren't cropped.

`agent-reach` is bundled as shipped upstream, with one change: `SKILL.md` is the upstream English file
(`SKILL_en.md`), and the upstream Chinese `SKILL.md` is kept as `SKILL_zh.md`. The skill is a guide, not the
tools: reading plain web pages works straight away, but most platforms need the Agent Reach tools installed.
To install them, tell Claude Code:
`Install Agent Reach: https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md`.
RefCut's setup does not run that installer, and never handles logins or cookies for any platform.
