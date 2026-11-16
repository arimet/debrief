"""Check what Claude wrote for the fixture week against the traps planted in evals/fixture.py.

The text changes from run to run; these checks look at properties that should not.

CLI: python3 evals/check.py <week folder>  -> one line per check, exit 1 if any fails.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "debrief" / "scripts"))
import content as content_mod  # noqa: E402

# Questions whose answer is a person, a date or a count; "a customer who..." is not one.
TRIVIA = re.compile(r"^(who|when)\b|\bhow many\b|\bwho (wrote|made|authored|merged|reviewed|did)\b"
                    r"|\b(what|which) (date|day|author|developer)\b", re.I)


def _ref_pr(ref) -> str:
    if isinstance(ref, dict):
        if ref.get("pr") is not None:
            return str(ref["pr"])
        m = re.search(r"/pull/(\d+)", str(ref.get("url", "")))
        return m.group(1) if m else ""
    m = re.search(r"#(\d+)", str(ref))
    return m.group(1) if m else ""


def checks(week: Path):
    facts = json.loads((week / "facts.json").read_text(encoding="utf-8"))
    c = json.loads((week / "content.json").read_text(encoding="utf-8"))
    features = {f["id"]: f for f in c.get("features", [])}
    quiz = c.get("quiz") or {}
    questions = [q for role in ("business", "technical") for q in quiz.get(role, [])]
    review = c.get("review") or {}
    repo_review = review.get("repository") or {}
    retelling = c.get("retelling") or {}
    verdicts = retelling.get("verdicts") or []

    errors = content_mod.validate(c, facts)
    yield "content.json is valid", not errors, "; ".join(errors[:3])

    yield ("dependency bump listed without a summary",
           "pr-4" in features and not (week / "summaries" / "feature-4.md").exists(), "")

    summary = (week / "summaries" / "feature-1.md")
    text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    hit = any(re.search(r"\b10\b", line) and "[inferred]" in line for line in text.splitlines())
    yield "the unwritten under-10 EUR rule is tagged [inferred]", hit, "" if hit else "no [inferred] line about 10 EUR in summaries/feature-1.md"

    vat = [f for fid, f in features.items() if fid.startswith("commits-")]
    yield ("the VAT rule from a commit message has provenance commit",
           any(f.get("provenance") == "commit" for f in vat), ", ".join(f"{f['id']}={f.get('provenance')}" for f in vat))

    decisions = [q for q in questions if q.get("type") == "decision"]
    on_csv = [q for q in decisions if _ref_pr(q.get("ref")) == "2" or "export" in json.dumps(q.get("ref"))
              or re.search(r"csv|stream|memory", q.get("question", ""), re.I)]
    yield "a decision question on the CSV export choice", bool(on_csv), f"{len(decisions)} decision question(s)"

    notable = [_ref_pr(n.get("ref")) for n in repo_review.get("notable", [])]
    evidence = (repo_review.get("synthesis") or {}).get("evidence", "")
    weakened = "3" in notable or re.search(r"#3|assertion|test case", evidence, re.I)
    yield "the review flags the weakened tests of PR #3", bool(weakened), f"notable: {notable}"

    trivia = [q["question"] for q in questions if TRIVIA.search(q.get("question", ""))]
    yield "no trivia question (who, when, how many)", not trivia, " | ".join(trivia[:2])

    blind = {b["path"] for b in facts.get("blind_spots", [])}
    tech_files = [q["ref"].get("file") for q in quiz.get("technical", []) if isinstance(q.get("ref"), dict)]
    share = sum(f in blind for f in tech_files) / len(tech_files) if tech_files else 0
    yield "technical questions start from blind spots (half or more)", share >= 0.5, f"{share:.0%} of {len(tech_files)}"

    pen = ((review.get("conversations") or {}).get("synthesis") or {}).get("pen", "")
    yield "the conversation review reads who held the pen", bool(pen.strip()), ""

    kinds = {(v.get("verdict"), v.get("feature_id")) for v in verdicts}
    yield "retelling: the 10% fee drifts from PR #1", ("drift", "pr-1") in kinds, str(sorted(kinds, key=str))
    yield ("retelling: the PDF export is caught (drift or new)",
           any(v.get("verdict") in ("drift", "new") and re.search(r"pdf", v.get("quote", ""), re.I) for v in verdicts), "")
    yield ("retelling: 'subscriptions' is a vocabulary gap",
           any(v.get("verdict") == "word" and re.search(r"subscription", v.get("quote", ""), re.I) for v in verdicts), "")
    missing = {m.get("feature_id") for m in retelling.get("missing") or []}
    yield ("retelling: the unmentioned VAT rounding is listed as missing",
           any(fid and fid.startswith("commits-") for fid in missing), str(sorted(missing, key=str)))

    per_feature = {}
    for q in c.get("open_questions", []):
        per_feature[q.get("feature_id")] = per_feature.get(q.get("feature_id"), 0) + 1
    worst = max(per_feature.values(), default=0)
    yield "at most 2 points to clarify per feature", worst <= 2, f"max {worst}"


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: check.py <week folder>", file=sys.stderr)
        return 2
    results = list(checks(Path(argv[1])))
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
