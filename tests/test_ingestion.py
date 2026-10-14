import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

from ingestion.database import load_archive_etags, save_games
from ingestion.extract import (
    get_all_chess_games,
    get_changed_chess_games,
    ingest_all_chess_games,
)
from ingestion.parsing_pgn import parse_pgn
from psycopg.types.json import Jsonb


PGN = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2024.12.16"]
[Round "-"]
[White "tank_550"]
[Black "opponent"]
[Result "1-0"]
[UTCDate "2024.12.16"]
[UTCTime "19:50:12"]
[WhiteElo "155"]
[BlackElo "305"]
[TimeControl "300+0"]
[Link "https://www.chess.com/game/live/128117436733"]

1. e4 {[%clk 0:04:59.8]} 1... e5 {[%clk 0:04:58.2]} 2. Nf3 1-0
"""


class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {"URL_CHESS_COM": "https://api.chess.com/pub/player/test"})
    async def test_fetches_games_from_every_archive(self):
        first_game = {"pgn": "first pgn"}
        second_game = {"pgn": "second pgn"}

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
                self.status_code = 200
                self.headers = {"ETag": '"archive-etag"'}

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        class FakeClient:
            def __init__(self):
                self.requested_urls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def get(self, url, headers=None):
                self.requested_urls.append((url, headers or {}))
                responses = {
                    "https://api.chess.com/pub/player/test/games/archives": {
                        "archives": ["https://archive/one", "https://archive/two"]
                    },
                    "https://archive/one": {"games": [first_game]},
                    "https://archive/two": {"games": [second_game]},
                }
                return FakeResponse(responses[url])

        fake_client = FakeClient()
        with patch("ingestion.extract.httpx.AsyncClient", return_value=fake_client):
            games = await get_all_chess_games()

        self.assertEqual(games, [first_game, second_game])
        self.assertEqual(len(fake_client.requested_urls), 3)
        self.assertEqual(fake_client.requested_urls[1][1], {})

    @patch.dict(os.environ, {"URL_CHESS_COM": "https://api.chess.com/pub/player/test"}, clear=True)
    async def test_ingestion_uses_player_from_api_url_and_saves_games(self):
        archive_game = {"pgn": "pgn", "url": "https://chess.com/game/1"}
        parsed_game = ({"game_id": "1"}, [])
        etag_updates = {"https://archive/month": '"new-etag"'}
        output = StringIO()
        with (
            patch(
                "ingestion.extract.load_archive_etags",
                return_value={},
            ),
            patch(
                "ingestion.extract.get_changed_chess_games",
                new=AsyncMock(
                    return_value=([archive_game], etag_updates, 0, 1)
                ),
            ),
            patch("ingestion.extract.parse_pgn", return_value=parsed_game) as parse,
            patch("ingestion.extract.save_games") as save,
            redirect_stdout(output),
        ):
            count = await ingest_all_chess_games()

        self.assertEqual(count, 1)
        parse.assert_called_once_with(
            "pgn", game_metadata=archive_game, player_name="test"
        )
        save.assert_called_once_with([parsed_game], etag_updates)
        self.assertIn("1/1 (100.0%)", output.getvalue())
        self.assertIn("Saved 1/1 changed games (100.0%).", output.getvalue())

    @patch.dict(os.environ, {"URL_CHESS_COM": "https://api.chess.com/pub/player/test"})
    async def test_changed_archive_sends_if_none_match_and_skips_304_body(self):
        class FakeResponse:
            def __init__(self, status_code, payload=None, etag=None):
                self.status_code = status_code
                self.payload = payload
                self.headers = {"ETag": etag} if etag else {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise AssertionError("Unexpected HTTP failure")

            def json(self):
                return self.payload

        class FakeClient:
            def __init__(self):
                self.archive_headers = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def get(self, url, headers=None):
                if url.endswith("/games/archives"):
                    return FakeResponse(
                        200, {"archives": ["https://archive/old"]}, '"index"'
                    )
                self.archive_headers = headers
                return FakeResponse(304)

        fake_client = FakeClient()
        with patch("ingestion.extract.httpx.AsyncClient", return_value=fake_client):
            games, etag_updates, unchanged, checked = await get_changed_chess_games(
                {"https://archive/old": 'W/"old"'}
            )

        self.assertEqual(games, [])
        self.assertEqual(etag_updates, {})
        self.assertEqual(unchanged, 1)
        self.assertEqual(checked, 1)
        self.assertEqual(
            fake_client.archive_headers, {"If-None-Match": 'W/"old"'}
        )

    @patch.dict(os.environ, {"URL_CHESS_COM": "https://api.chess.com/pub/player/test"})
    async def test_changed_archive_captures_new_etag_and_games(self):
        archive_game = {"pgn": "updated pgn", "url": "https://chess.com/game/1"}

        class FakeResponse:
            status_code = 200
            headers = {"ETag": '"new-etag"'}

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def get(self, url, headers=None):
                if url.endswith("/games/archives"):
                    return FakeResponse({"archives": ["https://archive/current"]})
                return FakeResponse({"games": [archive_game]})

        with patch(
            "ingestion.extract.httpx.AsyncClient", return_value=FakeClient()
        ):
            games, etag_updates, unchanged, checked = await get_changed_chess_games({})

        self.assertEqual(games, [archive_game])
        self.assertEqual(etag_updates, {"https://archive/current": '"new-etag"'})
        self.assertEqual(unchanged, 0)
        self.assertEqual(checked, 1)


class ParsePgnTests(unittest.TestCase):
    @patch.dict(os.environ, {"PLAYER_NAME": "tank_550"})
    def test_parses_game_metadata_and_moves(self):
        details, moves = parse_pgn(
            PGN,
            game_metadata={
                "time_class": "blitz",
                "time_control": "300+0",
                "end_time": 1734378612,
                "white": {"rating": 160},
            },
        )

        self.assertEqual(details["game_id"], "128117436733")
        self.assertEqual(details["my_color"], "white")
        self.assertEqual(details["white_elo"], 160)
        self.assertEqual(details["black_elo"], 305)
        self.assertEqual(details["times_class"], "blitz")
        self.assertEqual(details["time_control"], "300+0")
        self.assertEqual(details["played_at"].utcoffset().total_seconds(), 0)
        self.assertEqual(len(moves), 3)
        self.assertEqual(moves[0]["move_san"], "e4")
        self.assertTrue(moves[0]["is_mine"])
        self.assertAlmostEqual(moves[0]["clock_seconds"], 299.8)
        self.assertFalse(moves[1]["is_mine"])

    @patch.dict(os.environ, {"PLAYER_NAME": "missing_player"})
    def test_rejects_games_that_do_not_include_configured_player(self):
        with self.assertRaisesRegex(ValueError, "does not match either player"):
            parse_pgn(PGN)

    @patch.dict(os.environ, {}, clear=True)
    def test_requires_player_name(self):
        with self.assertRaisesRegex(ValueError, "PLAYER_NAME is required"):
            parse_pgn(PGN)


class DatabaseTests(unittest.TestCase):
    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://test"})
    @patch("ingestion.database.psycopg.connect")
    def test_sends_games_and_all_moves_as_jsonb_batches(self, connect):
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        game = {
            "game_id": "1",
            "url": "https://www.chess.com/game/live/1",
            "played_at": datetime(2024, 12, 16, tzinfo=timezone.utc),
            "my_color": "white",
            "white_elo": 100,
            "black_elo": 100,
            "result": "1-0",
            "times_class": "blitz",
            "time_control": "300+0",
            "opening": "",
            "pgn": PGN,
        }
        move = {
            "ply": 0,
            "fen_before": "start",
            "move_uci": "e2e4",
            "move_san": "e4",
            "is_mine": True,
            "clock_seconds": 299.8,
        }

        save_games([(game, [move])], {"https://archive/month": '"etag-1"'})

        function_call = connection.execute.call_args_list[-2]
        self.assertIn("upsert_chess_games", function_call.args[0])
        games_json, moves_json = function_call.args[1]
        self.assertIsInstance(games_json, Jsonb)
        self.assertIsInstance(moves_json, Jsonb)
        self.assertEqual(
            games_json.obj[0]["played_at"], "2024-12-16T00:00:00+00:00"
        )
        self.assertEqual(moves_json.obj, [{"game_id": "1", **move}])
        etag_call = connection.execute.call_args_list[-1]
        self.assertIn("INSERT INTO chess_archive_etags", etag_call.args[0])
        self.assertEqual(
            etag_call.args[1][0].obj,
            [{"resource_url": "https://archive/month", "etag": '"etag-1"'}],
        )

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://test"})
    @patch("ingestion.database.psycopg.connect")
    def test_loads_persisted_archive_etags(self, connect):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [
            ("https://archive/month", '"etag-1"')
        ]
        connect.return_value.__enter__.return_value = connection

        etags = load_archive_etags()

        self.assertEqual(etags, {"https://archive/month": '"etag-1"'})

    def test_schema_defines_bulk_jsonb_function(self):
        from ingestion.database import SCHEMA_PATH

        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE FUNCTION upsert_chess_games", schema)
        self.assertEqual(schema.count("jsonb_to_recordset"), 3)


if __name__ == "__main__":
    unittest.main()
