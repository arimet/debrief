"""Render facts.json + content.json (+ past weeks) into one self-contained index.html.

CLI: python3 render.py <week folder>  -> writes <week folder>/index.html (exit 1 on invalid content).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import content as content_mod

WEEK_RE = re.compile(r"\d{4}-W\d{2}")
HISTORY_WEEKS = 12
TEMPLATE = Path(__file__).with_name("template.html")


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def history(debrief_dir: Path, current_week: str) -> list:
    weeks = sorted(p.name for p in Path(debrief_dir).iterdir()
                   if p.is_dir() and WEEK_RE.fullmatch(p.name) and p.name != current_week)
    out = []
    for week in weeks[-HISTORY_WEEKS:]:
        results = _load(Path(debrief_dir) / week / "results.json")
        facts = _load(Path(debrief_dir) / week / "facts.json")
        score = results.get("score") if isinstance(results, dict) else None
        files = []
        try:
            files = [f["path"] for f in facts["hotzones"]["files"][:5]]
        except (TypeError, KeyError):
            pass
        out.append({"week": week, "score": score if isinstance(score, dict) else None, "hotzones": files})
    return out


def role(debrief_dir: Path) -> str:
    cfg = _load(Path(debrief_dir) / "config.json")
    r = cfg.get("role") if isinstance(cfg, dict) else None
    return r if r in ("technical", "business") else "technical"


def render(week_dir: Path) -> Path:
    week_dir = Path(week_dir)
    facts = json.loads((week_dir / "facts.json").read_text(encoding="utf-8"))
    content = json.loads((week_dir / "content.json").read_text(encoding="utf-8"))
    root, week = week_dir.parent, week_dir.name
    data = {"facts": facts, "content": content, "history": history(root, week),
            "week": week, "role": role(root)}
    # "<" as a JSON escape keeps "</script>" and "<!--" from ever appearing in the block
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/", blob)
    out = week_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


def main(argv):
    if len(argv) != 2:
        print("usage: render.py <week folder>", file=sys.stderr)
        return 2
    folder = Path(argv[1])
    try:
        facts = json.loads((folder / "facts.json").read_text(encoding="utf-8"))
        content = json.loads((folder / "content.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"cannot read facts.json/content.json ({e})")
        return 1
    errors = content_mod.validate(content, facts)
    if errors:
        print("\n".join(errors))
        return 1
    print(render(folder))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
