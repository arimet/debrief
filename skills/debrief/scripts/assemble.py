"""Assemble content.json from the per-digest cards written by the digest subagents, plus trivial PRs.

CLI: python3 assemble.py <week folder> --lang fr|en
Writes <week>/content.json with features, visible_changes, open_questions and empty quiz/review for
Claude to fill. Reports on stderr each feature or unlinked digest without a card. Exit 1 on duplicate ids or invalid card JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TRIVIAL_SUMMARY = {
    "fr": "Mise à jour technique, sans changement fonctionnel annoncé dans la PR.",
    "en": "Technical update, no functional change stated in the PR.",
}


def trivial_card(pr: dict, lang: str) -> dict:
    return {"features": [{
        "id": f"pr-{pr['number']}", "title": pr["title"], "summary": TRIVIAL_SUMMARY[lang],
        "cases_handled": [], "cases_not_handled": [], "provenance": "pr",
        "ref": {"pr": pr["number"], "url": pr["url"]}, "merged_on": pr["merged_at"][:10]}]}


def assemble(week: Path, lang: str):
    """Return (content, missing digest paths); raise ValueError on duplicate feature ids or an unreadable card."""
    facts = json.loads((week / "facts.json").read_text(encoding="utf-8"))
    digests = facts.get("digests") or {}
    trivial = set(digests.get("trivial") or [])
    prs = {f"pr-{p['number']}": p for p in facts.get("prs") or []}
    cards = []
    for f in sorted((week / "summaries").glob("*.card.json")):
        try:
            cards.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError as e:
            raise ValueError(f"{f.name}: invalid JSON ({e})") from None
    cards += [trivial_card(p, lang) for p in prs.values() if f"digests/feature-{p['number']}.md" in trivial]

    def key(card):
        fid = card["features"][0]["id"] if card.get("features") else ""
        pr = prs.get(fid)
        if pr:
            is_trivial = f"digests/feature-{pr['number']}.md" in trivial
            return (is_trivial, 0, -(pr.get("additions", 0) + pr.get("deletions", 0)), 0)
        k = fid.rpartition("-")[2]
        return (False, 1, 0, int(k) if k.isdigit() else 0)

    cards.sort(key=key)
    content = {"lang": lang, "features": [], "visible_changes": [], "open_questions": [],
               "quiz": {"business": [], "technical": []},
               "review": {"repository": None, "conversations": None}, "retelling": None}
    for card in cards:
        for k in ("features", "visible_changes", "open_questions"):
            content[k] += card.get(k) or []
    ids = [f.get("id") for f in content["features"]]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        raise ValueError(f"duplicate feature ids: {', '.join(map(str, dups))}")
    expected = [d for d in digests.get("features") or [] if d not in trivial] + list(digests.get("unlinked") or [])
    missing = [d for d in expected if not (week / "summaries" / f"{Path(d).stem}.card.json").is_file()]
    return content, missing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("week")
    ap.add_argument("--lang", choices=sorted(TRIVIAL_SUMMARY), required=True)
    args = ap.parse_args(argv)
    week = Path(args.week)
    try:
        content, missing = assemble(week, args.lang)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    for d in missing:
        print(f"no card for {d}", file=sys.stderr)
    (week / "content.json").write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(week / "content.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
