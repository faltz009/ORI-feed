import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from build import validate_output
from weather import ReferenceData, WeatherAnalyzer, candidate_ngram_spans, token_stream


CONFIG = {
    "minimum_word_count": 2,
    "minimum_phrase_count": 2,
    "minimum_speakers": 2,
    "word_reference_minimum_lift": 2,
    "bigram_reference_minimum_lift": 2,
    "trigram_minimum_aboutness_terms": 2,
    "topic_minimum_pair_count": 2,
    "topic_minimum_association": 0,
    "topic_bigram_minimum_count": 2,
    "topic_bigram_minimum_speakers": 2,
    "topic_bigram_minimum_days": 2,
    "topic_maximum_reference_ppm": 150,
    "topic_minimum_lift": 2,
    "topic_resolution": 1,
    "exclude_words": [],
    "exclude_phrases": [],
}


class WeatherTests(unittest.TestCase):
    SAMPLE_MESSAGES = [
        ("u1", "memetics egregore cultural evolution open research institute"),
        ("u2", "memetics egregore cultural evolution research institute"),
        ("u1", "agents models inference neural training"),
        ("u2", "agents models inference neural training"),
    ]

    def analyzer(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        for index, (speaker, content) in enumerate(self.SAMPLE_MESSAGES):
            analyzer.observe(
                content=content,
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
                reactions=[],
                attachments=[],
            )
        return analyzer

    def test_bundled_reference_recognizes_broad_english(self):
        reference = ReferenceData.load(BASE / "data" / "reference")
        analyzer = WeatherAnalyzer(30, CONFIG)
        for word in ("little", "well", "work", "system", "use", "year", "possible"):
            with self.subTest(word=word):
                self.assertGreater(reference.word_rate(word) * 1_000_000, 150)
                self.assertFalse(
                    analyzer.aboutness_word(word, reference, lift=100_000),
                    "a broad-English word must not become weather even at extreme local lift",
                )

    def test_reference_loader_fails_closed_on_incomplete_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "count_1w.txt").write_text("well\t1\n", encoding="utf-8")
            (directory / "count_2w.txt").write_text("very well\t1\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "integrity check"):
                ReferenceData.load(directory)

    def semantic_pass(self, analyzer, reference, messages, aliases=frozenset()):
        """Replay test messages after their phrase inventory is discovered."""
        analyzer.member_alias_tokens = set(aliases)
        analyzer.begin_semantic_pass(analyzer.phrase_rows(2, reference))
        for index, (speaker, content) in enumerate(messages):
            analyzer.observe_semantic(
                content=content,
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
            )

    def test_member_aliases_are_removed_from_lexicon_phrases_and_topics(self):
        analyzer = self.analyzer()
        reference = ReferenceData(
            words={"research": 1000, "open": 1000},
            bigrams={"open research": 100},
        )
        self.semantic_pass(analyzer, reference, self.SAMPLE_MESSAGES, {"egregore"})
        report = analyzer.finalize(
            reference,
            server={"id": "guild", "name": "Test"},
            coverage={"member_count": 2},
        )
        serialized_features = json.dumps({
            "lexicon": report["lexicon"],
            "phrases": report["phrases"],
            "topics": report["topics"],
            "semantic_graph": report["semantic_graph"],
        })
        self.assertNotIn("egregore", serialized_features)

    def test_lexicon_uses_baseline_as_gate_and_adoption_as_rank(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        messages = [
            ("u1", "culture culture"),
            ("u2", "culture culture"),
            ("u3", "culture culture"),
            ("u1", "xylophone egregore"),
            ("u2", "xylophone egregore"),
        ]
        for index, (speaker, content) in enumerate(messages):
            analyzer.observe(
                content=content,
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
                reactions=[],
                attachments=[],
            )
        analyzer.member_alias_tokens = set()
        reference = ReferenceData(
            words={"culture": 100, "xylophone": 1, "ordinary": 999_899},
            bigrams={},
        )

        rows = analyzer.lexicon(reference)
        by_term = {row["term"]: row for row in rows}

        # Both ratios are beyond the 100x saturation point, so culture's
        # broader community adoption owns the more prominent rank.
        self.assertEqual(rows[0]["term"], "culture")
        self.assertGreater(by_term["xylophone"]["lift"], by_term["culture"]["lift"])
        self.assertEqual(by_term["culture"]["reference_status"], "measured")
        self.assertAlmostEqual(by_term["culture"]["reference_expected"], 0.001)
        self.assertEqual(by_term["egregore"]["reference_status"], "not_found")
        self.assertIsNone(by_term["egregore"]["reference_expected"])
        self.assertIsNone(by_term["egregore"]["lift"])

    def test_topics_are_derived_without_message_text_in_output(self):
        analyzer = self.analyzer()
        reference = ReferenceData(words={}, bigrams={})
        self.semantic_pass(analyzer, reference, self.SAMPLE_MESSAGES)
        report = analyzer.finalize(
            reference,
            server={"id": "guild", "name": "Test"},
            coverage={"member_count": 2},
        )
        self.assertTrue(report["topics"])
        self.assertTrue(all(topic["units"] for topic in report["topics"]))
        self.assertTrue(all(
            len(unit["series"]) == 30
            for topic in report["topics"]
            for unit in topic["units"]
        ))
        self.assertTrue(report["semantic_graph"]["edges"])
        self.assertTrue(all(
            len(edge["series"]) == 30
            for edge in report["semantic_graph"]["edges"]
        ))
        visible_terms = {
            unit["term"]
            for topic in report["topics"]
            for unit in topic["units"]
        }
        self.assertTrue(all(
            edge["source"] in visible_terms and edge["target"] in visible_terms
            for edge in report["semantic_graph"]["edges"]
        ))
        validate_output(report)
        serialized = json.dumps(report)
        self.assertNotIn("memetics egregore cultural evolution", serialized)

    def test_privacy_validator_rejects_attributed_fields(self):
        with self.assertRaises(RuntimeError):
            validate_output({"topics": [], "user_id": "discord:1"})

    def test_topic_morphology_folds_only_an_observed_singular(self):
        analyzer = self.analyzer()
        self.assertEqual(analyzer.topic_canonical("models"), "models")
        analyzer.words["model"] = 1
        self.assertEqual(analyzer.topic_canonical("models"), "model")
        self.assertEqual(analyzer.topic_canonical("consciousness"), "consciousness")

    def test_ordinary_words_can_form_a_meaningful_repeated_phrase(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        for index, speaker in enumerate(("u1", "u2", "u1", "u2")):
            analyzer.observe(
                content="september event planning",
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
                reactions=[],
                attachments=[],
            )
        analyzer.member_alias_tokens = set()
        reference = ReferenceData(
            words={"september": 100_000, "event": 100_000, "planning": 100_000},
            bigrams={},
        )
        phrases = analyzer.phrase_rows(2, reference)
        self.assertIn("september event", [row["term"] for row in phrases])

    def test_qualified_bigram_becomes_a_first_class_weather_unit(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        messages = [
            (speaker, "september event planning")
            for speaker in ("u1", "u2", "u1", "u2")
        ]
        for index, (speaker, content) in enumerate(messages):
            analyzer.observe(
                content=content,
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
                reactions=[],
                attachments=[],
            )
        reference = ReferenceData(
            words={"september": 100_000, "event": 100_000, "planning": 100_000},
            bigrams={},
        )
        self.semantic_pass(analyzer, reference, messages)
        report = analyzer.finalize(
            reference,
            server={"id": "guild", "name": "Test"},
            coverage={},
        )
        units = [
            unit
            for topic in report["topics"]
            for unit in topic["units"]
        ]
        september_event = next(unit for unit in units if unit["term"] == "september event")
        self.assertEqual(september_event["kind"], "bigram")

    def test_bigram_occurrence_does_not_duplicate_component_word_evidence(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        messages = [
            (speaker, "observer theory and theory with observer")
            for speaker in ("u1", "u2", "u1", "u2")
        ]
        for index, (speaker, content) in enumerate(messages):
            analyzer.observe(
                content=content,
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
                reactions=[],
                attachments=[],
            )
        reference = ReferenceData(words={}, bigrams={})
        self.semantic_pass(analyzer, reference, messages)
        self.assertEqual(analyzer.semantic_counts["observer theory"], 4)
        self.assertEqual(analyzer.semantic_counts["observer"], 4)
        self.assertEqual(analyzer.semantic_counts["theory"], 4)

        report = analyzer.finalize(
            reference,
            server={"id": "guild", "name": "Test"},
            coverage={},
        )
        units = {
            unit["term"]: unit
            for topic in report["topics"]
            for unit in topic["units"]
        }
        self.assertTrue(units["observer theory"]["label_visible"])
        self.assertFalse(units["observer"]["label_visible"])
        self.assertFalse(units["theory"]["label_visible"])

    def test_explicit_internet_lingo_blacklist_applies_everywhere(self):
        config = {**CONFIG, "exclude_words": ["lol", "lmao"]}
        analyzer = WeatherAnalyzer(30, config)
        analyzer.member_alias_tokens = set()
        self.assertFalse(analyzer.reportable_word("lol"))
        self.assertFalse(analyzer.reportable_word("lmao"))

    def test_common_bigrams_need_local_lift(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        for index, speaker in enumerate(("u1", "u2", "u1", "u2")):
            analyzer.observe(
                content="long time september event",
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
                reactions=[],
                attachments=[],
            )
        analyzer.member_alias_tokens = set()
        reference = ReferenceData(
            words={"long": 1, "time": 1, "september": 1, "event": 1},
            bigrams={"long time": 1_000_000, "all other bigrams": 1},
        )
        phrases = {row["term"] for row in analyzer.phrase_rows(2, reference)}
        self.assertNotIn("long time", phrases)
        self.assertIn("september event", phrases)

    def test_phrases_do_not_cross_punctuation_or_newlines(self):
        tokens, boundaries = token_stream(
            "observer theory. Mind and world\nmodel; good faith"
        )
        bigrams = {
            phrase for _index, phrase in candidate_ngram_spans(tokens, 2, boundaries)
        }
        self.assertIn("observer theory", bigrams)
        self.assertIn("good faith", bigrams)
        self.assertNotIn("theory mind", bigrams)
        self.assertNotIn("world model", bigrams)

    def test_trigrams_need_two_topic_bearing_terms(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        for index, speaker in enumerate(("u1", "u2", "u1", "u2")):
            analyzer.observe(
                content="merely the accumulated genetic trait selection",
                timestamp=f"2026-07-{index + 1:02d}T00:00:00+00:00",
                channel="general",
                speaker=speaker,
                reactions=[],
                attachments=[],
            )
        analyzer.member_alias_tokens = set()
        reference = ReferenceData(
            words={"merely": 1_000_000, "accumulated": 1_000_000},
            bigrams={},
        )
        phrases = {row["term"] for row in analyzer.phrase_rows(3, reference)}
        self.assertNotIn("merely the accumulated", phrases)
        self.assertIn("genetic trait selection", phrases)

    def test_movement_uses_prior_history_without_an_aboutness_gate(self):
        analyzer = WeatherAnalyzer(30, CONFIG)
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        for week in range(28):
            when = start + timedelta(weeks=week)
            for speaker in ("u1", "u2"):
                phrase = " september event" if week >= 24 else ""
                analyzer.observe(
                    content=f"community discussion knowledge{phrase}",
                    timestamp=when.isoformat(),
                    channel="general",
                    speaker=speaker,
                    reactions=[],
                    attachments=[],
                    current=week >= 24,
                )
        analyzer.member_alias_tokens = set()
        movement = analyzer.movement()
        self.assertEqual(movement["status"], "ready")
        september_event = next(
            row for row in movement["rising"] if row["term"] == "september event"
        )
        self.assertEqual(september_event["change"], "new")
        self.assertNotIn("momentum", september_event)
        self.assertNotIn("september", [row["term"] for row in movement["rising"]])

    def test_named_people_are_aggregate_but_raw_text_is_absent(self):
        analyzer = self.analyzer()
        analyzer.people_names.update({"u1": "Alice", "u2": "Bob"})
        reference = ReferenceData(words={}, bigrams={})
        self.semantic_pass(analyzer, reference, self.SAMPLE_MESSAGES)
        report = analyzer.finalize(
            reference,
            server={"id": "guild", "name": "Test"},
            coverage={},
        )
        self.assertEqual({row["name"] for row in report["people"]}, {"Alice", "Bob"})
        validate_output(report)
        self.assertNotIn("memetics egregore cultural evolution", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
