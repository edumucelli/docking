"""State and data helpers for the Random Trivia applet."""

from __future__ import annotations

import html
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.request import Request, urlopen

from docking.applets.trivia import meta
from docking.i18n import _
from docking.log import get_logger, with_context

_log = with_context(get_logger(name="trivia"), applet_id=meta.id)
_TRIVIA_ENDPOINT = "https://opentdb.com/api.php?amount={limit}"


@dataclass(frozen=True)
class TriviaEntry:
    category: str
    difficulty: str
    question: str
    answers: tuple[str, ...]
    correct_answer: str
    selected_answer: str = ""


FALLBACK_TRIVIA: tuple[TriviaEntry, ...] = (
    TriviaEntry(
        category="Computers",
        difficulty="easy",
        question="What does CPU stand for?",
        answers=(
            "Central Processing Unit",
            "Computer Personal Unit",
            "Central Process Utility",
            "Control Program Usage",
        ),
        correct_answer="Central Processing Unit",
    ),
    TriviaEntry(
        category="Science",
        difficulty="medium",
        question="What planet is known as the Red Planet?",
        answers=("Mars", "Venus", "Jupiter", "Mercury"),
        correct_answer="Mars",
    ),
    TriviaEntry(
        category="General Knowledge",
        difficulty="easy",
        question="How many days are there in a leap year?",
        answers=("366", "365", "364", "367"),
        correct_answer="366",
    ),
    TriviaEntry(
        category="History",
        difficulty="medium",
        question="Which ancient civilization built Machu Picchu?",
        answers=("Inca", "Maya", "Aztec", "Roman"),
        correct_answer="Inca",
    ),
    TriviaEntry(
        category="Books",
        difficulty="easy",
        question="Who wrote '1984'?",
        answers=(
            "George Orwell",
            "Aldous Huxley",
            "Ray Bradbury",
            "Jules Verne",
        ),
        correct_answer="George Orwell",
    ),
)


def normalize_text(text: str) -> str:
    clean = html.unescape(text).replace("\n", " ").replace("\r", " ").strip()
    return " ".join(clean.split())


def format_difficulty(difficulty: str) -> str:
    mapping = {
        "easy": _("Easy"),
        "medium": _("Medium"),
        "hard": _("Hard"),
    }
    return mapping.get(difficulty.lower(), difficulty.title())


def answer_entry(entry: TriviaEntry, answer: str) -> TriviaEntry:
    if answer not in entry.answers:
        return entry
    return replace(entry, selected_answer=answer)


def format_trivia(entry: TriviaEntry) -> str:
    header = _("{category} / {difficulty}").format(
        category=entry.category,
        difficulty=format_difficulty(entry.difficulty),
    )
    lines = [header, entry.question]
    if not entry.selected_answer:
        return "\n".join(lines)
    if entry.selected_answer == entry.correct_answer:
        lines.append(_("Correct: {answer}").format(answer=entry.correct_answer))
        return "\n".join(lines)
    lines.append(_("Your answer: {answer}").format(answer=entry.selected_answer))
    lines.append(_("Correct answer: {answer}").format(answer=entry.correct_answer))
    return "\n".join(lines)


def _http_get_json(url: str, timeout: float = 8.0) -> Any:
    request = Request(
        url=url,
        headers={"User-Agent": "DockingTriviaApplet/1.0 (+https://github.com/)"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    return json.loads(payload)


def _shuffle_answers(
    answers: list[str],
    shuffle: Callable[[list[str]], None] | None = None,
) -> tuple[str, ...]:
    shuffle_fn = shuffle or random.shuffle
    shuffled = list(answers)
    shuffle_fn(shuffled)
    return tuple(shuffled)


def _parse_results(
    data: Any,
    limit: int,
    shuffle_answers: Callable[[list[str]], None] | None = None,
) -> list[TriviaEntry]:
    if not isinstance(data, dict):
        return []
    if data.get("response_code") != 0:
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []

    entries: list[TriviaEntry] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_category = item.get("category")
        raw_difficulty = item.get("difficulty")
        raw_question = item.get("question")
        raw_correct = item.get("correct_answer")
        raw_incorrect = item.get("incorrect_answers")
        if not all(
            isinstance(value, str)
            for value in (raw_category, raw_difficulty, raw_question, raw_correct)
        ):
            continue
        if not isinstance(raw_incorrect, list) or not all(
            isinstance(answer, str) for answer in raw_incorrect
        ):
            continue
        category = normalize_text(raw_category)
        difficulty = normalize_text(raw_difficulty)
        question = normalize_text(raw_question)
        correct_answer = normalize_text(raw_correct)
        incorrect_answers = [
            normalize_text(answer) for answer in raw_incorrect if normalize_text(answer)
        ]
        if not category or not difficulty or not question or not correct_answer:
            continue
        answers = _shuffle_answers(
            [correct_answer, *incorrect_answers],
            shuffle=shuffle_answers,
        )
        entries.append(
            TriviaEntry(
                category=category,
                difficulty=difficulty,
                question=question,
                answers=answers,
                correct_answer=correct_answer,
            )
        )
        if len(entries) >= limit:
            break
    return entries


def fetch_trivia(
    limit: int = 20,
    http_get_json: Callable[[str], Any] | None = None,
    shuffle_answers: Callable[[list[str]], None] | None = None,
) -> list[TriviaEntry]:
    getter = http_get_json or _http_get_json
    try:
        data = getter(_TRIVIA_ENDPOINT.format(limit=limit))
        return _parse_results(
            data=data,
            limit=limit,
            shuffle_answers=shuffle_answers,
        )
    except Exception as exc:
        _log.bind(action="fetch_trivia").debug(
            "Failed to fetch trivia questions: %s",
            exc,
        )
        return []


def fallback_trivia() -> list[TriviaEntry]:
    return list(FALLBACK_TRIVIA)
