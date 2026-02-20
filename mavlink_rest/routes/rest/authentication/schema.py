from typing import Literal
from pydantic import BaseModel


class TokenData(BaseModel):
    username: str
    permission: Literal[0, 10, 11]


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"