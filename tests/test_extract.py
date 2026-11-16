import json
import subprocess
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

from fixtures import FakeRunner, RepoBuilder, SessionBuilder, TmpDirCase
import extract

KEYS = {"period", "repo", "user", "sources", "commits", "hotzones", "risks", "blind_spots", "prs",
        "repo_signals", "priorities", "sessions", "digests", "warnings"}
SINCE, UNTIL = date(2026, 11, 9), date(2026, 11, 15)
BOB = ("Bob", "bob@example.com")


class IsTrivialTest(unittest.TestCase):
    def pr(self, title="feat(admin): site wizard", files=("src/a.py",), author="bob"):
        return {"title": title, "files": list(files), "author": author}

    def test_rules(self):
        t = extract.is_trivial
        lock = ["package-lock.json", "yarn.lock", "go.sum", "package.json", "requirements-dev.txt"]
        self.assertFalse(t(self.pr()))
        self.assertFalse(t(self.pr(files=[])))
        self.assertFalse(t(self.pr(files=["src/a.py", "package-lock.json"])))
        for title in ("chore(deps-dev): bump x", "Chore(deps): y", "Bump lodash from 1 to 2", "Update by Dependabot"):
            self.assertTrue(t(self.pr(title=title, files=lock)), title)
            self.assertFalse(t(self.pr(title=title, files=["src/a.py", "package.json"])), title)  # zod-like
        self.assertTrue(t(self.pr(title="chore(deps): pm2 7", files=["package.json", "yarn.lock"])))  # pm2-like
        self.assertTrue(t(self.pr(author="renovate[bot]", files=["Cargo.lock"])))
        self.assertFalse(t(self.pr(author="renovate[bot]", files=["src/a.py"])))
        for title in ("1.2.3", "v10.0.1", "Release 3.0", "chore(release): 3.0"):
            self.assertTrue(t(self.pr(title=title, files=["package.json", "CHANGELOG.md", "yarn.lock"])), title)
            self.assertFalse(t(self.pr(title=title, files=["package.json", "src/a.py"])), title)
        self.assertFalse(t(self.pr(title="feat: release notes page")))
        self.assertTrue(t(self.pr(files=[".github/workflows/ci.yml", ".gitlab-ci.yml", ".circleci/c.yml"])))
        self.assertTrue(t(self.pr(files=["tests/a.py", "src/b.test.ts"])))

    def test_digest_list_and_feature_digest_still_written(self):
        facts = {"prs": [self.pr(title="chore(deps): x", files=["yarn.lock"]) | {"number": 1}]}
        self.assertEqual(extract.trivial_digests(facts["prs"]), ["digests/feature-1.md"])


class ExtractTest(TmpDirCase):
    def setUp(self):
        super().setUp()
        self.repo = RepoBuilder(self.tmp)
        self.root = self.repo.root
        self.projects = self.tmp / "projects"

    def build(self, run=None):
        kwargs = {"run": run} if run else {}
        return extract.build(self.root, SINCE, UNTIL, self.projects, **kwargs)

    def test_branch_option_analyzes_that_branch(self):
        self.repo.commit({"src/a.py": "x = 1\n"}, "add a")
        self.repo.git("checkout", "-q", "-b", "side")
        self.repo.commit({"src/side.py": "y = 2\n"}, "side work", author=BOB)
        self.repo.git("checkout", "-q", "-")
        runner = FakeRunner({"gh pr list": "[]"})
        with mock.patch("prfacts.detect_host", return_value="github"):
            facts = extract.build(self.root, SINCE, UNTIL, self.projects, run=runner, branch="side")
        self.assertEqual(facts["repo"]["ref"], "side")
        self.assertIn("side work", [c["subject"] for c in facts["commits"]])
        self.assertIn(["--base", "side"], [c[0][i:i + 2] for c in runner.calls for i in range(len(c[0]))])
        self.assertNotIn("side work", [c["subject"] for c in self.build()["commits"]])

    def test_git_only_repo_produces_full_facts(self):
        self.repo.commit({"src/a.py": "x = 1\n"}, "add a")
        facts = self.build()
        self.assertEqual(set(facts) - {"_session_paths"}, KEYS)
        self.assertEqual(facts["sources"], {"git": True, "prs": False, "sessions": 0, "specs": []})
        self.assertEqual(facts["sessions"], [])
        self.assertTrue(any("no PR host" in w for w in facts["warnings"]))
        self.assertEqual(facts["period"], {"since": "2026-11-09", "until": "2026-11-15", "week": "2026-W46"})
        self.assertEqual(facts["user"], {"name": "Ada", "email": "ada@example.com"})
        self.assertEqual(facts["repo"]["host"], None)
        json.dumps(facts)

    def test_analyzed_ref_reported(self):
        self.repo.commit({"a.py": "x"}, "a")
        facts = self.build()
        self.assertEqual(facts["repo"]["ref"], "main")
        self.assertFalse([w for w in facts["warnings"] if "checkout" in w])

    def test_checkout_differing_from_ref_warns(self):
        self.repo.commit({"a.py": "x"}, "a")
        self.repo.git("update-ref", "refs/remotes/origin/main", "main")
        self.repo.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.repo.git("checkout", "-q", "-b", "feature")
        self.repo.commit({"b.py": "y"}, "b")
        facts = self.build()
        self.assertEqual(facts["repo"]["ref"], "origin/main")
        self.assertIn("your checkout (HEAD) differs from origin/main: quiz file:line refs point to origin/main",
                      facts["warnings"])

    def test_clone_behind_origin_sees_files_added_there(self):
        self.repo.commit({"src/a.py": "x\n"}, "a", date="2026-11-01T10:00:00")
        clone = self.tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(self.root), str(clone)], check=True)
        for k, v in (("user.name", "Ada"), ("user.email", "ada@example.com")):
            subprocess.run(["git", "config", k, v], cwd=clone, check=True)
        self.repo.commit({"src/new_feature.py": "def f():\n    pass\n"}, "bob adds", author=BOB)
        subprocess.run(["git", "fetch", "-q"], cwd=clone, check=True)
        facts = extract.build(clone, SINCE, UNTIL, self.projects)
        self.assertEqual(facts["repo"]["ref"], "origin/main")
        self.assertEqual([b["path"] for b in facts["blind_spots"]], ["src/new_feature.py"])
        self.assertEqual(facts["priorities"][0]["path"], "src/new_feature.py")
        self.assertIn("src/new_feature.py", facts["risks"]["untested"])

    def test_no_unlinked_commits_means_empty_list(self):
        self.repo.commit({"a.py": "x"}, "a")
        facts = self.build()
        facts["commits"] = []
        extract.write_week(self.root, self.tmp / "week", facts, self.projects)
        self.assertEqual(facts["digests"]["unlinked"], [])

    def test_unlinked_digest_split_within_cap(self):
        for n in range(3):
            body = "\n".join(f"detail {n}.{i}" for i in range(1200))
            self.repo.commit({f"f{n}.py": "x"}, f"commit {n}\n\n{body}")
        self.repo.commit({"huge.py": "x"}, "huge\n\n" + "\n".join(f"h{i}" for i in range(3000)))
        week = self.tmp / "week"
        facts = self.build()
        extract.write_week(self.root, week, facts, self.projects)
        paths = facts["digests"]["unlinked"]
        self.assertEqual(paths, [f"digests/commits-unlinked-{k}.md" for k in range(1, len(paths) + 1)])
        self.assertGreaterEqual(len(paths), 4)
        texts = [(week / p).read_text() for p in paths]
        for t in texts:
            self.assertLessEqual(len(t.splitlines()), extract.DIFF_MAX_LINES)
        self.assertEqual(sum("[diff truncated]" in t for t in texts), 1)
        joined = "\n".join(texts)
        for n in range(3):  # a commit that fits is never cut
            self.assertIn(f"detail {n}.1199", joined)
        for t in texts:  # and never split across files
            self.assertEqual(sum(f"detail {n}.0\n" in t for n in range(3)) , sum(f"detail {n}.1199" in t for n in range(3)))

    def test_hotzones_skip_paths_gone_at_head(self):
        self.repo.commit({"src/gone.py": "x\n", "src/kept.py": "y\n"}, "add both")
        self.repo.commit({"src/gone.py": None}, "remove one")
        facts = self.build()
        self.assertEqual([h["path"] for h in facts["hotzones"]["files"]], ["src/kept.py"])
        self.assertIn("src", [d["path"] for d in facts["hotzones"]["dirs"]])

    def test_priorities_order(self):
        self.repo.commit({"src/seed.py": "x = 1\n"}, "seed", author=BOB, date="2026-11-01T10:00:00")
        self.repo.commit({"src/seed.py": "x = 2\n"}, "bob edits", author=BOB)
        self.repo.commit({"src/mine.py": "y = 1\n"}, "mine")
        facts = self.build()
        got = {p["path"]: p for p in facts["priorities"]}
        self.assertEqual(got["src/seed.py"]["reasons"], ["blind_spot", "untested", "hotzone"])
        self.assertEqual(got["src/mine.py"]["reasons"], ["untested", "hotzone"])
        paths = [p["path"] for p in facts["priorities"]]
        self.assertEqual(paths, ["src/seed.py", "src/mine.py"])

    def test_priorities_unit(self):
        facts = {"risks": {"untested": ["a"], "dependencies": ["d"], "migrations": ["m"]},
                 "blind_spots": [{"path": "a", "authors": []}],
                 "hotzones": {"files": [{"path": "h", "commits": 1, "lines": 1}]},
                 "commits": [{"files": [{"path": p} for p in "adhmz"]}]}
        got = extract.priorities(facts, exists={"a", "d", "h", "m", "z"})
        self.assertEqual([(p["path"], p["score"]) for p in got],
                         [("a", 5), ("d", 2), ("m", 2), ("h", 1), ("z", 0)])
        self.assertEqual(got[0]["reasons"], ["blind_spot", "untested"])

    def test_feature_digests_capped(self):
        base = self.repo.commit({"src/big.py": "a\n"}, "base", date="2026-11-01T10:00:00")
        c1 = self.repo.commit({"src/big.py": "".join(f"line {i}\n" for i in range(2500))}, "big change")
        self.repo.commit({"src/other.py": "z = 1\n"}, "unlinked work")
        pr = {"number": 12, "title": "Big feature", "body": "Does a big thing", "url": "u", "author": {"login": "ada"},
              "createdAt": "2026-11-10T10:00:00Z", "mergedAt": "2026-11-10T12:00:00Z", "additions": 2500,
              "deletions": 1, "files": [{"path": "src/big.py"}], "reviews": [], "commits": [{"oid": c1}]}
        self.repo.git("remote", "add", "origin", "https://github.com/x/y.git")
        runner = FakeRunner({"gh pr list": json.dumps([{k: v for k, v in pr.items() if k != "commits"}]),
                             "gh pr view": json.dumps({"commits": pr["commits"]})})
        week = self.tmp / "week"
        facts = extract.build(self.root, SINCE, UNTIL, self.projects, run=runner)
        extract.write_week(self.root, week, facts, self.projects)
        self.assertEqual(facts["digests"]["features"], ["digests/feature-12.md"])
        text = (week / "digests/feature-12.md").read_text()
        self.assertIn("Big feature", text)
        self.assertIn("Does a big thing", text)
        self.assertIn("src/big.py", text)
        self.assertIn("[diff truncated]", text)
        diff_part = text.split("[diff truncated]")[0]
        self.assertLessEqual(len(diff_part.splitlines()), extract.DIFF_MAX_LINES + 40)
        self.assertNotIn("line 2400", text)
        self.assertEqual(facts["digests"]["unlinked"], ["digests/commits-unlinked-1.md"])
        unlinked = (week / "digests/commits-unlinked-1.md").read_text()
        self.assertIn("unlinked work", unlinked)
        self.assertNotIn("big change", unlinked)
        self.assertTrue(base)

    def _squash_pr(self, merge_commit, files=("src/feat.py",)):
        pr = {"number": 5, "title": "Feat", "body": "Adds feat to the product", "url": "u",
              "author": {"login": "bob"}, "createdAt": "2026-11-10T10:00:00Z", "mergedAt": "2026-11-10T12:00:00Z",
              "additions": 1, "deletions": 0, "files": [{"path": p} for p in files], "reviews": [],
              "mergeCommit": {"oid": merge_commit} if merge_commit else None}
        self.repo.git("remote", "add", "origin", "https://github.com/x/y.git")
        gone = "f" * 40  # branch commit deleted after the squash
        return FakeRunner({"gh pr list": json.dumps([pr]), "gh pr view": json.dumps({"commits": [{"oid": gone}]})})

    def test_squash_merge_diff_comes_from_merge_commit(self):
        self.repo.commit({"README.md": "r\n"}, "base", date="2026-11-01T10:00:00")
        sq = self.repo.commit({"src/feat.py": "def feat():\n    return 42\n"}, "Feat (#5)", author=BOB)
        facts = extract.build(self.root, SINCE, UNTIL, self.projects, run=self._squash_pr(sq))
        week = self.tmp / "week"
        extract.write_week(self.root, week, facts, self.projects)
        text = (week / "digests/feature-5.md").read_text()
        self.assertIn("+    return 42", text)
        self.assertEqual(facts["digests"]["unlinked"], [])
        self.assertFalse([w for w in facts["warnings"] if "diff unavailable" in w])

    def test_weakened_tests_signal(self):
        self.repo.commit({"src/feat.py": "x = 1\n", "tests/test_feat.py": "def test_x():\n    assert x == 1\n"},
                         "base", date="2026-11-01T10:00:00")
        sq = self.repo.commit({"src/feat.py": "x = 2\n", "tests/test_feat.py": "\n"}, "Feat (#5)", author=BOB)
        runner = self._squash_pr(sq, files=("src/feat.py", "tests/test_feat.py"))
        facts = extract.build(self.root, SINCE, UNTIL, self.projects, run=runner)
        extract.write_week(self.root, self.tmp / "week", facts, self.projects)
        weakened = [s for s in facts["repo_signals"] if s["type"] == "tests_weakened"]
        self.assertEqual(len(weakened), 1)
        self.assertEqual(weakened[0]["ref"], "#5")
        self.assertIn("1 assertion(s) and 1 test case(s)", weakened[0]["note"])

    def test_unavailable_diff_warns(self):
        self.repo.commit({"src/feat.py": "x\n"}, "unrelated")
        facts = extract.build(self.root, SINCE, UNTIL, self.projects, run=self._squash_pr(None))
        extract.write_week(self.root, self.tmp / "week", facts, self.projects)
        self.assertIn("PR #5: diff unavailable locally", facts["warnings"])

    def test_sources_prs_true_with_zero_merged_prs(self):
        self.repo.commit({"a.py": "x"}, "a")
        self.repo.git("remote", "add", "origin", "https://github.com/x/y.git")
        facts = self.build(FakeRunner({"gh pr list": "[]"}))
        self.assertEqual((facts["sources"]["prs"], facts["prs"]), (True, []))
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="auth required")
        facts = self.build(FakeRunner({"gh pr list": failed}))
        self.assertFalse(facts["sources"]["prs"])

    def test_specs_detected(self):
        self.repo.commit({"docs/guide.md": "g", "adr/0001.md": "a", "notes/auth-spec.md": "s",
                          "x/y.adr.md": "y", "src/a.py": "x"}, "docs")
        self.assertEqual(sorted(self.build()["sources"]["specs"]),
                         ["adr/0001.md", "docs/guide.md", "notes/auth-spec.md", "x/y.adr.md"])

    def test_week_folder_and_exclude(self):
        self.repo.commit({"a.py": "x"}, "a")
        self.assertEqual(extract.week_folder(self.root, UNTIL), self.root / ".debrief" / "2026-W46")
        extract.ensure_excluded(self.root)
        extract.ensure_excluded(self.root)
        exclude = (self.root / ".git/info/exclude").read_text().splitlines()
        self.assertEqual(exclude.count(".debrief/"), 1)

    def test_default_period(self):
        d = extract.default_period
        self.assertEqual(d(None, None, date(2026, 11, 13)), (date(2026, 11, 9), date(2026, 11, 13)))  # Friday
        self.assertEqual(d(None, None, date(2026, 11, 16)), (date(2026, 11, 9), date(2026, 11, 15)))  # Monday: last week
        self.assertEqual(d(None, None, date(2026, 11, 15)), (date(2026, 11, 9), date(2026, 11, 15)))  # Sunday
        self.assertEqual(d(date(2026, 11, 1), date(2026, 11, 3), date(2026, 11, 13)), (date(2026, 11, 1), date(2026, 11, 3)))
        self.assertEqual(d(date(2026, 11, 2), None, date(2026, 11, 13)), (date(2026, 11, 2), date(2026, 11, 13)))
        self.assertEqual(d(None, date(2026, 11, 4), date(2026, 11, 13)), (date(2026, 11, 2), date(2026, 11, 4)))

    def test_rerun_keeps_results(self):
        self.repo.commit({"a.py": "x"}, "a")
        week = extract.week_folder(self.root, UNTIL)
        week.mkdir(parents=True)
        (week / "results.json").write_text('{"keep": 1}')
        (week / "content.json").write_text("{}")
        (week / "digests").mkdir()
        (week / "digests/old.md").write_text("old")
        facts = self.build()
        extract.write_week(self.root, week, facts, self.projects)
        self.assertEqual((week / "results.json").read_text(), '{"keep": 1}')
        self.assertFalse((week / "content.json").exists())
        self.assertFalse((week / "digests/old.md").exists())
        self.assertEqual(json.loads((week / "facts.json").read_text())["period"]["week"], "2026-W46")

    def test_rerun_keeps_summaries_of_unchanged_digests(self):
        self.repo.commit({"src/a.py": "x"}, "a")
        week = extract.week_folder(self.root, UNTIL)

        def prs(body2):
            return json.dumps([{"number": n, "title": f"t{n}", "body": b, "url": "u", "createdAt": "2026-11-10T00:00:00Z",
                                "mergedAt": "2026-11-10T00:00:00Z", "additions": 0, "deletions": 0, "files": [],
                                "reviews": [], "mergeCommit": None, "author": {"login": "bob"}}
                               for n, b in ((1, "same"), (2, body2))])

        def run(body2):
            self.repo.git("remote", "remove", "origin") if "origin" in self.repo.git("remote") else None
            self.repo.git("remote", "add", "origin", "https://github.com/x/y.git")
            facts = self.build(FakeRunner({"gh pr list": prs(body2)}))
            extract.write_week(self.root, week, facts, self.projects)

        run("old")
        (week / "summaries").mkdir()
        for n in (1, 2):
            (week / f"summaries/feature-{n}.md").write_text("s")
            (week / f"summaries/feature-{n}.card.json").write_text("{}")
        (week / "summaries/gone.md").write_text("s")
        (week / "content.json").write_text("{}")
        run("new")
        self.assertEqual(sorted(p.name for p in (week / "summaries").iterdir()),
                         ["feature-1.card.json", "feature-1.md"])
        self.assertFalse((week / "content.json").exists())

    def test_session_digest_written(self):
        self.repo.commit({"a.py": "x"}, "a")
        SessionBuilder(self.root, session_id="s9", started="2026-11-10T09:00:00.000Z",
                       ended="2026-11-10T09:05:00.000Z").write(self.projects / "p")
        week = self.tmp / "week"
        facts = self.build()
        extract.write_week(self.root, week, facts, self.projects)
        self.assertEqual(facts["sessions"][0]["digest"], "digests/session-s9.txt")
        self.assertTrue((week / "digests/session-s9.txt").is_file())
        self.assertEqual(facts["sources"]["sessions"], 1)

    def test_each_session_parsed_and_searched_once(self):
        self.repo.commit({"a.py": "x"}, "a")
        for sid in ("s1", "s2"):
            SessionBuilder(self.root, session_id=sid).write(self.projects / "p")
        real = extract.sess.cr_extract.build_facts
        with mock.patch.object(extract.sess.cr_extract, "build_facts", side_effect=real) as bf, \
                mock.patch.object(extract.sess, "find_sessions", wraps=extract.sess.find_sessions) as fs:
            facts = self.build()
            extract.write_week(self.root, self.tmp / "week", facts, self.projects)
        self.assertEqual((bf.call_count, fs.call_count), (2, 1))
        self.assertNotIn("_session_paths", json.loads((self.tmp / "week/facts.json").read_text()))

    def test_truncated_session_flagged_and_warned(self):
        self.repo.commit({"a.py": "x"}, "a")
        SessionBuilder(self.root, session_id="s9", prompt="Add a login page").write(self.projects / "p")
        facts = self.build()
        with mock.patch.object(extract.sess.cr_extract, "digest",
                               return_value="\n".join(f"line {i}" for i in range(20000))):
            extract.write_week(self.root, self.tmp / "week", facts, self.projects)
        self.assertTrue(facts["sessions"][0]["truncated"])
        self.assertIn('session "Add a login page" digest truncated: kept its start and end', facts["warnings"])

    def test_untruncated_session_has_no_warning(self):
        self.repo.commit({"a.py": "x"}, "a")
        SessionBuilder(self.root, session_id="s9").write(self.projects / "p")
        facts = self.build()
        extract.write_week(self.root, self.tmp / "week", facts, self.projects)
        self.assertFalse(facts["sessions"][0]["truncated"])
        self.assertFalse([w for w in facts["warnings"] if "truncated" in w])

    def test_cli_rejects_non_repo(self):
        out = subprocess.run(["python3", str(Path(extract.__file__)), "--repo", str(self.tmp)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("not a git repository", out.stderr)


if __name__ == "__main__":
    unittest.main()
