import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import feed


class FeedTests(unittest.TestCase):
    def test_member_mentions_are_resolved_to_discord_display_names(self):
        content = "ask <@42>, <@!84>, and <@999>"
        mentions = [
            {
                "id": "42",
                "username": "account-name",
                "global_name": "Global Name",
                "member": {"nick": "Server Nick"},
            },
            {
                "id": "84",
                "username": "account-name-2",
                "global_name": "Second Person",
            },
        ]
        self.assertEqual(
            feed.resolve_member_mentions(content, mentions),
            "ask @Server Nick, @Second Person, and <@999>",
        )

    def test_high_confidence_credentials_are_redacted_before_storage(self):
        samples = [
            "sk-" + "a" * 40,
            "ghp_" + "b" * 36,
            "xoxb-" + "1234567890-abcdefghij",
            "AIza" + "c" * 35,
            "M" + "d" * 23 + ".abcdef." + "e" * 27,
        ]
        message = "ordinary context " + " ".join(samples) + " remains"
        redacted = feed.redact_credentials(message)
        self.assertEqual(
            redacted,
            "ordinary context "
            + " ".join([feed.CREDENTIAL_REDACTION] * len(samples))
            + " remains",
        )
        self.assertEqual(
            feed.redact_credentials("observer theory and september event"),
            "observer theory and september event",
        )

    def test_pull_channel_paginates_in_message_order(self):
        page = [{"id": str(i)} for i in range(100, 0, -1)]
        with patch.object(feed, "api", side_effect=[(page, None), ([], None)]) as api:
            messages, error = feed.pull_channel("channel", 0)
        self.assertIsNone(error)
        self.assertEqual(messages[0]["id"], "1")
        self.assertEqual(messages[-1]["id"], "100")
        self.assertIn("after=100", api.call_args_list[-1].args[0])

    def test_archived_public_threads_use_archive_timestamp_cursor(self):
        first = {
            "threads": [{
                "id": "20",
                "thread_metadata": {"archive_timestamp": "2026-01-02T00:00:00+00:00"},
            }],
            "has_more": True,
        }
        second = {"threads": [], "has_more": False}
        with patch.object(feed, "api", side_effect=[(first, None), (second, None)]) as api:
            threads, error = feed.archived_threads("parent", "public")
        self.assertIsNone(error)
        self.assertEqual([thread["id"] for thread in threads], ["20"])
        self.assertIn("before=2026-01-02T00%3A00%3A00%2B00%3A00", api.call_args_list[-1].args[0])

    def test_private_archive_permission_failure_is_explicit(self):
        with patch.object(feed, "api", return_value=(None, 403)):
            threads, error = feed.archived_threads("parent", "private")
        self.assertEqual(threads, [])
        self.assertEqual(error, 403)

    def test_archive_window_excludes_threads_archived_before_the_cutoff(self):
        # 2026-01-02T00:00:00+03:00 is 21:00 UTC — before the 22:00 UTC
        # cutoff in absolute time even though its wall-clock reads later.
        # The thread is out of window: it must not become a pull target,
        # and pagination must stop rather than walk the older archive.
        page = {
            "threads": [
                {
                    "id": "19",
                    "thread_metadata": {
                        "archive_timestamp": "2026-01-02T01:30:00+03:00",
                    },
                },
                {
                    "id": "20",
                    "thread_metadata": {
                        "archive_timestamp": "2026-01-02T00:00:00+03:00",
                    },
                },
            ],
            "has_more": True,
        }
        cutoff = datetime(2026, 1, 1, 22, tzinfo=timezone.utc).timestamp()
        with patch.object(feed, "api", return_value=(page, None)) as api:
            threads, error = feed.archived_threads(
                "parent", "public", stop_before=cutoff
            )
        self.assertIsNone(error)
        self.assertEqual([thread["id"] for thread in threads], ["19"])
        api.assert_called_once()

    def test_joined_private_archives_filter_the_window_but_keep_walking(self):
        # The joined-private route pages by thread ID, not archive time, so
        # an out-of-window thread is dropped without ending the walk: later
        # pages can still contain in-window threads.
        first = {
            "threads": [
                {
                    "id": "30",
                    "thread_metadata": {
                        "archive_timestamp": "2026-01-01T12:00:00+00:00",
                    },
                },
                {
                    "id": "29",
                    "thread_metadata": {
                        "archive_timestamp": "2025-12-01T00:00:00+00:00",
                    },
                },
            ],
            "has_more": True,
        }
        second = {
            "threads": [{
                "id": "28",
                "thread_metadata": {
                    "archive_timestamp": "2026-01-01T13:00:00+00:00",
                },
            }],
            "has_more": False,
        }
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
        with patch.object(feed, "api", side_effect=[(first, None), (second, None)]) as api:
            threads, error = feed.archived_threads(
                "parent", "joined_private", stop_before=cutoff
            )
        self.assertIsNone(error)
        self.assertEqual([thread["id"] for thread in threads], ["30", "28"])
        self.assertIn("before=29", api.call_args_list[-1].args[0])

    def test_member_roster_becomes_alias_tokens_not_a_second_message_source(self):
        page = [
            {
                "nick": "Defender Prime",
                "user": {"id": "2", "username": "defender", "global_name": None},
            },
            {
                "nick": None,
                "user": {"id": "3", "username": "Aella", "global_name": "Aella Girl"},
            },
        ]
        with patch.object(feed, "api", return_value=(page, None)):
            aliases, count, error = feed.member_aliases("guild")
        self.assertIsNone(error)
        self.assertEqual(count, 2)
        self.assertIn("defender", aliases)
        self.assertIn("prime", aliases)
        self.assertIn("aella", aliases)


if __name__ == "__main__":
    unittest.main()
