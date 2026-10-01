# Bundled skills

Skills in this folder ship with RefCut. `skillset.py` installs each one into the machine's Claude skills
folder (`~/.claude/skills/<name>`) during setup and whenever RefCut starts, so both RefCut's own Claude
calls and Claude Code on that machine can use them. A skill you installed yourself under the same name
is never overwritten.

To bundle another skill, drop its folder (with a `SKILL.md`) here.

| Skill | Source | Used by RefCut for |
|---|---|---|
| `motion-broll` | [Barty-Bart/motion-graphics](https://github.com/Barty-Bart/motion-graphics) @ `e8d610a` (MIT, see `motion-broll/LICENSE`; Geist fonts OFL) | Talking video → **Add motion B-roll** |

Local changes to `motion-broll`: `engine/beats.js` and `engine/render.js` use the OS temp folder and
proper `file://` URLs, and `engine/build.py` reads its files as UTF-8 (Windows support); `beats.js`
sizes its still sheet to the clip's frame, so vertical clips aren't cropped.
