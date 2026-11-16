import unittest
from datetime import date

from fixtures import RepoBuilder, TmpDirCase
import gitfacts

SINCE, UNTIL = date(2026, 11, 8), date(2026, 11, 15)


class GitFactsTest(TmpDirCase):
    def test_commits_in_period_only(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"old.txt": "x\n"}, "too old", date="2026-11-07T10:00:00")
        repo.commit({"src/a.py": "a\nb\n"}, "add a", date="2026-11-10T10:00:00")
        repo.commit({"late.txt": "x\n"}, "too late", date="2026-11-16T10:00:00")
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["subject"], "add a")
        self.assertEqual(found[0]["author"], "Ada")
        self.assertEqual(found[0]["files"], [{"path": "src/a.py", "added": 2, "removed": 0}])

    def test_ai_coauthored_trailer(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"a.txt": "1\n"}, "ai\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        repo.commit({"b.txt": "1\n"}, "plain")
        by_subject = {c["subject"]: c for c in gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")}
        self.assertIs(by_subject["ai"]["ai_coauthored"], True)
        self.assertIs(by_subject["plain"]["ai_coauthored"], False)

    def test_numstat_binary_and_rename(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"src/a.py": "one\ntwo\nthree\n"}, "base", date="2026-11-07T10:00:00")
        repo.commit({"logo.bin": b"\x00\x01\x02\xff"}, "binary")
        repo.commit({"src/a.py": None, "src/b.py": "one\ntwo\nthree\n"}, "rename")
        repo.git("checkout", "-q", "-b", "side")
        repo.commit({"side.txt": "s\n"}, "side work")
        repo.git("checkout", "-q", "main")
        repo.commit({"main.txt": "m\n"}, "main work")
        repo.git("merge", "-q", "--no-ff", "side", "-m", "merge side",
                 env={"GIT_AUTHOR_DATE": "2026-11-10T10:00:00", "GIT_COMMITTER_DATE": "2026-11-10T10:00:00"})
        found = {c["subject"]: c for c in gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")}
        self.assertNotIn("merge side", found)
        binary = found["binary"]["files"][0]
        self.assertIsNone(binary["added"])
        self.assertIsNone(binary["removed"])
        self.assertIn("src/b.py", [f["path"] for f in found["rename"]["files"]])

    def test_rename_into_repo_root(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"src/a.py": "one\ntwo\nthree\n"}, "base", date="2026-11-07T10:00:00")
        repo.commit({"src/a.py": None, "a.py": "one\ntwo\nthree\n"}, "move to root")
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        self.assertEqual([f["path"] for f in found[0]["files"]], ["a.py"])

    def test_new_path_brace_rename_to_root(self):
        self.assertEqual(gitfacts._new_path("{src => }/a.py"), "a.py")
        self.assertEqual(gitfacts._new_path("lib/{sub => }/a.py"), "lib/a.py")

    def test_non_ascii_path_is_not_quoted(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"caf\u00e9.py": "x\n"}, "accent")
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        self.assertEqual(found[0]["files"][0]["path"], "caf\u00e9.py")

    def test_hotzones_rank_files_and_dirs(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"src/a.py": "1\n"}, "c1")
        repo.commit({"src/a.py": "1\n2\n"}, "c2")
        repo.commit({"src/a.py": "1\n2\n3\n", "src/b.py": "x\n"}, "c3")
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        lines = sum((f["added"] or 0) + (f["removed"] or 0)
                    for c in found for f in c["files"] if f["path"] == "src/a.py")
        zones = gitfacts.hotzones(found)
        self.assertEqual(zones["files"][0], {"path": "src/a.py", "commits": 3, "lines": lines})
        self.assertEqual(zones["dirs"][0]["path"], "src")

    def test_empty_period(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"a.txt": "1\n"}, "old", date="2026-10-01T10:00:00")
        self.assertEqual(gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD"), [])
        self.assertEqual(gitfacts.hotzones([]), {"files": [], "dirs": []})

    def test_default_branch_falls_back_to_current(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"a.txt": "1\n"}, "init")
        self.assertEqual(gitfacts.default_branch(repo.root), "main")

    def test_is_test(self):
        for path in ["tests/a.py", "src/__tests__/x.ts", "a.test.ts", "a.spec.js", "test_a.py",
                     "a_test.go", "spec/models/user_spec.rb"]:
            self.assertTrue(gitfacts.is_test(path), path)
        for path in ["src/contest.py", "src/latest.ts"]:
            self.assertFalse(gitfacts.is_test(path), path)

    def test_untested_source_change(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"src/a.py": "1\n", "README.md": "x\n", "package-lock.json": "{}\n",
                     "logo.png": b"\x00\x01"}, "a only")
        repo.commit({"src/b.py": "1\n", "tests/test_b.py": "1\n"}, "b with test")
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        self.assertEqual(gitfacts.risks(found, repo.root)["untested"], ["src/a.py"])

    def test_untested_skips_deleted_files(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"src/a.py": "1\n"}, "add")
        repo.commit({"src/a.py": None}, "remove")
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        self.assertEqual(gitfacts.risks(found, repo.root)["untested"], [])

    def test_dependencies_and_migrations(self):
        repo = RepoBuilder(self.tmp)
        repo.commit({"package.json": "{}\n", "requirements-dev.txt": "x\n", "go.mod": "m\n",
                     "db/migrations/0003_add.sql": "x\n", "prisma/migrations/x/migration.sql": "x\n"}, "deps")
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        found_risks = gitfacts.risks(found, repo.root)
        self.assertEqual(sorted(found_risks["dependencies"]), ["go.mod", "package.json", "requirements-dev.txt"])
        self.assertEqual(sorted(found_risks["migrations"]),
                         ["db/migrations/0003_add.sql", "prisma/migrations/x/migration.sql"])

    def test_blind_spots_exclude_files_the_user_ever_touched(self):
        repo = RepoBuilder(self.tmp)
        bob = ("Bob", "bob@example.com")
        repo.commit({"src/x.py": "1\n"}, "bob x", author=bob, date="2026-10-01T10:00:00")
        repo.commit({"src/x.py": "1\n2\n"}, "ada x")
        repo.commit({"src/y.py": "y content\n"}, "bob y", author=bob)
        repo.commit({"src/z.py": "z content\n"}, "bob z", author=bob)
        repo.commit({"src/z.py": None}, "bob rm z", author=bob)
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        self.assertEqual(gitfacts.blind_spots(repo.root, found, "ada@example.com"),
                         [{"path": "src/y.py", "authors": ["Bob"]}])

    def test_blind_spots_know_the_users_other_email_and_renames(self):
        repo = RepoBuilder(self.tmp)
        bob = ("Bob", "bob@example.com")
        repo.commit({"src/created.py": "1\n"}, "ada elsewhere", author=("Ada", "ada@old-job.com"),
                    date="2026-10-01T10:00:00")
        repo.commit({"src/created.py": "1\n2\n"}, "bob edits", author=bob)
        repo.commit({"src/old_name.py": "a\nb\nc\nd\n"}, "ada original", date="2026-10-02T10:00:00")
        repo.git("mv", "src/old_name.py", "src/new_name.py")
        repo.commit({}, "bob renames", author=bob)
        repo.commit({"src/new_name.py": "a\nb\nc\nd\ne\n"}, "bob edits renamed", author=bob)
        repo.commit({"src/bobs.py": "unrelated bob file\n"}, "bob only", author=bob)
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        got = gitfacts.blind_spots(repo.root, found, "ada@example.com", "Ada")
        self.assertEqual([b["path"] for b in got], ["src/bobs.py"])

    def test_ai_without_tests_signal(self):
        repo = RepoBuilder(self.tmp)
        trailer = "\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
        sha = repo.commit({"src/a.py": "1\n"}, "ai change" + trailer)
        repo.commit({"src/b.py": "1\n", "tests/test_b.py": "1\n"}, "ai tested" + trailer)
        found = gitfacts.commits(repo.root, SINCE, UNTIL, "HEAD")
        self.assertEqual(gitfacts.commit_signals(found),
                         [{"type": "ai_without_tests", "ref": sha[:7], "note": "ai change"}])




def _diff(*files):
    out = []
    for path, lines in files:
        out += [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", "@@ -1,3 +1,3 @@", *lines]
    return "\n".join(out) + "\n"


class WeakenedTestsTest(unittest.TestCase):
    def test_removed_assertions_with_code_change(self):
        diff = _diff(("src/pay.js", ["-  return a", "+  return b"]),
                     ("src/pay.test.js", ['-it("refuses negative", () => {', "-  expect(pay(-1)).toThrow();", "-});",
                                          "   expect(pay(1)).toBe(1);"]))
        self.assertEqual(gitfacts.weakened_tests(diff), {"assertions": 1, "cases": 1})

    def test_moved_assertions_net_to_zero(self):
        diff = _diff(("src/a.py", ["+x = 1"]),
                     ("tests/test_a.py", ["-    assert a() == 1"]),
                     ("tests/test_b.py", ["+    assert a() == 1"]))
        self.assertIsNone(gitfacts.weakened_tests(diff))

    def test_tests_only_change_is_not_flagged(self):
        diff = _diff(("tests/test_a.py", ["-def test_old():", "-    assert old()"]))
        self.assertIsNone(gitfacts.weakened_tests(diff))

    def test_python_and_csharp_shapes(self):
        diff = _diff(("app/core.py", ["+y = 2"]),
                     ("tests/test_core.py", ["-def test_rule():", "-    self.assertEqual(f(), 2)"]),
                     ("tests/RuleTests.cs", ["-    [Fact]", "-    Check.That(rule()).IsTrue();"]))
        self.assertEqual(gitfacts.weakened_tests(diff), {"assertions": 2, "cases": 2})


if __name__ == "__main__":
    unittest.main()
