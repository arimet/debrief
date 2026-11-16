import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from fixtures import TmpDirCase
import content

SCRIPT = Path(content.__file__)


def tech_q(i):
    return {"id": f"d{i}", "type": "choice", "question": "Where?", "choices": ["a", "b", "c"],
            "answer": 1, "explanation": "because", "ref": {"file": "src/a.py", "line": 3},
            "inferred": False}


def biz_q(i):
    return {"id": f"f{i}", "type": "open", "question": "What?", "answer": "this",
            "explanation": "because", "ref": {"pr": 12, "url": "https://x/pull/12"},
            "inferred": True}


SYNTH = {"intent": "i", "evidence": "e", "control": "c"}


def good_content():
    return {
        "lang": "en",
        "features": [{"id": "f1", "title": "T", "summary": "S", "cases_handled": ["a"],
                      "cases_not_handled": [], "provenance": "pr", "ref": {"pr": 12},
                      "merged_on": "2026-11-10"}],
        "visible_changes": [{"text": "x", "feature_id": "f1", "provenance": "spec"}],
        "open_questions": [{"text": "x", "feature_id": "f1"}],
        "quiz": {"business": [biz_q(i) for i in range(5)], "technical": [tech_q(i) for i in range(5)]},
        "review": {"repository": {"synthesis": SYNTH, "notable": [{"ref": "a.py", "why": "w"}]},
                   "conversations": {"synthesis": {**SYNTH, "pen": "p"}, "moments": [
                       {"session_id": "s", "excerpt": "e", "why": "w", "better_prompt": "b"}]}},
    }


FACTS = {"commits": [{"sha": "abcdef1234567", "files": [{"path": "src/a.py"}]}],
         "sessions": [{"id": "s"}]}


class TestValidate(TmpDirCase):
    def errors(self, mutate, facts=FACTS):
        c = good_content()
        mutate(c)
        return content.validate(c, facts)

    def test_valid_content_passes(self):
        self.assertEqual(content.validate(good_content(), FACTS), [])

    def test_extra_keys_allowed(self):
        self.assertEqual(self.errors(lambda c: c.update(extra=1)), [])

    def test_validation_names_the_field(self):
        errs = self.errors(lambda c: c["review"]["repository"].pop("synthesis"))
        self.assertTrue(any(e.startswith("review.repository.synthesis") for e in errs), errs)
        errs = self.errors(lambda c: c["review"].pop("repository"))
        self.assertTrue(any(e.startswith("review.repository") for e in errs), errs)

        def bad_answer(c):
            c["quiz"]["technical"][2]["answer"] = 4
        self.assertIn("quiz.technical[2].answer: index 4 out of range (3 choices)", self.errors(bad_answer))

        def bad_prov(c):
            c["features"][0]["provenance"] = "guess"
        errs = self.errors(bad_prov)
        self.assertTrue(any(e.startswith("features[0].provenance") and "pr, spec, commit, inferred" in e
                            for e in errs), errs)

        def open_answer(c):
            c["quiz"]["business"][1]["answer"] = 3
        errs = self.errors(open_answer)
        self.assertTrue(any(e.startswith("quiz.business[1].answer") for e in errs), errs)

    def test_returns_all_errors(self):
        def mutate(c):
            c["lang"] = "de"
            c["features"][0]["title"] = 1
        self.assertEqual(len(self.errors(mutate)), 2)

    def test_quiz_size(self):
        def four(c):
            c["quiz"]["technical"].pop()
        self.assertIn("quiz.technical: expected 5 to 10 questions, got 4", self.errors(four))

        def empty(c):
            c["quiz"] = {"business": [], "technical": []}
        self.assertEqual(self.errors(empty, {"commits": [], "sessions": [{"id": "s"}]}), [])
        self.assertTrue(self.errors(empty))

    def test_duplicate_question_ids(self):
        def dup(c):
            c["quiz"]["technical"][0]["id"] = "f0"
        errs = self.errors(dup)
        self.assertTrue(any(e.startswith("quiz.technical[0].id") and "duplicate" in e for e in errs), errs)

    def test_conversations_must_be_null_without_sessions(self):
        facts = {**FACTS, "sessions": []}
        errs = self.errors(lambda c: None, facts)
        self.assertTrue(any(e.startswith("review.conversations") for e in errs), errs)

        def null(c):
            c["review"]["conversations"] = None
        self.assertEqual(self.errors(null, facts), [])

    def test_conversation_synthesis_needs_pen(self):
        def drop(c):
            del c["review"]["conversations"]["synthesis"]["pen"]
        self.assertIn("review.conversations.synthesis.pen: missing", self.errors(drop))

    def test_decision_question(self):
        def dec(c):
            c["quiz"]["technical"][0].update(type="decision", answer=2)
        self.assertEqual(self.errors(dec), [])

        def out(c):
            c["quiz"]["technical"][0].update(type="decision", answer=5)
        self.assertTrue(any(e.startswith("quiz.technical[0].answer") for e in self.errors(out)))

    def test_retelling(self):
        def ok(c):
            c["retelling"] = {"verdicts": [
                {"quote": "q", "verdict": "same", "feature_id": "f1", "note": "n"},
                {"quote": "abonnement", "verdict": "word", "feature_id": None, "note": "the PR says option"},
                {"quote": "q", "verdict": "new", "note": "n"}],
                "missing": [{"feature_id": "f1", "note": "n"}]}
        self.assertEqual(self.errors(ok), [])

        def bad(c):
            c["retelling"] = {"verdicts": [{"quote": "q", "verdict": "drift", "note": "n"},
                                           {"quote": "q", "verdict": "maybe", "feature_id": "f1", "note": "n"}],
                              "missing": [{"feature_id": "zz", "note": "n"}]}
        errs = self.errors(bad)
        self.assertIn("retelling.verdicts[0].feature_id: required for 'drift'", errs)
        self.assertTrue(any(e.startswith("retelling.verdicts[1].verdict") for e in errs), errs)
        self.assertTrue(any(e.startswith("retelling.missing[0].feature_id") for e in errs), errs)

    def test_ref_shapes(self):
        def tech_bad(c):
            c["quiz"]["technical"][0]["ref"] = {"file": "a.py", "line": 0}
        self.assertTrue(any(e.startswith("quiz.technical[0].ref.line") for e in self.errors(tech_bad)))

        def tech_nofile(c):
            c["quiz"]["technical"][0]["ref"] = {"line": 2}
        self.assertTrue(any(e.startswith("quiz.technical[0].ref.file") for e in self.errors(tech_nofile)))

        def biz_bad(c):
            c["quiz"]["business"][0]["ref"] = {"file": "a.py"}
        self.assertTrue(any(e.startswith("quiz.business[0].ref") for e in self.errors(biz_bad)))

        def fac_commit(c):
            c["quiz"]["business"][0]["ref"] = {"commit": "abcdef1"}
        self.assertEqual(self.errors(fac_commit), [])

        def fac_url(c):
            c["quiz"]["business"][0]["ref"] = {"url": "https://x"}
        self.assertEqual(self.errors(fac_url), [])


class TestCrossRefs(TmpDirCase):
    def errors(self, mutate, facts=FACTS):
        c = good_content()
        mutate(c)
        return content.validate(c, facts)

    def test_commit_provenance_allowed(self):
        def m(c):
            c["features"][0]["provenance"] = "commit"
        self.assertEqual(self.errors(m), [])

    def test_feature_id_refs(self):
        def vc(c):
            c["visible_changes"][0]["feature_id"] = "nope"
        self.assertTrue(any(e.startswith("visible_changes[0].feature_id") for e in self.errors(vc)))

        def oq(c):
            c["open_questions"][0]["feature_id"] = "nope"
        self.assertTrue(any(e.startswith("open_questions[0].feature_id") for e in self.errors(oq)))

    def test_session_id_ref(self):
        def m(c):
            c["review"]["conversations"]["moments"][0]["session_id"] = "zzz"
        errs = self.errors(m)
        self.assertTrue(any(e.startswith("review.conversations.moments[0].session_id") for e in errs), errs)

    def test_commit_ref_prefix(self):
        def bad(c):
            c["quiz"]["business"][0]["ref"] = {"commit": "deadbeef"}
        self.assertTrue(any(e.startswith("quiz.business[0].ref.commit") for e in self.errors(bad)))

        def short(c):
            c["quiz"]["business"][0]["ref"] = {"commit": "abcdef"}
        self.assertTrue(any(e.startswith("quiz.business[0].ref.commit") for e in self.errors(short)))

    def test_dev_file_ref(self):
        def m(c):
            c["quiz"]["technical"][0]["ref"] = {"file": "src/missing.py", "line": 1}
        self.assertTrue(any(e.startswith("quiz.technical[0].ref.file") for e in self.errors(m)))

    def test_dev_file_in_repo_root(self):
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "new.py").write_text("x")
        facts = {**FACTS, "repo": {"root": str(self.tmp)}}

        def ok(c):
            c["quiz"]["technical"][0]["ref"] = {"file": "src/new.py", "line": 1}
        self.assertEqual(self.errors(ok, facts), [])

        def bad(c):
            c["quiz"]["technical"][0]["ref"] = {"file": "src/missing.py", "line": 1}
        self.assertTrue(any(e.startswith("quiz.technical[0].ref.file") for e in self.errors(bad, facts)))

    def test_bad_root_skips_existence(self):
        facts = {**FACTS, "repo": {"root": "/no/such/dir"}}
        def m(c):
            c["quiz"]["technical"][0]["ref"] = {"file": "src/elsewhere.py", "line": 1}
        errs = self.errors(m, facts)
        self.assertEqual([e.split(":")[0] for e in errs], ["quiz.technical[0].ref.file"])
        self.assertEqual(self.errors(lambda c: None, facts), [])

    def test_unhashable_ids_do_not_crash(self):
        def m(c):
            c["features"][0]["id"] = ["x"]
        self.assertTrue(self.errors(m))
        facts = {**FACTS, "sessions": [{"id": ["s"]}]}
        self.assertTrue(self.errors(lambda c: None, facts))

    def test_sparse_or_non_dict_facts(self):
        self.assertIsInstance(content.validate(good_content(), {}), list)
        self.assertTrue(content.validate(good_content(), None))
        self.assertTrue(content.validate(good_content(), []))


class TestCli(TmpDirCase):
    def run_cli(self, content_data=None, raw=None):
        (self.tmp / "facts.json").write_text(json.dumps(FACTS))
        if raw is not None:
            (self.tmp / "content.json").write_text(raw)
        elif content_data is not None:
            (self.tmp / "content.json").write_text(json.dumps(content_data))
        return subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)],
                              capture_output=True, text=True)

    def test_ok(self):
        r = self.run_cli(good_content())
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"))

    def test_errors_exit_1(self):
        c = good_content()
        c["lang"] = "de"
        r = self.run_cli(c)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("lang:"))

    def test_missing_and_unparseable(self):
        self.assertEqual(self.run_cli().returncode, 1)
        r = self.run_cli(raw="{nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("content.json", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
