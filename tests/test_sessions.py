import unittest
from unittest import mock
from pathlib import Path

from fixtures import RepoBuilder, SessionBuilder, TmpDirCase
import sessions

GH_TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"


class FindSessionsTest(TmpDirCase):
    def setUp(self):
        super().setUp()
        self.projects = self.tmp / "projects"
        self.repo = RepoBuilder(self.tmp)
        self.root = self.repo.root

    def put(self, cwd, sid, project="p1", **kw):
        return SessionBuilder(cwd, session_id=sid, **kw).write(self.projects / project)

    def find(self, since="2026-11-08", until="2026-11-14"):
        return sessions.find_sessions([self.root], since, until, self.projects)

    def test_finds_sessions_of_repo_and_worktrees(self):
        wt = self.tmp / "wt"
        self.repo.git("worktree", "add", "-q", str(wt), "-b", "feat")
        roots = sessions.worktree_roots(self.root)
        self.assertIn(wt.resolve(), [r.resolve() for r in roots])
        a = self.put(self.root, "a")
        b = self.put(self.root / "src", "b")
        c = self.put(wt, "c", project="p2")
        self.put(self.tmp / "other", "d")
        found = sessions.find_sessions(roots, "2026-11-08", "2026-11-14", self.projects)
        self.assertEqual({p.name for p in found}, {a.name, b.name, c.name})

    def test_period_filter_uses_last_timestamp(self):
        kept = self.put(self.root, "kept", started="2026-11-07T23:00:00.000Z", ended="2026-11-08T01:00:00.000Z")
        self.put(self.root, "old", started="2026-11-07T20:00:00.000Z", ended="2026-11-07T22:00:00.000Z")
        self.assertEqual(self.find(), [kept])

    def test_skips_malformed_and_subagents(self):
        good = self.put(self.root, "good")
        (self.projects / "p1" / "bad.jsonl").write_text("not json\n{")
        (self.projects / "p1" / "good" / "subagents").mkdir(parents=True)
        SessionBuilder(self.root, session_id="sub").write(self.projects / "p1" / "good" / "subagents")
        self.assertEqual(self.find(), [good])

    def test_no_projects_dir(self):
        self.assertEqual(sessions.find_sessions([self.root], "2026-11-08", "2026-11-14", self.tmp / "missing"), [])


class DigestTest(TmpDirCase):
    def test_digest_is_redacted_and_capped(self):
        home = "/home/dev"
        path = SessionBuilder(f"{home}/work/app", prompt=f"use {GH_TOKEN} in {home}/work/app/x.py",
                              reply="ok " * 400).write(self.tmp)
        info, text = sessions.session_digest(path, home=home)
        self.assertEqual(info, {"id": "sess-1", "title": info["title"], "date": "2026-11-10", "truncated": False,
                                "pen": {"messages": 1, "approvals": 0}})
        self.assertNotIn(GH_TOKEN, text)
        self.assertNotIn(home, text)
        _, short = sessions.session_digest(path, max_chars=500, home=home)
        self.assertLessEqual(len(short), 500)
        self.assertIn("lines omitted", short)

    def test_long_digest_keeps_start_and_end(self):
        path = SessionBuilder("/home/dev/app").write(self.tmp)
        text = "\n".join(f"line {i}" for i in range(1000))
        with mock.patch.object(sessions.cr_extract, "digest", return_value=text):
            meta, out = sessions.session_digest(path, max_chars=2000, home="/home/dev")
            full_meta, full = sessions.session_digest(path, max_chars=100_000, home="/home/dev")
        self.assertTrue(meta["truncated"])
        self.assertLessEqual(len(out), 2000)
        lines = out.split("\n")
        self.assertEqual(lines[0], "line 0")
        self.assertEqual(lines[-1], "line 999")
        marker = [l for l in lines if l.startswith("[... ")]
        self.assertEqual(len(marker), 1)
        omitted = int(marker[0].split()[1])
        self.assertEqual(len(lines) - 1 + omitted, 1000)
        head = lines.index(marker[0])
        self.assertLess(head, len(lines) - head)  # the end gets about twice the start
        self.assertFalse(full_meta["truncated"])
        self.assertEqual(full, text)


class PenTest(unittest.TestCase):
    def test_counts_approval_only_messages(self):
        events = [{"kind": "user_message", "text": t} for t in
                  ("Add a CSV export to the invoices page, admins only", "ok", "Vas-y !", "go", "oui mais garde le tri")]
        events.append({"kind": "assistant_message", "text": "ok"})
        self.assertEqual(sessions.pen(events), {"messages": 5, "approvals": 3})


if __name__ == "__main__":
    unittest.main()
