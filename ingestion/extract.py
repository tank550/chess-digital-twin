import asyncio
import os
import sys
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from dotenv import load_dotenv

if __package__:
    from .database import save_games
    from .parsing_pgn import parse_pgn
else:
    from database import save_games
    from parsing_pgn import parse_pgn

load_dotenv()

USER_AGENT = "chess-digital-twin/1.0"
PROGRESS_BAR_WIDTH = 30


def _print_progress(current: int, total: int) -> None:
    if total <= 0:
        raise ValueError("Progress total must be greater than zero.")

    percentage = current / total * 100
    filled = round(PROGRESS_BAR_WIDTH * current / total)
    bar = "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)
    sys.stdout.write(
        f"\rParsing games: [{bar}] {current}/{total} ({percentage:.1f}%)"
    )
    sys.stdout.flush()


def _player_api_url() -> str:
    url = os.getenv("URL_CHESS_COM", "").strip().rstrip("/")
    if not url:
        raise ValueError(
            "URL_CHESS_COM is required and must be the Chess.com player API URL."
        )
    return url


def _player_name() -> str:
    player_name = os.getenv("PLAYER_NAME", "").strip()
    if player_name:
        return player_name

    path_parts = [part for part in urlparse(_player_api_url()).path.split("/") if part]
    if len(path_parts) >= 3 and path_parts[-3:-1] == ["pub", "player"]:
        return unquote(path_parts[-1])

    raise ValueError(
        "Set PLAYER_NAME or use a Chess.com player API URL ending in "
        "/pub/player/<username>."
    )


async def _get_archive_urls(client: httpx.AsyncClient) -> list[str]:
    archives_url = f"{_player_api_url()}/games/archives"
    response = await client.get(archives_url)
    response.raise_for_status()
    payload = response.json()

    archives = payload.get("archives") if isinstance(payload, dict) else None
    if not isinstance(archives, list) or not all(
        isinstance(archive_url, str) for archive_url in archives
    ):
        raise ValueError(
            f"Chess.com returned an invalid archives response: {archives_url}"
        )
    return archives


async def get_chess_archive() -> list[str]:
    """Return every monthly archive URL for the configured Chess.com player."""
    async with httpx.AsyncClient(
        timeout=30, headers={"User-Agent": USER_AGENT}
    ) as client:
        return await _get_archive_urls(client)


async def get_all_chess_games() -> list[dict[str, Any]]:
    """Fetch every game from all monthly archives of the configured player."""
    async with httpx.AsyncClient(
        timeout=30, headers={"User-Agent": USER_AGENT}
    ) as client:
        archive_urls = await _get_archive_urls(client)
        games: list[dict[str, Any]] = []
        for archive_url in archive_urls:
            archive_response = await client.get(archive_url)
            archive_response.raise_for_status()
            archive = archive_response.json()
            archive_games = archive.get("games") if isinstance(archive, dict) else None
            if not isinstance(archive_games, list):
                raise ValueError(
                    f"Chess.com returned an invalid games response: {archive_url}"
                )
            for game in archive_games:
                if not isinstance(game, dict) or not isinstance(game.get("pgn"), str):
                    raise ValueError(
                        f"Chess.com returned a game without PGN data: {archive_url}"
                    )
                games.append(game)

    return games


async def ingest_all_chess_games() -> int:
    """Fetch, parse, and persist the player's complete Chess.com game history."""
    player_name = _player_name()
    archive_games = await get_all_chess_games()
    parsed_games = []
    total = len(archive_games)
    if total == 0:
        print("No games found in the Chess.com archives.")
    else:
        _print_progress(0, total)
        for index, game in enumerate(archive_games, start=1):
            try:
                parsed_games.append(
                    parse_pgn(
                        game["pgn"], game_metadata=game, player_name=player_name
                    )
                )
            except ValueError as exc:
                print()
                raise ValueError(
                    f"Failed to parse Chess.com game {game.get('url', '<unknown>')}: {exc}"
                ) from exc
            _print_progress(index, total)
        print()

    print(f"Saving {total} games and their moves to PostgreSQL...")
    save_games(parsed_games)
    if total:
        print(f"Saved {total}/{total} games (100.0%).")
    else:
        print("Database save complete.")
    return len(parsed_games)


if __name__ == "__main__":
    count = asyncio.run(ingest_all_chess_games())
    print(f"Successfully ingested {count} Chess.com games.")
