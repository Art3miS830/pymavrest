from pydantic import BaseModel

class GPSData(BaseModel):
    lat: float|None = None
    lon: float|None = None
    alt: float|None = None