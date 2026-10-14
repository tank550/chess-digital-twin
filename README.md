# Chess Digital Twin

## Import Chess.com games into PostgreSQL

The ingestion command fetches every monthly archive for a Chess.com player,
parses each game's PGN and moves, creates the tables in `ingestion/schema.sql`
if necessary, and upserts the complete archive into PostgreSQL. Games and
moves are sent as JSONB batches to the `upsert_chess_games` PostgreSQL function,
which expands them with `jsonb_to_recordset` and replaces each imported game's
move rows within the same transaction. While importing, the command displays
the number and percentage of games parsed, then confirms when the database
batch has been saved.

1. Install the dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Configure these values in `.env` at the repository root:

   ```dotenv
   URL_CHESS_COM=https://api.chess.com/pub/player/your_chess_com_username
   DATABASE_URL=postgresql://username:password@localhost:5432/database_name
   ```

   `URL_CHESS_COM` is the public Chess.com player profile API URL, not the
   regular chess.com website URL. The player name is inferred from this URL;
   set `PLAYER_NAME=your_chess_com_username` only if you want to override it.
   Ensure the PostgreSQL database exists and is reachable with the supplied
   connection URL.

3. Run from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe -m ingestion.extract
   ```

   You can also run it from the `ingestion` folder:

   ```powershell
   python extract.py
   ```

The command is safe to rerun: existing games are updated, and their move rows
are replaced in the same database transaction. On its first run it imports
every archive. Later runs send each month's stored ETag with `If-None-Match`;
unchanged archives return HTTP 304 and their game bodies are not downloaded.
Games and changed ETags are committed together, so a failed parse or database
write cannot advance the cache and skip data on the next run. ETags are stored
in PostgreSQL, so they persist between ephemeral CI runs without Redis or a
local cache file.

## Daily GitHub Actions sync

The `.github/workflows/chess-ingestion.yml` workflow runs daily at 04:17 UTC
and can also be started manually from the repository's Actions tab. Configure
these repository Actions secrets before enabling it:

- `DATABASE_URL`: connection URL for PostgreSQL reachable from GitHub-hosted
  runners. A `localhost` URL will refer to the runner, not your PC.
- `URL_CHESS_COM`: the complete API URL, such as
  `https://api.chess.com/pub/player/your_username`. GitHub Actions does not
  expand variables from your local `.env` file.

The username is inferred from `URL_CHESS_COM`; no separate player-name secret
is needed. The local command continues to load values from `.env`.
