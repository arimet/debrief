---
name: debrief
description: Weekly debrief of a project repository, so the team keeps a grip on code increasingly written by AI. Builds a local HTML page with a quiz on the week's work (Business or Technical role), a dashboard (features, hotzones, risks, blind spots) and a review of how the week was done with AI (pull requests, commits, the user's Claude sessions). Use when the user asks for a debrief, a weekly recap or review, to be quizzed on this week's work ("quiz me on this week", "what did we ship this week"), or in French "débrief de la semaine", "récap de la semaine", "quiz sur la semaine", or invokes /debrief. Also use when the user asks to compare their retelling of the week ("compare ma restitution", "compare my retelling").
---

# Debrief

Each team member runs this once a week, by hand, inside the project repository. It produces one local page that answers one question: **do I still have this week's work in my head?** The quiz covers the whole team's work (other people's and the AI's first), not only the user's own commits.

The page makes the user produce before it shows: the Quiz tab opens on a retelling of the week written from memory, decision questions ask what the user would have chosen and what they rule out before revealing the team's choice, and the conversation review measures who held the pen.

Four failures to avoid, even if the page renders:

- **Surveillance.** Nothing leaves the machine. Never name or rank people, never quiz on who wrote what. Export is the user's own action from the page; never post, send or upload anything.
- **Trivia.** No question whose answer is a name, a count, a date or a line number. Ask about behavior, business rules, where logic lives, and why.
- **Made-up rules presented as fact.** A rule read only in the code is `inferred`, and says so; a rule stated in a commit message is `commit`.
- **Reports nobody reads.** The review is one weekly synthesis, not a list of per-PR or per-session reports.

`<skill-dir>` below is the directory that contains this SKILL.md. Run the scripts with `python3 <skill-dir>/scripts/<name>.py`. `content.lang` (`fr` or `en`) is, in order: `--lang`, else `lang` in `.debrief/config.json`, else the language the user writes in. Every text you generate for the page, and the questions you ask during the run, are in that language.

## 1. Read the request

`/debrief [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--branch NAME] [--role business|technical] [--lang fr|en]`

- Branch: `--branch NAME`, else `branch` in `.debrief/config.json`, else the repository's default branch. A given `--branch` is saved there. The branch is read from `origin/NAME` when it exists and only pull requests merged into it are counted; pass it on to `extract.py` whenever it is not the default. Turn "on the develop branch" into `--branch develop`.

- Period: the current ISO week by default (Monday to today; on a Monday, the previous full Monday to Sunday week). Dates are inclusive. For another period pass `--since` / `--until` (`YYYY-MM-DD`): with only `--until`, `since` is the Monday of that week; with only `--since`, `until` is today. Turn "last week" or "since Monday" into these flags. The week folder is the ISO week of `until`.
- **Python first.** Run `python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'`. If it fails, try the same with `python`; if that passes, use `python` instead of `python3` for every script below. If neither passes, stop before anything else and tell the user in one line that debrief needs Python 3.9 or later (standard library only, nothing else to install): on macOS `xcode-select --install` (it also provides git), on Linux the distribution's `python3` package, on Windows the installer from python.org. Do not try to run the scripts any other way.
- Stop if `git rev-parse --show-toplevel` fails: this skill runs inside a repository. Otherwise `cd` to that path before anything else, so `.debrief/` lands at the repository root and every relative path below resolves there.
- **Fresh remote.** `extract.py` reads `origin/<default branch>` when it exists, so a stale remote ref hides the latest merges. If `git remote` lists `origin` and `find "$(git rev-parse --git-path FETCH_HEAD)" -mmin -720 2>/dev/null` prints nothing (fetched more than 12 hours ago, or never), say so in one line and ask with AskUserQuestion: **Fetch first** (you run `git fetch origin`) or **Continue as is**. Never fetch unasked.

## 2. Role and language

Read `.debrief/config.json` (`{"role": "technical" | "business", "lang": "fr" | "en", "branch": "<name>"}`, every key optional). `--role`, `--lang` and `--branch` override it and are saved; any other `--lang` value: say only `fr` and `en` exist and ask which. Without `--lang` and without a saved `lang`, nothing is saved: the language follows the user's writing. When the file is missing and no `--role` was given, ask once with AskUserQuestion, in the user's language ("Métier ou Technique ?" in French, "Business or Technical?" in English):

- **Technical** (shown as "Technique" in French): quizzed on the code that changed, sees hotzones and risk areas.
- **Business** (shown as "Métier" in French): carries the business side, quizzed on features and business cases.

Run `mkdir -p .debrief`, then update `.debrief/config.json`, keeping the keys already there (never drop one key when writing another). Both quizzes are generated either way; the role only picks the view the page opens on.

## 3. Extract

```bash
python3 <skill-dir>/scripts/extract.py --repo . [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--branch NAME]
```

It prints the week folder (`.debrief/<ISO week>/`, absolute path); call it `<week>`. It writes `<week>/facts.json` and `<week>/digests/`, and adds `.debrief/` to `.git/info/exclude`. A rerun clears `digests/`, `summaries/` and `content.json`, never `results.json`. Relay each entry of `facts.warnings` to the user in one line.

`facts.json` holds the exact numbers: `commits`, `prs`, `hotzones`, `risks`, `blind_spots`, `repo_signals`, `sessions`, `priorities` (changed files sorted by score: blind spot 3, untested change, migration or new dependency 2 each, hotzone 1) and `digests`:

- `digests.features`: one `digests/feature-<n>.md` per merged PR (description, files, diff capped at 2000 lines).
- `digests.unlinked`: a list of `digests/commits-unlinked-<k>.md` for commits outside any PR (messages and file lists, no diff), `[]` when none.
- `digests.sessions`: one `digests/session-<id>.txt` per Claude session of this user on this repository, already redacted.
- `digests.trivial`: the feature digests of dependency, CI, test-only or release PRs; `assemble.py` writes their card.

## 4. Digest

Never read the digests and write `content.json` in one pass: summarize first. Run `mkdir -p <week>/summaries`. Skip the digests listed in `digests.trivial`: no subagent, no summary. Every other digest gets one summary named after its file stem: `digests/feature-42.md` → `summaries/feature-42.md`, `digests/session-<id>.txt` → `summaries/session-<id>.md`, `digests/commits-unlinked-<k>.md` → `summaries/commits-unlinked-<k>.md`. Feature and unlinked digests also get the page's card for their features, `summaries/<stem>.card.json`.

- **Up to 8 non-trivial digests** (unlinked digests count too): one subagent per digest, or summarize them yourself when there are only two or three.
- **More than 8**: split them into at most 8 batches of similar size and start one subagent per batch, all in one parallel wave. Never one subagent per file on a medium week.

Give each subagent the absolute paths of the repository, `<week>` and its digest files, the value of `facts.repo.ref`, `content.lang`, the first theme number of each unlinked digest (`10 × (k − 1) + 1` for `commits-unlinked-<k>`: 1, 11, 21…), and the instructions below, copied in full.

> You summarize digest files for a weekly team debrief. For each digest you are given, write `<week>/summaries/<digest file stem>.md`, about 40 lines at most. Read the digest; read repository files only to confirm a location, a test or a spec. Never run git commands that change anything. Write in English; quote the digest verbatim when you quote. Tag every business statement `[pr]` (stated in the PR title or description), `[spec]` (stated in a spec or doc in the repository; name the file), `[commit]` (stated in a commit message the author wrote) or `[inferred]` (read only in the code, no written source). When unsure, `[inferred]`.
>
> **feature-<n>.md** (one PR). Header: `PR #<n>: <title>`, its URL, the merge date. Then:
> - **Behavior**: what changes for the business or the end user, two or three plain sentences, tagged.
> - **Cases handled** and **Cases not handled**: one line each, tagged. Not handled: edge cases the code ignores, TODOs, rules nobody stated.
> - **Where the logic lives**: `path:line` (line at the analyzed ref `<ref>` given to you, read with `git show <ref>:<path>`, never from the working tree) and what that code decides. Three to six entries, the ones that carry the behavior.
> - **Why**: decisions and trade-offs, tagged; say when the PR gives no reason.
> - **Decisions**: each choice made among real alternatives: what was chosen, what was ruled out, the stated reason, tagged. "none" when the PR shows no alternative.
> - **Visible changes**: screens, messages, rules or removed behavior the end user will notice, tagged; or "none".
> - **Tests**: which tests cover it, or that none changed.
>
> **commits-unlinked-<k>.md** (commits outside any PR). Group the commits into a few themes (10 at most). For each theme: the short shas, then the same sections as a feature, without the URL. There is no diff: read `git show <sha>` to find the locations.
>
> **Card** (feature and unlinked digests only). Next to the summary, write `<week>/summaries/<digest file stem>.card.json`, in the language `<lang>` given to you (natural French when `fr`, phrased as a French writer would, not translated):
> `{"features": [{"id", "title", "summary", "cases_handled": [""], "cases_not_handled": [""], "provenance", "ref", "merged_on"}], "visible_changes": [{"feature_id", "text", "provenance"}], "open_questions": [{"feature_id", "text"}]}`
> - A PR is one feature: id `pr-<n>`, `ref` `{"pr": <n>, "url": "<url>"}`, `merged_on` the merge date (`YYYY-MM-DD`). Each unlinked theme is one feature: id `commits-<k>`, numbered from the first number given to you, `ref` `{"commit": "<short sha>"}`, `merged_on` the date of its last commit.
> - `summary` is two lines in business words. `provenance` (`pr`, `spec`, `commit` or `inferred`) is the weakest tag behind the summary; `inferred` only when no written source says it.
> - `visible_changes`: what the end user will notice, at most 2 per feature. `open_questions`: at most 2 per feature, only questions the client or product owner must answer (a business rule nobody stated, an expected behavior); technical doubts (CI, middleware, tests, infrastructure) go in `cases_not_handled`, never here. Both use one of this card's feature ids; `[]` when none.
>
> **session-<id>.txt** (one redacted Claude session). Header: session id, title, date, and the task in one sentence. Then notes for a weekly review, each with the event id it rests on (`e12`):
> - **Intent**: was the request clear about the expected result and its limits?
> - **Evidence**: what showed the result worked before it was accepted (tests, build, manual check), or nothing?
> - **Control**: scope drift, edits or commands nobody asked for, large edits accepted unread.
> - **Moments**: up to three worth showing, each with the event id, a short excerpt copied verbatim from this digest, why it matters, and a prompt that would have avoided it.
> - **Files and features touched**, so the session can be linked to the week's PRs.
>
> Work only from the digest: ignore the `extract.py show` command it mentions. Never copy anything from a raw transcript. Every summary ends in `.md`, session summaries included (`session-<id>.md`, never `.txt`).

Wait for every subagent, then check that each non-trivial digest has its summary. Summarize any missing one yourself.

## 5. Write content.json

```bash
python3 <skill-dir>/scripts/assemble.py <week> --lang <content.lang>
```

It writes `<week>/content.json` from the cards plus a card per trivial PR, with `quiz` and `review` left empty. It names on stderr each digest without a card: write that card yourself and rerun. It exits 1 on duplicate feature ids: fix the cards and rerun.

Then read `facts.json`, the assembled `content.json` and every file in `summaries/`, not the digests. Open a digest or a repository file only to check a detail. Fill only `quiz` and `review`; fix a card only when it is plainly wrong, never rewrite the cards. The summaries are in English; everything you write is in `content.lang`. The full shape:

```json
{
  "lang": "fr",
  "features": [{"id": "pr-42", "title": "", "summary": "", "cases_handled": [""], "cases_not_handled": [""],
                "provenance": "pr", "ref": {"pr": 42, "url": ""}, "merged_on": "2026-11-12"}],
  "visible_changes": [{"text": "", "feature_id": "pr-42", "provenance": "pr"}],
  "open_questions": [{"text": "", "feature_id": "pr-42"}],
  "quiz": {"business": [Q], "technical": [Q]},
  "review": {"repository": {"synthesis": {"intent": "", "evidence": "", "control": ""}, "notable": [{"ref": {"pr": 42, "url": ""}, "why": ""}]},
             "conversations": null},
  "retelling": null
}
```

Leave `retelling` null: it is filled only by step 9.

**Quiz.** Q is `{"id", "type": "choice" | "open" | "decision", "question", "choices", "answer", "explanation", "ref", "inferred"}`.

- 5 to 10 questions per role, scaled to the week (about one per feature, at least 5, at most 10). Zero for both roles only when `facts.commits` is empty. Ids are unique across both roles (`f1`…, `d1`…).
- **Pick topics in `facts.priorities` order**: walk the list from the top and map each path to its feature. Blind spots come first (work the user did not author), then risk areas; the user's own work only fills the remaining slots. One topic per question; cover as many features as the size allows.
- **Business**: business behavior and cases, in non-technical words, no file paths or code names: "A customer does X while Y. What happens?". `ref` is `{"pr": 42, "url": "…"}`, or `{"commit": "<short sha>"}` when the work has no PR.
- **Technical**: where a piece of logic lives, how the code behaves in a given case, why a choice was made. `ref` is `{"file": "path", "line": 12}` taken from the summaries, pointing to the analyzed ref `facts.repo.ref`: read lines with `git show <ref>:<path>`, never from the working tree. The file must exist in the repository or in this week's commits.
- Mostly `choice`: 3 or 4 `choices`, `answer` is the index of the only correct one, its position varies across questions, distractors are real (other files, other behaviors of this week) and have the same shape and length as the answer.
- "Why" questions are `open`: `answer` is the expected answer in one to three sentences; the user rates themselves against it.
- **Decision questions** (`decision`): one or two per role whenever the summaries' **Decisions** sections record a choice among real alternatives; none when they record none. `question` sets the context and asks what the user would choose (Business: a product choice, Technical: a design choice); `choices` are the options really considered, 2 to 4; `answer` is the index of what the team chose; `explanation` says why, and what the choice ruled out. The page makes the user pick and write what they rule out before it reveals the team's choice: a different choice is shown as "not the team's choice", never as wrong.
- `explanation` says what is true and where to read more; the page shows it after the answer.
- `"inferred": true` whenever the rule tested has no PR, spec or commit message behind it.
- No trivia: never ask for a name, a count, a date, a line number or an author.

**Repository review** (always written, team-level, never about one person). Build it from `facts.repo_signals` (`no_human_approval`, `ai_without_tests`, `empty_description`, `fast_large_merge`, `tests_weakened`), `facts.prs` and the summaries. `tests_weakened` means a PR removed assertions or test cases from test files while changing the code, the shape of tests rewritten until they pass: say so under **evidence** and list the PR in `notable`, after checking its summary for a stated reason (a removed feature is a fair reason). `synthesis`: **intent** (were changes described and scoped?), **evidence** (tests, human approvals), **control** (size and speed of merges, AI commits without tests), one to three sentences each. `notable`: the 3 to 5 most telling PRs or commits, `ref` `{"pr": 42, "url": "…"}` or `{"commit": "<short sha>"}`, `why` in one sentence. When there are no signals, say what went well and keep `notable` short.

**Conversation review.** `null` when `facts.sessions` is empty. Otherwise an object, never a string:

```json
{"synthesis": {"intent": "", "evidence": "", "control": "", "pen": ""},
 "moments": [{"session_id": "<facts.sessions[].id>", "excerpt": "", "why": "", "better_prompt": ""}]}
```

`synthesis` covers the user's week across sessions, one to three sentences per axis (trends: how requests were framed, edits accepted without proof, scope drift). **pen** (who holds the pen): did the user write the intent, the design and the definition of done in their own words, or mostly approve what Claude proposed? Start from `facts.sessions[].pen` (`messages`, `approvals`: messages that only say "ok", "go", "vas-y"), then the summaries; the page shows the counts, so give the reading, not the numbers again. `moments` holds the 3 to 5 most problematic moments. Sessions with `truncated: true` lack their middle part: claim nothing about what happened in the omitted part. Copy excerpts only from the session summaries or digests (already redacted), never from a raw transcript or from memory. `better_prompt` is ready to copy.

## 6. Validate

```bash
python3 <skill-dir>/scripts/content.py <week>
```

It prints `ok`, or one error per line (`quiz.technical[3].ref.line: expected an integer >= 1`). Fix `content.json` and rerun, at most 3 times. If errors remain, stop and show them to the user.

## 7. Render and serve

```bash
python3 <skill-dir>/scripts/render.py <week>
python3 <skill-dir>/scripts/serve.py <week>
```

`render.py` writes `<week>/index.html` (with past weeks' scores and hotzones) and prints its path. Run `serve.py` in the background: it serves the page on `127.0.0.1` only and saves quiz answers to `<week>/results.json`. Read the URL it prints and open it (`open <url>` on macOS, `xdg-open <url>` on Linux).

## 8. Report

One line: the period, the number of features, the quiz size per role, and the page (URL and `<week>/index.html`). One line more: the Quiz tab opens on a retelling of the week from memory; once written, the user asks you to compare it. If a warning says HEAD differs from `facts.repo.ref`, say that the quiz refs point to that ref, not to the working tree. Then one line on how to stop the server (ask you, or stop the background task); answers are saved to `<week>/results.json` only while the server runs, and a new run restores them from that file. Opened straight from disk, the page keeps answers in the browser for that file only, and they are not written to `results.json`.

## 9. Compare the retelling

When the user asks to compare their retelling ("compare ma restitution"), read `retelling.text` in `<week>/results.json`. If it is missing, say in one line that the retelling is saved there only while `serve.py` runs (step 7), and stop. Otherwise read `content.json` (features, visible changes) and the `summaries/`, then cut the retelling into the claims it makes and judge each one against what was written in the PRs, specs and commits:

- `same`: says what the sources say.
- `drift`: says something else about a feature (another rule, another user, another scope). Say in `note` what the sources say.
- `new`: in no source: an idea, a hope or a confusion with another feature. `feature_id` is null unless it extends a feature.
- `word`: the user's term is not the one the sources use for the same thing ("subscription" where the PR says "option"). `quote` is the user's word, `note` the term in the sources and why it matters (a wrong word now becomes logic in the wrong place later).

`quote` is copied verbatim from the retelling. `missing` lists, in `facts.priorities` order, the non-trivial features the retelling never mentions, at most 5, each with a one-line `note` on what it changed. Write in `content.lang`, never judge the person, only the gaps:

```json
"retelling": {"verdicts": [{"quote": "", "verdict": "same" | "drift" | "new" | "word", "feature_id": "pr-42" | null, "note": ""}],
              "missing": [{"feature_id": "pr-42", "note": ""}]}
```

Set it in `content.json`, run `content.py` (step 6), then `render.py`. Tell the user to reload the page: the comparison opens the Bilan tab. A rerun of `extract.py` drops `content.json`, so the comparison goes with it; the retelling itself stays in `results.json`.
