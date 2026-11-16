import json
import subprocess
import unittest
from datetime import date

from fixtures import FakeRunner, RepoBuilder, TmpDirCase
import prfacts

SINCE, UNTIL = date(2026, 11, 9), date(2026, 11, 15)


def gh_pr(**over):
    pr = {"number": 7, "title": "Add export", "body": "Adds CSV export for the report page.",
          "url": "https://github.com/o/r/pull/7", "author": {"login": "alice"},
          "createdAt": "2026-11-10T09:00:00Z", "mergedAt": "2026-11-11T09:00:00Z",
          "additions": 10, "deletions": 2, "files": [{"path": "src/a.py"}],
          "reviews": [{"state": "APPROVED", "author": {"login": "bob"}},
                      {"state": "APPROVED", "author": {"login": "dependabot[bot]"}},
                      {"state": "COMMENTED", "author": {"login": "carol"}}],
          "commits": [{"oid": "abc123" + "0" * 34}]}
    pr.update(over)
    return pr


def gh_run(*prs, view=None):
    """`gh pr list` returns the PRs without commits; `gh pr view` returns each PR's commits."""
    listed = [{k: v for k, v in p.items() if k != "commits"} for p in prs]
    commits = {"commits": next((p["commits"] for p in prs), [])}
    return FakeRunner({"gh pr list": json.dumps(listed), "gh pr view": json.dumps(commits) if view is None else view})


class PrFactsTest(TmpDirCase):
    def test_github_prs_mapped(self):
        prs, warnings = prfacts.merged_prs(self.tmp, SINCE, UNTIL, "github", gh_run(gh_pr()))
        self.assertEqual(warnings, [])
        pr = prs[0]
        self.assertEqual(pr["approvals"], 1)
        self.assertEqual(pr["files"], ["src/a.py"])
        self.assertEqual(pr["commits"], ["abc123" + "0" * 34])
        self.assertEqual(pr["merged_at"], "2026-11-11T09:00:00Z")
        self.assertEqual(pr["author"], "alice")
        self.assertEqual(pr["number"], 7)

    def test_commits_come_from_pr_view_not_list(self):
        run = gh_run(gh_pr())
        prfacts.merged_prs(self.tmp, SINCE, UNTIL, "github", run)
        listed = run.calls[0][0]
        self.assertNotIn("commits", listed[listed.index("--json") + 1].split(","))
        viewed = run.calls[1][0]
        self.assertEqual(viewed[:4], ["gh", "pr", "view", "7"])
        self.assertEqual(viewed[viewed.index("--json") + 1], "commits")

    def test_views_run_in_parallel_with_stable_order(self):
        import threading
        barrier = threading.Barrier(2, timeout=5)  # only passes if two views run at once
        listed = [{k: v for k, v in gh_pr(number=n).items() if k != "commits"} for n in (7, 8)]

        def run(cmd, **kw):
            if cmd[2] == "list":
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(listed), stderr="")
            barrier.wait()
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"commits": [{"oid": "c" + cmd[3]}]}), stderr="")

        prs, warnings = prfacts.merged_prs(self.tmp, SINCE, UNTIL, "github", run)
        self.assertEqual([(p["number"], p["commits"]) for p in prs], [(7, ["c7"]), (8, ["c8"])])
        self.assertEqual(warnings, [])

    def test_failing_view_warns_and_keeps_pr(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom\nmore")
        prs, warnings = prfacts.merged_prs(self.tmp, SINCE, UNTIL, "github", gh_run(gh_pr(), view=failed))
        self.assertEqual(len(prs), 1)
        self.assertEqual(prs[0]["commits"], [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("#7", warnings[0])

    def test_prs_outside_period_dropped(self):
        late = gh_pr(mergedAt="2026-11-16T00:10:00Z")
        prs, _ = prfacts.merged_prs(self.tmp, SINCE, UNTIL, "github", gh_run(late))
        self.assertEqual(prs, [])

    def test_gitlab_mr_mapped(self):
        mr = {"iid": 3, "title": "Fix", "description": "Longer description here.",
              "web_url": "https://gitlab.com/o/r/-/merge_requests/3", "author": {"username": "dan"},
              "created_at": "2026-11-10T09:00:00Z", "merged_at": "2026-11-12T09:00:00Z"}
        run = FakeRunner({"glab mr": json.dumps([mr])})
        prs, _ = prfacts.merged_prs(self.tmp, SINCE, UNTIL, "gitlab", run)
        pr = prs[0]
        self.assertEqual((pr["number"], pr["body"], pr["author"]), (3, "Longer description here.", "dan"))
        self.assertEqual(pr["url"], mr["web_url"])
        self.assertIsNone(pr["approvals"])
        self.assertEqual((pr["files"], pr["commits"], pr["additions"]), ([], [], 0))

    def test_missing_cli_degrades(self):
        run = FakeRunner({"gh pr": FileNotFoundError()})
        self.assertEqual(prfacts.merged_prs(self.tmp, SINCE, UNTIL, "github", run),
                         ([], ["gh not available: PR data skipped"]))
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="not logged in\nrun gh auth")
        prs, warnings = prfacts.merged_prs(self.tmp, SINCE, UNTIL, "github", FakeRunner({"gh pr": failed}))
        self.assertEqual(prs, [])
        self.assertIn("not logged in", warnings[0])
        self.assertNotIn("run gh auth", warnings[0])

    def test_no_host_means_nothing(self):
        self.assertEqual(prfacts.merged_prs(self.tmp, SINCE, UNTIL, None), ([], []))

    def test_detect_host(self):
        repo = RepoBuilder(self.tmp)
        self.assertIsNone(prfacts.detect_host(repo.root))
        repo.git("remote", "add", "origin", "git@github.com:o/r.git")
        self.assertEqual(prfacts.detect_host(repo.root), "github")
        repo.git("remote", "set-url", "origin", "https://gitlab.example.org/o/r.git")
        self.assertEqual(prfacts.detect_host(repo.root), "gitlab")

    def test_pr_signals(self):
        def pr(number, **over):
            base = {"number": number, "body": "A proper description of the change.", "approvals": 1,
                    "additions": 5, "deletions": 0,
                    "created_at": "2026-11-10T09:00:00Z", "merged_at": "2026-11-11T09:00:00Z"}
            return {**base, **over}

        def types(p):
            return [s["type"] for s in prfacts.pr_signals([p])]

        self.assertEqual(types(pr(1, approvals=0)), ["no_human_approval"])
        self.assertEqual(types(pr(2, approvals=None)), [])
        self.assertEqual(types(pr(3, body="")), ["empty_description"])
        self.assertEqual(types(pr(4, body="  fix ")), ["empty_description"])
        self.assertEqual(types(pr(5, additions=400, deletions=50, merged_at="2026-11-10T09:20:00Z")),
                         ["fast_large_merge"])
        self.assertEqual(types(pr(6, additions=400, deletions=50, merged_at="2026-11-10T11:00:00Z")), [])
        sig = prfacts.pr_signals([pr(9, approvals=0)])[0]
        self.assertEqual(sig["ref"], "#9")
        self.assertTrue(sig["note"])


if __name__ == "__main__":
    unittest.main()
