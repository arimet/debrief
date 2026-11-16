"""Validate content.json (written by Claude) against the contract, so the renderer gets clean data.

CLI: python3 content.py <week folder>  -> prints "ok" (exit 0) or one error per line (exit 1).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LANGS = ("fr", "en")
PROVENANCE = ("pr", "spec", "commit", "inferred")
TYPES = ("choice", "open", "decision")
SYNTHESIS_KEYS = ("intent", "evidence", "control")
VERDICTS = ("same", "drift", "new", "word")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _nonempty_str(v):
    return isinstance(v, str) and v != ""


def _str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


class _Check:
    def __init__(self):
        self.errors = []

    def err(self, path, problem):
        self.errors.append(f"{path}: {problem}")

    def get(self, obj, key, path, pred, what):
        """Return obj[key] after checking pred; records an error and returns None otherwise."""
        p = f"{path}.{key}" if path else key
        if not isinstance(obj, dict) or key not in obj:
            self.err(p, "missing")
        elif not pred(obj[key]):
            self.err(p, f"expected {what}")
        else:
            return obj[key]
        return None

    def items(self, obj, key, path):
        lst = self.get(obj, key, path, lambda v: isinstance(v, list), "a list")
        p = f"{path}.{key}" if path else key
        return [(f"{p}[{i}]", x) for i, x in enumerate(lst or [])]

    def provenance(self, obj, path):
        v = self.get(obj, "provenance", path, lambda x: isinstance(x, str), "a string")
        if v is not None and v not in PROVENANCE:
            self.err(f"{path}.provenance", f"{v!r} not allowed, use one of {', '.join(PROVENANCE)}")

    def synthesis(self, obj, path, keys=SYNTHESIS_KEYS):
        s = self.get(obj, "synthesis", path, lambda v: isinstance(v, dict), "an object")
        if s is not None:
            for k in keys:
                self.get(s, k, f"{path}.synthesis", _nonempty_str, "a non-empty string")

    def question(self, q, path, role, seen):
        if not isinstance(q, dict):
            return self.err(path, "expected an object")
        qid = self.get(q, "id", path, _nonempty_str, "a non-empty string")
        if qid is not None:
            if qid in seen:
                self.err(f"{path}.id", f"duplicate id {qid!r}")
            seen.add(qid)
        for k in ("question", "explanation"):
            self.get(q, k, path, _nonempty_str, "a non-empty string")
        self.get(q, "inferred", path, lambda v: isinstance(v, bool), "a boolean")
        typ = self.get(q, "type", path, lambda v: v in TYPES, f"one of {', '.join(TYPES)}")
        if typ in ("choice", "decision"):
            choices = self.get(q, "choices", path, lambda v: _str_list(v) and len(v) >= 2,
                               "a list of at least 2 strings")
            if "answer" not in q:
                self.err(f"{path}.answer", "missing")
            elif not _is_int(q["answer"]):
                self.err(f"{path}.answer", "expected an integer index")
            elif choices is not None and not 0 <= q["answer"] < len(choices):
                self.err(f"{path}.answer", f"index {q['answer']} out of range ({len(choices)} choices)")
        elif typ == "open":
            self.get(q, "answer", path, _nonempty_str, "a non-empty string")
        ref = self.get(q, "ref", path, lambda v: isinstance(v, dict), "an object")
        if ref is None:
            return
        rp = f"{path}.ref"
        if role == "technical":
            self.get(ref, "file", rp, _nonempty_str, "a non-empty string")
            self.get(ref, "line", rp, lambda v: _is_int(v) and v >= 1, "an integer >= 1")
            f = ref.get("file")
            if _nonempty_str(f) and f not in self.changed and not (self.root and (self.root / f).is_file()):
                self.err(f"{rp}.file", f"{f!r} not found in the repository or in this week's commits")
        elif not (_nonempty_str(ref.get("url")) or _nonempty_str(ref.get("commit"))
                  or _is_int(ref.get("pr")) or _nonempty_str(ref.get("pr"))):
            self.err(rp, "expected pr or url (or commit for a git-only week)")
        sha = ref.get("commit")
        if _nonempty_str(sha) and not (len(sha) >= 7 and any(x.startswith(sha) for x in self.shas)):
            self.err(f"{rp}.commit", f"{sha!r} matches no commit of facts.commits (7+ characters needed)")


def validate(content: dict, facts: dict) -> list:
    c = _Check()
    if not isinstance(content, dict):
        return ["content: expected an object"]
    if not isinstance(facts, dict):
        return ["facts: expected an object"]
    commits = [x for x in facts.get("commits") or [] if isinstance(x, dict)]
    shas = [x["sha"] for x in commits if isinstance(x.get("sha"), str)]
    changed = {f.get("path") for x in commits for f in x.get("files") or [] if isinstance(f, dict)}
    session_ids = {s["id"] for s in facts.get("sessions") or []
                   if isinstance(s, dict) and _nonempty_str(s.get("id"))}
    root = (facts.get("repo") or {}).get("root") if isinstance(facts.get("repo"), dict) else None
    c.shas, c.changed = shas, changed
    c.root = Path(root) if isinstance(root, str) and Path(root).is_dir() else None
    c.get(content, "lang", "", lambda v: v in LANGS, f"one of {', '.join(LANGS)}")

    feats = c.items(content, "features", "")
    ids = {f["id"] for _, f in feats if isinstance(f, dict) and _nonempty_str(f.get("id"))}

    def feature_ref(p, x):
        fid = c.get(x, "feature_id", p, _nonempty_str, "a non-empty string")
        if fid is not None and fid not in ids:
            c.err(f"{p}.feature_id", f"{fid!r} matches no features[].id")

    for p, f in feats:
        if not isinstance(f, dict):
            c.err(p, "expected an object")
            continue
        for k in ("id", "title", "summary", "merged_on"):
            c.get(f, k, p, _nonempty_str, "a non-empty string")
        for k in ("cases_handled", "cases_not_handled"):
            c.get(f, k, p, _str_list, "a list of strings")
        c.provenance(f, p)
        c.get(f, "ref", p, lambda v: isinstance(v, dict), "an object")
    for p, x in c.items(content, "visible_changes", ""):
        c.get(x, "text", p, _nonempty_str, "a non-empty string")
        feature_ref(p, x)
        c.provenance(x, p)
    for p, x in c.items(content, "open_questions", ""):
        c.get(x, "text", p, _nonempty_str, "a non-empty string")
        feature_ref(p, x)

    quiz = c.get(content, "quiz", "", lambda v: isinstance(v, dict), "an object")
    if quiz is not None:
        seen = set()
        allowed = (0, *range(5, 11)) if not facts.get("commits") else tuple(range(5, 11))
        for role in ("business", "technical"):
            qs = c.get(quiz, role, "quiz", lambda v: isinstance(v, list), "a list")
            if qs is None:
                continue
            if len(qs) not in allowed:
                want = "0 or 5 to 10" if 0 in allowed else "5 to 10"
                c.err(f"quiz.{role}", f"expected {want} questions, got {len(qs)}")
            for i, q in enumerate(qs):
                c.question(q, f"quiz.{role}[{i}]", role, seen)

    review = c.get(content, "review", "", lambda v: isinstance(v, dict), "an object")
    if review is not None:
        repo = c.get(review, "repository", "review", lambda v: isinstance(v, dict), "an object")
        if repo is not None:
            c.synthesis(repo, "review.repository")
            for p, n in c.items(repo, "notable", "review.repository"):
                c.get(n, "ref", p, lambda v: v not in (None, "", {}), "a reference")
                c.get(n, "why", p, _nonempty_str, "a non-empty string")
        if "conversations" not in review:
            c.err("review.conversations", "missing (use null when there are no sessions)")
        elif review["conversations"] is None:
            pass
        elif not facts.get("sessions"):
            c.err("review.conversations", "must be null when facts.sessions is empty")
        elif not isinstance(review["conversations"], dict):
            c.err("review.conversations", "expected an object or null")
        else:
            conv = review["conversations"]
            c.synthesis(conv, "review.conversations", SYNTHESIS_KEYS + ("pen",))
            for p, m in c.items(conv, "moments", "review.conversations"):
                for k in ("session_id", "excerpt", "why", "better_prompt"):
                    c.get(m, k, p, _nonempty_str, "a non-empty string")
                sid = m.get("session_id") if isinstance(m, dict) else None
                if _nonempty_str(sid) and sid not in session_ids:
                    c.err(f"{p}.session_id", f"{sid!r} matches no facts.sessions[].id")

    retelling = content.get("retelling")
    if retelling is not None:
        if not isinstance(retelling, dict):
            c.err("retelling", "expected an object or null")
        else:
            for p, v in c.items(retelling, "verdicts", "retelling"):
                for k in ("quote", "note"):
                    c.get(v, k, p, _nonempty_str, "a non-empty string")
                kind = c.get(v, "verdict", p, lambda x: x in VERDICTS, f"one of {', '.join(VERDICTS)}")
                if isinstance(v, dict) and v.get("feature_id") is not None:
                    feature_ref(p, v)
                elif kind in ("same", "drift"):
                    c.err(f"{p}.feature_id", f"required for {kind!r}")
            for p, m in c.items(retelling, "missing", "retelling"):
                feature_ref(p, m)
                c.get(m, "note", p, _nonempty_str, "a non-empty string")
    return c.errors


def main(argv):
    if len(argv) != 2:
        print("usage: content.py <week folder>", file=sys.stderr)
        return 2
    folder = Path(argv[1])
    loaded = {}
    for name in ("facts", "content"):
        path = folder / f"{name}.json"
        try:
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"{name}.json: cannot read ({e})")
            return 1
    errors = validate(loaded["content"], loaded["facts"])
    print("\n".join(errors) if errors else "ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
