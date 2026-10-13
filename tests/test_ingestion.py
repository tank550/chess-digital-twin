import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

from ingestion.database import save_games
from ingestion.extract import get_all_chess_games, ingest_all_chess_games
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

            async def get(self, url):
                self.requested_urls.append(url)
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

    @patch.dict(os.environ, {"URL_CHESS_COM": "https://api.chess.com/pub/player/test"}, clear=True)
    async def test_ingestion_uses_player_from_api_url_and_saves_games(self):
        archive_game = {"pgn": "pgn", "url": "https://chess.com/game/1"}
        parsed_game = ({"game_id": "1"}, [])
        output = StringIO()
        with (
            patch(
                "ingestion.extract.get_all_chess_games",
                new=AsyncMock(return_value=[archive_game]),
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
        save.assert_called_once_with([parsed_game])
        self.assertIn("1/1 (100.0%)", output.getvalue())
        self.assertIn("Saved 1/1 games (100.0%).", output.getvalue())


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

        save_games([(game, [move])])

        function_call = connection.execute.call_args_list[-1]
        self.assertIn("upsert_chess_games", function_call.args[0])
        games_json, moves_json = function_call.args[1]
        self.assertIsInstance(games_json, Jsonb)
        self.assertIsInstance(moves_json, Jsonb)
        self.assertEqual(
            games_json.obj[0]["played_at"], "2024-12-16T00:00:00+00:00"
        )
        self.assertEqual(moves_json.obj, [{"game_id": "1", **move}])

    def test_schema_defines_bulk_jsonb_function(self):
        from ingestion.database import SCHEMA_PATH

        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE FUNCTION upsert_chess_games", schema)
        self.assertEqual(schema.count("jsonb_to_recordset"), 3)


if __name__ == "__main__":
    unittest.main()
