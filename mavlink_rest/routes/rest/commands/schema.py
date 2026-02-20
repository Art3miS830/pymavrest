from pydantic import BaseModel, Field
from typing import Literal

class ChangeMode(BaseModel):
    flight_mode: Literal["HOLD", "RTL", "LAND"]
    
    

class GoToLocation(BaseModel):
    lat: float|None = None
    lon: float|None = None
    alt_abs_m: float|None = None
    yaw_deg: float|None = None


class SetSpeed(BaseModel):
    speed_m_s: float = Field(gt=0)


class SetMissionCurrent(BaseModel):
    seq: int = Field(ge=0)


class DisableRcInterval(BaseModel):
    interval_sec: float = Field(default=5.0, gt=0)
    
