"""A fixed, trapped week of work on a small invoicing app, to test debrief end to end.

Every trap has a known right answer, checked by evals/check.py:
- PR #1 states its business rule (5% late fee after 30 days, capped at 50 EUR); the code also skips
  invoices under 10 EUR, a rule written nowhere: it must come out as `inferred`.
- PR #2 states a choice between two options (CSV built in memory rather than streamed): a decision question.
- PR #3 removes a test case and its assertions while changing the code: `tests_weakened`.
- PR #4 is a dependency bump: trivial, no summary.
- A commit outside any PR states the VAT rounding rule in its message: provenance `commit`.
- Files authored by Ada and Bob are blind spots for the user, Me.
- The user's Claude session is mostly approvals: who holds the pen.
- RETELLING, the user's retelling of the week, has a wrong figure, a wrong format, a wrong word and gaps.

Everything is dated and authored explicitly, so commit hashes and facts.json are the same on every machine.

CLI: python3 evals/fixture.py <empty folder>  -> builds the repo, extracts the week, prints the week folder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "debrief" / "scripts"))
import extract  # noqa: E402

SINCE, UNTIL = date(2026, 11, 9), date(2026, 11, 15)
ME = ("Me", "me@example.com")
ADA = ("Ada", "ada@example.com")
BOB = ("Bob", "bob@example.com")
BOT = ("dependabot[bot]", "bot@example.com")
URL = "https://github.com/example/invoices"

RETELLING = (
    "This week we added a 10% late fee on unpaid invoices, a PDF export of invoices for admins, "
    "and customers can now get subscriptions on their discounts."
)

BASE = {
    "README.md": "# invoices\n\nA small invoicing app.\n",
    "requirements.txt": "requests==2.31.0\n",
    "invoices/__init__.py": "",
    "invoices/models.py": (
        "from dataclasses import dataclass\nfrom datetime import date\n\n\n"
        "@dataclass\nclass Invoice:\n    number: str\n    total: float\n    due: date\n    paid: bool = False\n"
    ),
    "invoices/fees.py": "def late_fee(invoice, today):\n    return 0.0\n",
    "invoices/discounts.py": (
        "def discount(customer_years, quantity):\n"
        "    \"\"\"Loyalty: 5% after 3 years. Volume: 10% from 100 units. The larger one applies.\"\"\"\n"
        "    loyalty = 0.05 if customer_years >= 3 else 0.0\n"
        "    volume = 0.10 if quantity >= 100 else 0.0\n"
        "    return max(loyalty, volume)\n"
    ),
    "tests/test_discounts.py": (
        "from invoices.discounts import discount\n\n\n"
        "def test_loyalty():\n    assert discount(3, 1) == 0.05\n    assert discount(2, 1) == 0.0\n\n\n"
        "def test_volume():\n    assert discount(0, 100) == 0.10\n    assert discount(0, 99) == 0.0\n\n\n"
        "def test_larger_wins():\n    assert discount(5, 200) == 0.10\n"
    ),
}

VAT_BEFORE = "def vat(amount):\n    return round(amount * 0.2, 2)\n"
VAT_AFTER = (
    "from decimal import ROUND_HALF_UP, Decimal\n\n\n"
    "def vat(amount):\n"
    "    return float((Decimal(str(amount)) * Decimal('0.2')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))\n"
)

FEES = (
    "from datetime import timedelta\n\n"
    "RATE = 0.05\nCAP = 50.0\nGRACE = timedelta(days=30)\n\n\n"
    "def late_fee(invoice, today):\n"
    "    if invoice.paid or today <= invoice.due + GRACE:\n"
    "        return 0.0\n"
    "    if invoice.total < 10:\n"
    "        return 0.0\n"
    "    return min(round(invoice.total * RATE, 2), CAP)\n"
)
TEST_FEES = (
    "from datetime import date\n\nfrom invoices.fees import late_fee\nfrom invoices.models import Invoice\n\n\n"
    "def test_fee_after_thirty_days():\n"
    "    assert late_fee(Invoice('A1', 200.0, date(2026, 10, 1)), date(2026, 11, 10)) == 10.0\n\n\n"
    "def test_fee_is_capped():\n"
    "    assert late_fee(Invoice('A2', 5000.0, date(2026, 10, 1)), date(2026, 11, 10)) == 50.0\n"
)
EXPORT = (
    "import csv\nimport io\n\n\n"
    "class Forbidden(Exception):\n    status = 403\n\n\n"
    "def export_csv(user, invoices):\n"
    "    if not user.is_admin:\n"
    "        raise Forbidden()\n"
    "    out = io.StringIO()\n"
    "    writer = csv.writer(out)\n"
    "    writer.writerow(['number', 'total', 'due', 'paid'])\n"
    "    for i in invoices:\n"
    "        writer.writerow([i.number, f'{i.total:.2f}', i.due.isoformat(), 'yes' if i.paid else 'no'])\n"
    "    return out.getvalue()\n"
)
TEST_EXPORT = (
    "import pytest\n\nfrom invoices.export import Forbidden, export_csv\n\n\n"
    "class User:\n    def __init__(self, admin):\n        self.is_admin = admin\n\n\n"
    "def test_non_admin_is_refused():\n    with pytest.raises(Forbidden):\n        export_csv(User(False), [])\n\n\n"
    "def test_header():\n    assert export_csv(User(True), []).splitlines()[0] == 'number,total,due,paid'\n"
)
DISCOUNTS_AFTER = (
    "def discount(customer_years, quantity):\n"
    "    \"\"\"One rule: 10% for loyal customers (3 years) or large orders (100 units).\"\"\"\n"
    "    return 0.10 if customer_years >= 3 or quantity >= 100 else 0.0\n"
)
TEST_DISCOUNTS_AFTER = (
    "from invoices.discounts import discount\n\n\n"
    "def test_loyal_or_large():\n    assert discount(3, 1) == 0.10\n"
)

PRS = [
    {"number": 1, "title": "Late fee on overdue invoices", "author": ADA, "date": "2026-11-10T10:00:00",
     "body": "Invoices still unpaid 30 days after their due date get a 5% late fee, capped at 50 EUR.",
     "files": {"invoices/fees.py": FEES, "tests/test_fees.py": TEST_FEES}, "approved_by": "bob"},
    {"number": 2, "title": "CSV export of invoices for admins", "author": BOB, "date": "2026-11-11T15:00:00",
     "body": ("Admins can download all invoices as a CSV file; other users get a 403.\n\n"
              "We weighed streaming the file row by row against building it in memory. We build it in memory: "
              "exports stay under 10,000 rows and the code stays simple."),
     "files": {"invoices/export.py": EXPORT, "tests/test_export.py": TEST_EXPORT}, "approved_by": "ada"},
    {"number": 3, "title": "Simplify discount rules", "author": ADA, "date": "2026-11-12T11:00:00",
     "body": "Merge the loyalty and volume discounts into one rule.",
     "files": {"invoices/discounts.py": DISCOUNTS_AFTER, "tests/test_discounts.py": TEST_DISCOUNTS_AFTER},
     "approved_by": None},
    {"number": 4, "title": "chore(deps): bump requests from 2.31.0 to 2.32.3", "author": BOT, "date": "2026-11-13T08:00:00",
     "body": "Bumps requests from 2.31.0 to 2.32.3.", "files": {"requirements.txt": "requests==2.32.3\n"},
     "approved_by": "ada"},
]

SESSION_MESSAGES = [
    "Add a CSV export of invoices, admins only",
    "ok",
    "go",
    "looks good, commit it",
]


def _git(repo: Path, *args, env=None):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True,
                          text=True, env={**os.environ, **(env or {})}).stdout.strip()


def _commit(repo: Path, files: dict, message: str, author, when: str) -> str:
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _git(repo, "add", "--", path)
    stamp = when + "+01:00"
    _git(repo, "commit", "-q", "-m", message, env={
        "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1], "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_NAME": author[0], "GIT_COMMITTER_EMAIL": author[1], "GIT_COMMITTER_DATE": stamp})
    return _git(repo, "rev-parse", "HEAD")


def _session(repo: Path, projects: Path) -> None:
    """One Claude Code session of Me on this repo, mostly approvals."""
    rows, parent = [], None
    base = {"isSidechain": False, "sessionId": "sess-csv", "cwd": str(repo)}
    for i, text in enumerate(SESSION_MESSAGES):
        stamp = f"2026-11-11T14:{10 + 2 * i:02d}:00.000Z"
        rows.append({**base, "type": "user", "uuid": f"u{i}", "parentUuid": parent, "timestamp": stamp,
                     "message": {"role": "user", "content": text}})
        rows.append({**base, "type": "assistant", "uuid": f"a{i}", "parentUuid": f"u{i}", "timestamp": stamp,
                     "message": {"role": "assistant", "id": f"m{i}", "model": "claude-opus-5-5",
                                 "content": [{"type": "text", "text": "Done."}]}})
        parent = f"a{i}"
    folder = projects / "invoices"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sess-csv.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def build(root: Path) -> dict:
    """Create the repository and the Claude sessions folder under `root`. Returns what extract needs."""
    root = Path(root)
    repo, projects = root / "invoices", root / "projects"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", ME[0])
    _git(repo, "config", "user.email", ME[1])
    _git(repo, "remote", "add", "origin", URL + ".git")
    _commit(repo, BASE, "Initial invoicing app", ADA, "2026-11-02T09:00:00")
    _commit(repo, {"invoices/vat.py": VAT_BEFORE}, "Add VAT", ME, "2026-11-03T09:00:00")

    gh_list, gh_commits = [], {}
    for pr in PRS:
        sha = _commit(repo, pr["files"], f"{pr['title']} (#{pr['number']})", pr["author"], pr["date"])
        if pr["number"] == 2:  # the commit outside any PR lands between two merges
            _commit(repo, {"invoices/vat.py": VAT_AFTER},
                    "fix(vat): round VAT half up\n\nVAT amounts are rounded half up to the cent, as accounting "
                    "requires: 0.125 becomes 0.13, not 0.12.", ME, "2026-11-11T17:00:00")
        added = sum(c.count("\n") for c in pr["files"].values())
        gh_list.append({
            "number": pr["number"], "title": pr["title"], "body": pr["body"], "url": f"{URL}/pull/{pr['number']}",
            "author": {"login": pr["author"][0].lower()}, "createdAt": pr["date"][:11] + "08:00:00Z",
            "mergedAt": pr["date"] + "Z", "additions": added, "deletions": 0,
            "files": [{"path": p} for p in pr["files"]],
            "reviews": [{"author": {"login": pr["approved_by"]}, "state": "APPROVED"}] if pr["approved_by"] else [],
            "mergeCommit": {"oid": sha}})
        gh_commits[str(pr["number"])] = [{"oid": sha}]
    _session(repo, projects)

    def run(cmd, **kwargs):
        """Stands in for the gh CLI."""
        if cmd[:3] == ["gh", "pr", "list"]:
            out = json.dumps(gh_list)
        elif cmd[:3] == ["gh", "pr", "view"]:
            out = json.dumps({"commits": gh_commits[cmd[3]]})
        else:
            raise FileNotFoundError(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    return {"repo": repo, "projects": projects, "run": run}


def extract_week(root: Path) -> Path:
    """Build the fixture, extract its week like `extract.py` does, and save the retelling as the page would."""
    made = build(root)
    repo = made["repo"]
    facts = extract.build(repo, SINCE, UNTIL, made["projects"], run=made["run"])
    week = extract.week_folder(repo, UNTIL)
    extract.ensure_excluded(repo)
    extract.write_week(repo, week, facts, made["projects"])
    (repo / ".debrief" / "config.json").write_text(json.dumps({"role": "technical", "lang": "en"}) + "\n")
    (week / "results.json").write_text(json.dumps(
        {"week": week.name, "role": "technical", "answers": {},
         "retelling": {"text": RETELLING, "at": "2026-11-15T18:00:00Z"}}, indent=2) + "\n")
    return week


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: fixture.py <empty folder>", file=sys.stderr)
        sys.exit(2)
    dest = Path(sys.argv[1]).resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} is not empty", file=sys.stderr)
        sys.exit(2)
    print(extract_week(dest))
