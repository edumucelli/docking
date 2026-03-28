"""Tests for the Random Trivia applet."""

from unittest.mock import MagicMock, patch

import docking.applets.trivia.applet as trivia_mod
from docking.applets.trivia.applet import TriviaApplet
from docking.applets.trivia.render import draw_trivia_icon
from docking.applets.trivia.state import (
    TriviaEntry,
    answer_entry,
    fallback_trivia,
    fetch_trivia,
    format_difficulty,
    format_trivia,
    normalize_text,
)

ENTRY = TriviaEntry(
    category="Science",
    difficulty="medium",
    question="What planet is known as the Red Planet?",
    answers=("Mars", "Venus", "Jupiter", "Mercury"),
    correct_answer="Mars",
)


class TestTriviaHelpers:
    def test_normalize_text_decodes_entities(self):
        assert normalize_text("Tom &amp; Jerry") == "Tom & Jerry"

    def test_format_difficulty(self):
        assert format_difficulty("easy") == "Easy"
        assert format_difficulty("legendary") == "Legendary"

    def test_answer_entry_sets_selected_answer(self):
        updated = answer_entry(ENTRY, "Mars")
        assert updated.selected_answer == "Mars"

    def test_answer_entry_ignores_unknown_answer(self):
        updated = answer_entry(ENTRY, "Pluto")
        assert updated.selected_answer == ""

    def test_format_trivia_unanswered(self):
        text = format_trivia(ENTRY)
        assert "Science / Medium" in text
        assert "Red Planet" in text

    def test_format_trivia_wrong_answer(self):
        text = format_trivia(answer_entry(ENTRY, "Venus"))
        assert "Your answer: Venus" in text
        assert "Correct answer: Mars" in text

    def test_format_trivia_correct_answer(self):
        text = format_trivia(answer_entry(ENTRY, "Mars"))
        assert "Correct: Mars" in text


class TestFetchTrivia:
    @patch("docking.applets.trivia.state._http_get_json")
    def test_fetches_entries_from_api(self, mock_get):
        mock_get.return_value = {
            "response_code": 0,
            "results": [
                {
                    "category": "Science",
                    "difficulty": "medium",
                    "question": "What planet is known as the Red Planet?",
                    "correct_answer": "Mars",
                    "incorrect_answers": ["Venus", "Jupiter", "Mercury"],
                }
            ],
        }

        entries = fetch_trivia(
            limit=5,
            shuffle_answers=lambda answers: answers.reverse(),
        )

        assert len(entries) == 1
        assert entries[0].category == "Science"
        assert entries[0].correct_answer == "Mars"
        assert entries[0].answers[0] == "Mercury"

    @patch(
        "docking.applets.trivia.state._http_get_json",
        side_effect=RuntimeError("boom"),
    )
    def test_fetch_failure_returns_empty(self, _mock_get):
        assert fetch_trivia(limit=5) == []

    def test_fallback_has_entries(self):
        assert len(fallback_trivia()) >= 3


class TestTriviaApplet:
    def test_creates_with_icon(self):
        applet = TriviaApplet(48)
        assert applet.item.icon is not None
        assert applet._current is not None

    def test_click_advances_questions(self):
        applet = TriviaApplet(48)
        applet._entries = [
            ENTRY,
            TriviaEntry(
                category="Books",
                difficulty="easy",
                question="Who wrote 1984?",
                answers=("George Orwell", "Jules Verne"),
                correct_answer="George Orwell",
            ),
        ]
        applet._index = -1
        applet._current = None

        applet.on_clicked()
        assert applet._current == ENTRY

        applet.on_clicked()
        assert applet._current is not None
        assert "1984" in applet._current.question

    def test_exhausted_click_triggers_fetch(self):
        applet = TriviaApplet(48)
        applet._entries = [ENTRY]
        applet._index = 0
        applet._current = ENTRY

        with patch.object(applet, "_fetch_async") as fetch_mock:
            applet.on_clicked()

        fetch_mock.assert_called_once_with(show_first=True)

    def test_menu_contains_answers_and_actions(self):
        applet = TriviaApplet(48)
        applet._current = ENTRY

        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Open Trivia DB" in labels
        assert "Mars" in labels
        assert "Next Trivia" in labels
        assert "Refresh from Web" in labels

    def test_select_answer_updates_current_entry(self):
        applet = TriviaApplet(48)
        applet._current = ENTRY

        applet._select_answer("Venus")

        assert applet._current is not None
        assert applet._current.selected_answer == "Venus"
        assert "Correct answer: Mars" in applet.item.name

    def test_next_trivia_clears_answered_icon_state(self):
        applet = TriviaApplet(48)
        first = answer_entry(ENTRY, "Venus")
        second = TriviaEntry(
            category="Books",
            difficulty="easy",
            question="Who wrote 1984?",
            answers=("George Orwell", "Jules Verne"),
            correct_answer="George Orwell",
        )
        applet._entries = [first, second]
        applet._index = 0
        applet._current = first

        applet.on_clicked()

        assert applet._current is not None
        assert applet._current.selected_answer == ""


class TestTriviaRender:
    def test_draw_trivia_icon_supports_unanswered_and_answered_entries(self):
        import cairo

        unanswered = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
        answered = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)

        draw_trivia_icon(cr=cairo.Context(unanswered), size=64, entry=ENTRY)
        draw_trivia_icon(
            cr=cairo.Context(answered),
            size=64,
            entry=answer_entry(ENTRY, "Mars"),
        )

        assert unanswered.get_data().tobytes() != answered.get_data().tobytes()


class TestTriviaAppletBranches:
    def test_refresh_from_web_delegates(self):
        applet = TriviaApplet(48)
        applet._fetch_async = MagicMock()

        applet._refresh_from_web()

        applet._fetch_async.assert_called_once_with(show_first=True)

    def test_fetch_async_noop_when_loading(self):
        applet = TriviaApplet(48)
        applet._loading = True
        applet.present = MagicMock()

        applet._fetch_async(show_first=True)

        applet.present.assert_not_called()

    def test_fetch_async_runs_worker_and_posts_idle_result(self, monkeypatch):
        applet = TriviaApplet(48)
        monkeypatch.setattr(
            trivia_mod,
            "fetch_trivia",
            lambda limit: [ENTRY],
        )
        calls = []
        monkeypatch.setattr(
            trivia_mod.GLib,
            "idle_add",
            lambda cb, entries, show_first: calls.append((cb, entries, show_first)),
        )

        class FakeThread:
            def __init__(self, target, daemon):
                self._target = target
                self.daemon = daemon

            def start(self):
                self._target()

        monkeypatch.setattr(trivia_mod.threading, "Thread", FakeThread)

        applet._fetch_async(show_first=True)

        assert calls
        assert calls[0][0] == applet._on_fetch_result
        assert calls[0][1] == [ENTRY]
        assert calls[0][2] is True

    def test_on_fetch_result_applies_entries_and_fallback(self, monkeypatch):
        applet = TriviaApplet(48)
        applet._loading = True
        applet.present = MagicMock()

        assert applet._on_fetch_result([ENTRY], show_first=True) is False
        assert applet._current == ENTRY

        applet._current = None
        monkeypatch.setattr(trivia_mod, "fallback_trivia", lambda: [ENTRY])
        assert applet._on_fetch_result([], show_first=False) is False
        assert applet._current == ENTRY

    def test_refresh_tooltip_loading_state(self):
        applet = TriviaApplet(48)
        applet._loading = True
        applet._current = None

        applet.refresh_tooltip()

        assert "loading" in applet.item.name.lower()
