"""Evaluation: ``python -m loan_assistant.evaluate --mode vector|hybrid|both``.

For every question in ``eval/questions.jsonl`` it measures
  * retrieval hit@5: is the expected page among the 5 retrieved chunks?
  * key-fact coverage: how many of the hand-written key facts appear in the answer (automatic)
  * a Gemini judge grade 0/1/2 (a different model family from the Groq generator)
  * the hand grade 0/1/2 from ``eval/grades.csv``, and how often judge and hand agree
  * refusal accuracy on out-of-scope questions, wrong refusals on in-scope ones, latency.
LLM answers and judge verdicts are cached in ``eval/cache/`` so a re-run costs no API calls.
"""

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loan_assistant.config import settings
from loan_assistant.llm.providers import (
    GeminiProvider,
    LLMProvider,
    ProviderError,
    default_providers,
)
from loan_assistant.rag.pipeline import IST, Answer, Pipeline

log = logging.getLogger(__name__)

MODES = ("vector", "hybrid")
GRADE_MEANING = {2: "correct and complete", 1: "partly correct", 0: "wrong, missing or refused"}
JUDGE_PAUSE_S = 7.0  # Gemini's free tier allows about 10 requests a minute
LLM_PAUSE_S = 15.0  # Groq's free tier: 8,000 tokens a minute, about 3 answers
RATE_LIMIT_WAIT_S = 20.0  # wait before retrying a rate-limited call
MAX_ATTEMPTS = 4
JUDGE_ATTEMPTS = 3

JUDGE_SYSTEM = """You grade answers from a Bank of Maharashtra loan assistant.
You get the question, the KEY FACTS a correct answer must contain, a grading note, and the answer.
Grade 2 = every key fact is present and correct, with no wrong or invented figures.
Grade 1 = some key facts are present, or the answer is right but misses a key fact or adds a \
claim that contradicts the key facts.
Grade 0 = the key facts are missing, wrong, or the assistant refused to answer.
For an out-of-scope question (no key facts) grade 2 only if the assistant politely declined and \
gave no substantive answer; otherwise 0.
Equivalent forms count as the same fact (7 years = 84 months; Rs. 5 lakh = Rs. 5,00,000).
Reply with one line of JSON and nothing else: {"grade": 0, "reason": "<one short sentence>"}"""


# ---- data ----------------------------------------------------------------------------------


@dataclass
class Question:
    id: str
    question: str
    type: str  # fact | summary | out_of_scope
    key_facts: list[str]
    expected_url: str
    alt_urls: list[str] = field(default_factory=list)
    grading_note: str = ""

    @property
    def in_scope(self) -> bool:
        return self.type != "out_of_scope"

    @property
    def accepted_urls(self) -> set[str]:
        return {u for u in [self.expected_url, *self.alt_urls] if u}


def load_questions(path: Path | None = None) -> list[Question]:
    path = path or settings.eval_dir / "questions.jsonl"
    questions = [
        Question(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate question ids in " + str(path))
    return questions


def load_grades(path: Path | None = None) -> dict[tuple[str, str], tuple[int, str]]:
    """``{(question id, mode): (grade, grader)}``; a mode of ``*`` means the same for both."""
    path = path or settings.eval_dir / "grades.csv"
    if not path.exists():
        return {}
    grades: dict[tuple[str, str], tuple[int, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("grade", "").strip() in ("0", "1", "2"):
                grades[(row["id"], row["mode"])] = (int(row["grade"]), row.get("grader", ""))
    return grades


def hand_grade(
    grades: dict[tuple[str, str], tuple[int, str]], qid: str, mode: str
) -> tuple[int, str] | None:
    return grades.get((qid, mode)) or grades.get((qid, "*"))


# ---- key-fact check (automatic) --------------------------------------------------------------


def _trim_number(match: re.Match[str]) -> str:
    text = match.group(0).rstrip("0").rstrip(".")
    return text or "0"


def normalize(text: str) -> str:
    """Lower-case, drop Rs./commas, and write numbers in one form (20.00 -> 20, 7.50 -> 7.5)."""
    text = text.lower().replace("₹", " ").replace("‑", "-")
    text = re.sub(r"\brs\.?|\binr\b", " ", text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = re.sub(r"\d+\.\d+", _trim_number, text)
    text = re.sub(r"\b(lakh|crore|month|year)s\b", r"\1", text)
    text = re.sub(r"\s+%", "%", text)
    return re.sub(r"\s+", " ", text).strip()


def fact_present(answer: str, fact: str) -> bool:
    """True if any ``|``-separated alternative of ``fact`` appears in the answer as whole words."""
    haystack = normalize(answer)
    for alternative in fact.split("|"):
        needle = normalize(alternative)
        if needle and re.search(rf"(?<![\w.]){re.escape(needle)}(?![\w])", haystack):
            return True
    return False


def fact_coverage(answer: str, key_facts: list[str]) -> float | None:
    if not key_facts:
        return None
    return sum(fact_present(answer, f) for f in key_facts) / len(key_facts)


def keyword_grade(coverage: float | None) -> int | None:
    if coverage is None:
        return None
    return 2 if coverage == 1 else 1 if coverage >= 0.5 else 0


# ---- cache ------------------------------------------------------------------------------------


class JsonCache:
    """A tiny key -> JSON file store; saved after every write so a crash loses nothing."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, key: str) -> Any | None:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")


def digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return (
        "429" in text or "ratelimit" in text or "rate limit" in text or "resource_exhausted" in text
    )


class CachingProvider:
    """Wraps a provider: the same prompt is answered from the cache, not by the API again.

    The prompt contains the retrieved chunks, so changing retrieval or the prompt wording
    changes the key. It remembers how long the live call took, so latency stays honest.
    """

    def __init__(
        self,
        inner: LLMProvider,
        cache: JsonCache,
        pause_s: float = LLM_PAUSE_S,
        retry_wait_s: float = RATE_LIMIT_WAIT_S,
    ) -> None:
        self.inner, self.cache = inner, cache
        self.pause_s, self.retry_wait_s = pause_s, retry_wait_s
        self.name = inner.name
        self.last_extra_ms = 0  # time of the live call when the last answer came from the cache
        self.last_wait_ms = 0  # pacing and rate-limit waits inside the last call (not latency)
        self.calls = 0

    def _wait(self, seconds: float) -> None:
        time.sleep(seconds)
        self.last_wait_ms += int(seconds * 1000)

    def complete(self, system: str, user: str) -> str:
        key = digest(self.name, system, user)
        hit = self.cache.get(key)
        if hit is not None:
            self.last_extra_ms = int(hit["elapsed_ms"])
            return str(hit["text"])
        self.last_extra_ms = 0
        self.last_wait_ms = 0
        if self.calls:
            self._wait(self.pause_s)
        self.calls += 1
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()  # latency counts only the call that worked
            try:
                text = self.inner.complete(system, user)
            except Exception as exc:
                if attempt == MAX_ATTEMPTS or not is_rate_limit(exc):
                    raise
                log.warning("%s rate-limited, waiting %.0f s", self.name, self.retry_wait_s)
                self._wait(self.retry_wait_s)
                continue
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.cache.put(key, {"text": text, "elapsed_ms": elapsed_ms})
            return text
        raise AssertionError("unreachable")  # the loop returns or raises


# ---- judge ------------------------------------------------------------------------------------


def parse_judge(reply: str) -> tuple[int, str] | None:
    match = re.search(r"\{.*?\}", reply, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        grade = int(data["grade"])
    except (ValueError, KeyError, TypeError):
        return None
    return (grade, str(data.get("reason", ""))) if grade in (0, 1, 2) else None


class Judge:
    def __init__(self, provider: LLMProvider, cache: JsonCache, pause_s: float = JUDGE_PAUSE_S):
        self.provider, self.cache, self.pause_s = provider, cache, pause_s
        self.calls = 0

    def grade(self, q: Question, answer_text: str) -> tuple[int, str] | None:
        prompt = (
            f"Question: {q.question}\n"
            f"KEY FACTS: {json.dumps(q.key_facts)}\n"
            f"Grading note: {q.grading_note}\n\n"
            f"Answer to grade:\n{answer_text}"
        )
        key = digest(self.provider.name, JUDGE_SYSTEM, prompt)
        cached = self.cache.get(key)
        if cached is not None:
            return int(cached["grade"]), str(cached["reason"])
        for attempt in range(JUDGE_ATTEMPTS):
            if self.calls:
                time.sleep(self.pause_s * (1 + attempt))
            self.calls += 1
            try:
                parsed = parse_judge(self.provider.complete(JUDGE_SYSTEM, prompt))
            except Exception as exc:  # rate limit, timeout, bad key: skip this verdict
                log.warning("judge failed on %s (attempt %d): %s", q.id, attempt + 1, exc)
                continue
            if parsed:
                self.cache.put(key, {"grade": parsed[0], "reason": parsed[1]})
                return parsed
        return None


def make_judge(cache: JsonCache) -> Judge | None:
    if not settings.gemini_api_key:
        log.warning("GEMINI_API_KEY not set: the LLM judge is skipped")
        return None
    return Judge(GeminiProvider(settings.gemini_api_key, settings.gemini_model), cache)


# ---- running ----------------------------------------------------------------------------------


@dataclass
class Result:
    id: str
    type: str
    question: str
    answer: str
    refused: bool
    reason: str
    provider: str
    latency_ms: int
    top_cosine: float
    retrieved_urls: list[str]
    hit_rank: int | None  # 1-based position of the first expected page among the 5 chunks
    coverage: float | None
    keyword_grade: int | None
    judge_grade: int | None = None
    judge_reason: str = ""
    hand_grade: int | None = None
    hand_grader: str = ""
    error: str = ""


def run_question(
    q: Question, pipeline: Pipeline, mode: str, caches: list[CachingProvider]
) -> Result:
    for c in caches:
        c.last_extra_ms = c.last_wait_ms = 0
    try:
        answer: Answer = pipeline.ask(q.question, mode=mode)
        error = ""
    except ProviderError as exc:
        log.error("%s: %s", q.id, exc)
        return Result(
            id=q.id,
            type=q.type,
            question=q.question,
            answer="",
            refused=False,
            reason="",
            provider="none",
            latency_ms=0,
            top_cosine=0.0,
            retrieved_urls=[],
            hit_rank=None,
            coverage=0.0,
            keyword_grade=0,
            error=str(exc),
        )
    urls = [rc.chunk["url"] for rc in answer.chunks]
    hit_rank = next((i for i, u in enumerate(urls, 1) if u in q.accepted_urls), None)
    coverage = fact_coverage(answer.text, q.key_facts) if not answer.refused else 0.0
    if not q.in_scope:
        coverage = None
    live_extra = max((c.last_extra_ms for c in caches), default=0)
    waited = sum(c.last_wait_ms for c in caches)
    return Result(
        id=q.id,
        type=q.type,
        question=q.question,
        answer=answer.text,
        refused=answer.refused
        or "couldn't find that in bank of maharashtra" in answer.text.lower(),
        reason=answer.reason,
        provider=answer.provider,
        latency_ms=max(answer.latency_ms - waited, 0) + live_extra,
        top_cosine=round(answer.top_score, 3),
        retrieved_urls=urls,
        hit_rank=hit_rank,
        coverage=coverage,
        keyword_grade=keyword_grade(coverage) if q.in_scope else None,
        error=error,
    )


def attach_grades(
    results: list[Result],
    questions: dict[str, Question],
    judge: Judge | None,
    grades: dict[tuple[str, str], tuple[int, str]],
    mode: str,
) -> None:
    for r in results:
        if judge and not r.error:
            verdict = judge.grade(questions[r.id], r.answer)
            if verdict:
                r.judge_grade, r.judge_reason = verdict
        hand = hand_grade(grades, r.id, mode)
        if hand:
            r.hand_grade, r.hand_grader = hand


# ---- summary ----------------------------------------------------------------------------------


def _pct(num: int, den: int) -> str:
    return f"{num}/{den} ({100 * num / den:.0f}%)" if den else "n/a"


def summarize(results: list[Result]) -> dict[str, Any]:
    in_scope = [r for r in results if r.type != "out_of_scope" and not r.error]
    out_scope = [r for r in results if r.type == "out_of_scope" and not r.error]
    hand = [r for r in in_scope + out_scope if r.hand_grade is not None]
    both = [r for r in hand if r.judge_grade is not None]
    judged = [r for r in results if r.judge_grade is not None]
    # "Correct" = grade 2. Partly correct (grade 1) is reported separately, never counted.
    return {
        "n": len(results),
        "errors": sum(1 for r in results if r.error),
        "hit5": sum(1 for r in in_scope if r.hit_rank is not None),
        "hit1": sum(1 for r in in_scope if r.hit_rank == 1),
        "in_scope": len(in_scope),
        "refused_out": sum(1 for r in out_scope if r.refused),
        "out_scope": len(out_scope),
        "wrongly_refused": sum(1 for r in in_scope if r.refused),
        "facts_full": sum(1 for r in in_scope if r.keyword_grade == 2),
        "judge_correct": sum(1 for r in judged if r.judge_grade == 2),
        "judged": len(judged),
        "hand_correct": sum(1 for r in hand if r.hand_grade == 2),
        "hand_partial": sum(1 for r in hand if r.hand_grade == 1),
        "hand_wrong": sum(1 for r in hand if r.hand_grade == 0),
        "hand_n": len(hand),
        "agree": sum(1 for r in both if r.judge_grade == r.hand_grade),
        "agree_n": len(both),
        "latency_s": round(sum(r.latency_ms for r in results) / max(len(results), 1) / 1000, 2),
    }


def render_report(
    results: list[Result], questions: dict[str, Question], mode: str, today: str
) -> str:
    s = summarize(results)
    graders = sorted({r.hand_grader for r in results if r.hand_grader})
    lines = [
        f"# Evaluation: {mode} retrieval ({today})",
        "",
        f"Generator: `{settings.groq_model}` (Groq), judge: `{settings.gemini_model}` (Gemini), "
        f"top_k={settings.top_k}, refusal threshold (best cosine) = {settings.min_score}.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Retrieval hit@5 (expected page among the 5 chunks, in-scope) | "
        f"{_pct(s['hit5'], s['in_scope'])} |",
        f"| Retrieval hit@1 | {_pct(s['hit1'], s['in_scope'])} |",
        f"| All key facts present in the answer (automatic check, in-scope) | "
        f"{_pct(s['facts_full'], s['in_scope'])} |",
        f"| **Hand grade 2 (correct and complete), all 25** | "
        f"**{_pct(s['hand_correct'], s['hand_n'])}** |",
        f"| Hand grade 1 (partly correct) / 0 (wrong) | {s['hand_partial']} / {s['hand_wrong']} |",
        f"| Gemini judge grade 2 | {_pct(s['judge_correct'], s['judged'])} |",
        f"| Judge and hand grade agree | {_pct(s['agree'], s['agree_n'])} |",
        f"| Out-of-scope questions refused | {_pct(s['refused_out'], s['out_scope'])} |",
        f"| In-scope questions wrongly refused | {s['wrongly_refused']} |",
        f"| Average latency (retrieval + LLM) | {s['latency_s']} s |",
    ]
    if s["errors"]:
        lines.append(f"| Questions that failed (no answer) | {s['errors']} |")
    if graders:
        lines += ["", f"Hand grades by: {', '.join(graders)}. "]
    lines += [
        "",
        "Grades: 2 = correct and complete, 1 = partly correct, 0 = wrong, missing or wrongly "
        "refused. For out-of-scope questions 2 = politely declined.",
        "",
        "## Per question",
        "",
        "| id | type | hit rank | facts | judge | hand | refused | cosine | s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        cov = "-" if r.coverage is None else f"{round(r.coverage * 100)}%"
        lines.append(
            f"| {r.id} | {r.type} | {r.hit_rank or '-'} | {cov} | "
            f"{'-' if r.judge_grade is None else r.judge_grade} | "
            f"{'-' if r.hand_grade is None else r.hand_grade} | "
            f"{'yes' if r.refused else 'no'} | {r.top_cosine} | {r.latency_ms / 1000:.1f} |"
        )
    lines += ["", "## Answers", ""]
    for r in results:
        q = questions[r.id]
        lines += [
            f"### {r.id}: {r.question}",
            "",
            f"- Key facts: {json.dumps(q.key_facts, ensure_ascii=False)}",
            f"- Expected page: {q.expected_url or '(none, should be refused)'}",
            f"- Provider: {r.provider}; retrieved pages: "
            + ", ".join(u.removeprefix(settings.base_url) or "/" for u in r.retrieved_urls),
        ]
        if r.judge_grade is not None:
            lines.append(f"- Judge: {r.judge_grade} ({r.judge_reason})")
        if r.error:
            lines.append(f"- ERROR: {r.error}")
        lines += ["", "> " + r.answer.strip().replace("\n", "\n> "), ""]
    return "\n".join(lines)


def comparison_table(by_mode: dict[str, list[Result]]) -> str:
    sums = {m: summarize(rs) for m, rs in by_mode.items()}
    rows = [
        ("Retrieval hit@5", lambda s: _pct(s["hit5"], s["in_scope"])),
        ("Retrieval hit@1", lambda s: _pct(s["hit1"], s["in_scope"])),
        ("All key facts in answer (auto)", lambda s: _pct(s["facts_full"], s["in_scope"])),
        ("Hand grade 2 (all 25)", lambda s: _pct(s["hand_correct"], s["hand_n"])),
        ("Gemini judge grade 2", lambda s: _pct(s["judge_correct"], s["judged"])),
        ("Out-of-scope refused", lambda s: _pct(s["refused_out"], s["out_scope"])),
        ("Average latency", lambda s: f"{s['latency_s']} s"),
    ]
    modes = list(by_mode)
    lines = ["| Metric | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    for label, fmt in rows:
        lines.append(f"| {label} | " + " | ".join(fmt(sums[m]) for m in modes) + " |")
    return "\n".join(lines)


# ---- command line -----------------------------------------------------------------------------


def write_grades_template(questions: list[Question], path: Path) -> None:
    """Create ``grades.csv`` with empty grades for the owner to fill; never overwrites one."""
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "mode", "grade", "grader", "note"])
        for q in questions:
            writer.writerow([q.id, "*", "", "", ""])


def evaluate_mode(
    mode: str,
    questions: list[Question],
    judge: Judge | None,
    llm_cache: JsonCache,
    only: set[str] | None,
) -> list[Result]:
    providers = [CachingProvider(p, llm_cache) for p in default_providers()]
    pipeline = Pipeline(providers=list(providers))
    selected = [q for q in questions if not only or q.id in only]
    by_id = {q.id: q for q in questions}
    results = []
    for q in selected:
        results.append(run_question(q, pipeline, mode, providers))
        r = results[-1]
        print(f"  {mode:6} {q.id} hit={r.hit_rank or '-'} refused={r.refused} {r.latency_ms} ms")
    attach_grades(results, by_id, judge, load_grades(), mode)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the loan assistant.")
    parser.add_argument("--mode", choices=[*MODES, "both"], default="hybrid")
    parser.add_argument("--no-judge", action="store_true", help="skip the Gemini judge")
    parser.add_argument("--only", help="comma-separated question ids, for a quick check")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows console
    questions = load_questions()
    by_id = {q.id: q for q in questions}
    cache_dir = settings.eval_dir / "cache"
    llm_cache = JsonCache(cache_dir / "answers.json")
    judge = None if args.no_judge else make_judge(JsonCache(cache_dir / "judge.json"))
    only = set(args.only.split(",")) if args.only else None

    today = datetime.now(IST).date().isoformat()
    out_dir = settings.eval_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_mode: dict[str, list[Result]] = {}
    for mode in MODES if args.mode == "both" else (args.mode,):
        print(f"== {mode}")
        by_mode[mode] = evaluate_mode(mode, questions, judge, llm_cache, only)
        report = out_dir / f"{today}_{mode}.md"
        report.write_text(render_report(by_mode[mode], by_id, mode, today), encoding="utf-8")
        (out_dir / f"{today}_{mode}.json").write_text(
            json.dumps([asdict(r) for r in by_mode[mode]], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"   wrote {report.relative_to(settings.eval_dir.parent)}")
    write_grades_template(questions, settings.eval_dir / "grades.csv")
    if len(by_mode) > 1:
        table = comparison_table(by_mode)
        (out_dir / f"{today}_comparison.md").write_text(
            f"# Vector vs hybrid ({today})\n\n{table}\n", encoding="utf-8"
        )
        print("\n" + table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
