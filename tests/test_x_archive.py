from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from x_archive import parse_folder
from categorize import categorize
from schema import empty_post


def test_fixture_parses():
    rows = parse_folder(Path(__file__).resolve().parent, handle="happiestocamper")
    # also accept tests/data/tweets.js via rglob from tests/
    assert len(rows) == 2, rows
    by_id = {r["id"]: r for r in rows}
    a = by_id["1001"]
    assert a["platform"] == "x"
    assert a["author_handle"] == "happiestocamper"
    assert a["post_kind"] == "original"
    assert "Dayshore" in a["hashtags"]
    assert "has_link" in a["categories"]
    b = by_id["1002"]
    assert b["post_kind"] == "reply"
    assert "reply" in b["categories"]


def test_question_tag():
    p = empty_post(text="Who is walking up?", post_kind="original")
    tags = categorize(p)
    assert "question" in tags


if __name__ == "__main__":
    test_fixture_parses()
    test_question_tag()
    print("TEST_OK")
