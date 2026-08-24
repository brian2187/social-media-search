import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["POST_LEDGER_DB"] = str(Path(self.tmp.name) / "t.sqlite")
        os.environ["POST_LEDGER_NO_OLLAMA"] = "1"
        import store
        import importlib

        importlib.reload(store)
        self.store = store

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("POST_LEDGER_DB", None)

    def test_insert_new_skips_existing(self):
        from categorize import categorize, topics_for
        from schema import empty_post

        a = empty_post(id="1", platform="x", author_handle="happiestocamper", text="Grok hello", post_kind="original")
        a["categories"] = categorize(a)
        a["topics"] = topics_for(a)
        b = empty_post(id="2", platform="x", author_handle="happiestocamper", text="second", post_kind="reply", reply_to_handle="elonmusk")
        b["categories"] = categorize(b)
        b["topics"] = topics_for(b)
        ins, skip = self.store.insert_new([a, b])
        self.assertEqual((ins, skip), (2, 0))
        ins2, skip2 = self.store.insert_new([a, b])
        self.assertEqual((ins2, skip2), (0, 2))
        self.assertEqual(self.store.count_posts(handle="happiestocamper"), 2)
        self.assertEqual(self.store.known_ids("x", "happiestocamper"), {"1", "2"})

    def test_accounts_isolated(self):
        from schema import empty_post

        self.store.insert_new(
            [
                empty_post(id="1", platform="x", author_handle="happiestocamper", text="a"),
                empty_post(id="2", platform="bluesky", author_handle="jay.bsky.team", text="other"),
            ]
        )
        mine = self.store.query(handle="happiestocamper", limit=0)
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["author_handle"], "happiestocamper")
        accts = { (a["platform"], a["handle"]) for a in self.store.accounts() }
        self.assertIn(("x", "happiestocamper"), accts)
        self.assertIn(("bluesky", "jay.bsky.team"), accts)

    def test_fx_status_maps_reply(self):
        from pull_public import _fx_status_to_post

        item = {
            "type": "status",
            "id": "99",
            "text": "@JobsNowPR hello",
            "created_at": "Sun Aug 23 22:55:11 +0000 2026",
            "created_timestamp": 1787525711,
            "likes": 7,
            "reposts": 2,
            "replies": 0,
            "views": 181,
            "author": {"id": "1527053178105044992", "screen_name": "RogueLou18", "name": "Lauren"},
            "replying_to": {"screen_name": "JobsNowPR", "status": "88"},
        }
        post = _fx_status_to_post(item, "RogueLou18")
        self.assertEqual(post["author_handle"], "RogueLou18")
        self.assertEqual(post["post_kind"], "reply")
        self.assertEqual(post["reply_to_handle"], "JobsNowPR")
        self.assertEqual(post["reply_to_id"], "88")
        self.assertTrue(post["created_at"].startswith("2026-08-23"))

    def test_topics_ai(self):
        from categorize import topics_for
        from schema import empty_post

        p = empty_post(text="Grok gets persistent memory")
        self.assertIn("ai", topics_for(p))


class SummarySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["POST_LEDGER_DB"] = str(Path(self.tmp.name) / "t.sqlite")
        os.environ["POST_LEDGER_NO_OLLAMA"] = "1"
        import store
        import summary
        import importlib

        importlib.reload(store)
        importlib.reload(summary)
        self.store = store
        self.summary = summary
        self.summary.TMP = Path(self.tmp.name) / "summaries"
        from schema import empty_post

        row = empty_post(
            id="9",
            platform="x",
            author_handle="happiestocamper",
            text="Grok subscription?",
            post_kind="reply",
            reply_to_handle="xai",
            reply_to_id="8",
        )
        from categorize import categorize, topics_for

        row["categories"] = categorize(row)
        row["topics"] = topics_for(row)
        self.store.insert_new([row])

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("POST_LEDGER_DB", None)

    def test_create_and_close_deletes(self):
        info = self.summary.create_session("happiestocamper", "x")
        self.assertTrue(info.get("ok"), info)
        token = info["token"]
        dest = self.summary.TMP / token
        self.assertTrue((dest / "index.html").is_file())
        html = (dest / "index.html").read_text(encoding="utf-8")
        self.assertIn("happiestocamper", html)
        self.assertIn("In response to", html)
        self.assertIn("@xai", html)
        self.assertTrue(self.summary.close_session(token))
        self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
