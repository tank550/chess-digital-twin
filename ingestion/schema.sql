CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    played_at TIMESTAMPTZ NOT NULL,
    my_color TEXT NOT NULL CHECK (my_color IN ('white', 'black')),
    white_elo INT,
    black_elo INT,
    result TEXT,
    time_class TEXT,
    time_control TEXT,
    opening TEXT,
    pgn TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moves (
    game_id TEXT REFERENCES games(game_id),
    ply INT NOT NULL,
    fen_before TEXT NOT NULL,
    move_uci TEXT NOT NULL,
    move_san TEXT NOT NULL,
    is_mine BOOLEAN NOT NULL,
    clock_seconds REAL,
    PRIMARY KEY (game_id, ply)
);

CREATE TABLE IF NOT EXISTS chess_archive_etags (
    resource_url TEXT PRIMARY KEY,
    etag TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION upsert_chess_games(
    p_games JSONB,
    p_moves JSONB
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO games (
        game_id, url, played_at, my_color, white_elo, black_elo,
        result, time_class, time_control, opening, pgn
    )
    SELECT
        game_rows.game_id,
        game_rows.url,
        game_rows.played_at,
        game_rows.my_color,
        game_rows.white_elo,
        game_rows.black_elo,
        game_rows.result,
        game_rows.time_class,
        game_rows.time_control,
        game_rows.opening,
        game_rows.pgn
    FROM jsonb_to_recordset(COALESCE(p_games, '[]'::JSONB)) AS game_rows(
        game_id TEXT,
        url TEXT,
        played_at TIMESTAMPTZ,
        my_color TEXT,
        white_elo INT,
        black_elo INT,
        result TEXT,
        time_class TEXT,
        time_control TEXT,
        opening TEXT,
        pgn TEXT
    )
    ON CONFLICT (game_id) DO UPDATE SET
        url = EXCLUDED.url,
        played_at = EXCLUDED.played_at,
        my_color = EXCLUDED.my_color,
        white_elo = EXCLUDED.white_elo,
        black_elo = EXCLUDED.black_elo,
        result = EXCLUDED.result,
        time_class = EXCLUDED.time_class,
        time_control = EXCLUDED.time_control,
        opening = EXCLUDED.opening,
        pgn = EXCLUDED.pgn;

    DELETE FROM moves AS existing_moves
    USING (
        SELECT DISTINCT game_rows.game_id
        FROM jsonb_to_recordset(COALESCE(p_games, '[]'::JSONB)) AS game_rows(
            game_id TEXT
        )
    ) AS imported_games
    WHERE existing_moves.game_id = imported_games.game_id;

    INSERT INTO moves (
        game_id, ply, fen_before, move_uci, move_san, is_mine, clock_seconds
    )
    SELECT
        move_rows.game_id,
        move_rows.ply,
        move_rows.fen_before,
        move_rows.move_uci,
        move_rows.move_san,
        move_rows.is_mine,
        move_rows.clock_seconds
    FROM jsonb_to_recordset(COALESCE(p_moves, '[]'::JSONB)) AS move_rows(
        game_id TEXT,
        ply INT,
        fen_before TEXT,
        move_uci TEXT,
        move_san TEXT,
        is_mine BOOLEAN,
        clock_seconds REAL
    );
END;
$$;

/*CREATE TABLE IF NOT EXISTS engine_eval (
 game_id TEXT,
 ply INT,
 engine_version TEXT,
 depth INT,
 eval_cp_before INT,
 eval_cp_after INT,
 best_move_uci TEXT,
 PRIMARY KEY (game_id, ply, engine_version, depth)
 );*/