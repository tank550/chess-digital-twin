import os
from pathlib import Path
from typing import Mapping, Sequence

import psycopg
from psycopg.types.json import Jsonb

if __package__:
    from .parsing_pgn import Game_Details, Move_Details
else:
    from parsing_pgn import Game_Details, Move_Details

ParsedGame = tuple[Game_Details, list[Move_Details]]
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def load_archive_etags() -> dict[str, str]:
    """Load the persisted HTTP ETags for monthly Chess.com archives."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError(
            "DATABASE_URL is required. Set it to your PostgreSQL connection URL."
        )

    with psycopg.connect(database_url) as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            "SELECT resource_url, etag FROM chess_archive_etags WHERE etag IS NOT NULL"
        ).fetchall()
    return {resource_url: etag for resource_url, etag in rows}


def save_games(
    games: Sequence[ParsedGame],
    archive_etags: Mapping[str, str | None] | None = None,
) -> None:
    """Atomically upsert games, moves, and ETags for processed archives."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError(
            "DATABASE_URL is required. Set it to your PostgreSQL connection URL."
        )

    with psycopg.connect(database_url) as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))

        games_payload = []
        moves_payload = []
        for game, moves in games:
            games_payload.append(
                {
                    "game_id": game["game_id"],
                    "url": game["url"],
                    "played_at": game["played_at"].isoformat(),
                    "my_color": game["my_color"],
                    "white_elo": game["white_elo"],
                    "black_elo": game["black_elo"],
                    "result": game["result"],
                    "time_class": game["times_class"],
                    "time_control": game["time_control"],
                    "opening": game["opening"],
                    "pgn": game["pgn"],
                }
            )
            moves_payload.extend(
                {"game_id": game["game_id"], **move} for move in moves
            )

        connection.execute(
            "SELECT upsert_chess_games(%s::JSONB, %s::JSONB)",
            (Jsonb(games_payload), Jsonb(moves_payload)),
        )
        if archive_etags:
            etag_payload = [
                {"resource_url": resource_url, "etag": etag}
                for resource_url, etag in archive_etags.items()
            ]
            connection.execute(
                """
                INSERT INTO chess_archive_etags (resource_url, etag)
                SELECT state.resource_url, state.etag
                FROM jsonb_to_recordset(%s::JSONB) AS state(
                    resource_url TEXT,
                    etag TEXT
                )
                ON CONFLICT (resource_url) DO UPDATE SET
                    etag = EXCLUDED.etag,
                    updated_at = NOW()
                """,
                (Jsonb(etag_payload),),
            )
