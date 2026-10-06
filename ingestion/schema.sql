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