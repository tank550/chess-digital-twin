from pydantic import BaseModel

class ChessGame(BaseModel):
    id: str
    white_player: str
    black_player: str
    result: str
    moves: list[str]