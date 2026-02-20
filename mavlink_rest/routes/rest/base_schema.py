from pydantic import BaseModel
from typing import Literal


class GeneralResponse(BaseModel):
    status: Literal["OK", "FAILED"] = "OK"
    code: int
    msg: str
    
    
