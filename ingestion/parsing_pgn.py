import io
import os
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, TypedDict
from urllib.parse import urlparse

import chess
import chess.pgn
from dotenv import load_dotenv

load_dotenv()


class Move_Details(TypedDict):
    ply: int
    fen_before: str
    move_uci: str
    move_san: str
    is_mine: bool
    clock_seconds: float | None


class Game_Details(TypedDict):
    game_id: str
    url: str
    played_at: datetime
    white_elo: int | None
    black_elo: int | None
    result: str
    my_color: str
    times_class: str
    time_control: str
    opening: str
    pgn: str


def _rating(
    headers: Mapping[str, str], color: str, metadata: Mapping[str, Any]
) -> int | None:
    player = metadata.get(color)
    rating = player.get("rating") if isinstance(player, dict) else None
    if rating is None:
        rating = headers.get(f"{color.title()}Elo")
    try:
        return int(rating) if rating not in (None, "", "?", "-") else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {color} player rating: {rating!r}") from exc


def _played_at(headers: Mapping[str, str], metadata: Mapping[str, Any]) -> datetime:
    end_time = metadata.get("end_time")
    if end_time is not None:
        try:
            return datetime.fromtimestamp(float(end_time), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise ValueError(f"Invalid game end_time: {end_time!r}") from exc

    raw_date = headers.get("UTCDate") or headers.get("Date")
    raw_time = headers.get("UTCTime") or "00:00:00"
    try:
        game_date = date.fromisoformat(raw_date.replace(".", "-"))
        game_time = time.fromisoformat(raw_time)
    except (AttributeError, ValueError) as exc:
        raise ValueError(
            f"PGN is missing a valid game date/time: {raw_date!r} {raw_time!r}"
        ) from exc
    return datetime.combine(game_date, game_time, tzinfo=timezone.utc)


def parse_pgn(
    pgn_string: str,
    game_metadata: Mapping[str, Any] | None = None,
    player_name: str | None = None,
) -> tuple[Game_Details, list[Move_Details]]:
    """Parse one Chess.com PGN into a game record and its mainline moves."""
    if not isinstance(pgn_string, str) or not pgn_string.strip():
        raise ValueError("PGN data must be a non-empty string.")

    game = chess.pgn.read_game(io.StringIO(pgn_string))
    if game is None:
        raise ValueError("The supplied PGN does not contain a game.")
    if game.errors:
        raise ValueError(f"PGN contains invalid moves or data: {game.errors[0]}")

    headers = game.headers
    metadata = game_metadata or {}
    player_name = (
        player_name if player_name is not None else os.getenv("PLAYER_NAME", "")
    ).strip()
    if not player_name:
        raise ValueError("PLAYER_NAME is required to identify your color in each game.")

    white_name = headers.get("White", "")
    black_name = headers.get("Black", "")
    if player_name.casefold() == white_name.casefold():
        my_color = chess.WHITE
    elif player_name.casefold() == black_name.casefold():
        my_color = chess.BLACK
    else:
        raise ValueError(
            f"PLAYER_NAME {player_name!r} does not match either player in the PGN."
        )

    url = headers.get("Link", "")
    game_id = urlparse(url).path.rstrip("/").split("/")[-1]
    if not game_id:
        raise ValueError("PGN is missing a valid Chess.com game Link header.")

    details: Game_Details = {
        "game_id": game_id,
        "url": url,
        "played_at": _played_at(headers, metadata),
        "white_elo": _rating(headers, "white", metadata),
        "black_elo": _rating(headers, "black", metadata),
        "result": headers.get("Result", ""),
        "my_color": "white" if my_color == chess.WHITE else "black",
        "times_class": str(metadata.get("time_class") or ""),
        "time_control": str(
            metadata.get("time_control") or headers.get("TimeControl", "")
        ),
        "opening": headers.get("ECO", ""),
        "pgn": pgn_string,
    }

    board = game.board()
    moves: list[Move_Details] = []
    for ply, node in enumerate(game.mainline()):
        move = node.move
        moves.append(
            {
                "ply": ply,
                "fen_before": board.fen(),
                "move_uci": move.uci(),
                "move_san": board.san(move),
                "is_mine": board.turn == my_color,
                "clock_seconds": node.clock(),
            }
        )
        board.push(move)

    return details, moves


if __name__ == "__main__":
    raise SystemExit("Run ingestion with: python -m ingestion.extract")
