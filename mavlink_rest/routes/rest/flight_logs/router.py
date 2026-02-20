from fastapi import APIRouter, HTTPException, status
from mavlink_rest.routes.dependencies import Config_Dep as Config, Read_Permission_Dep
from loguru import logger
from mavlink_rest.repository import telemetry
from pydantic import BaseModel
import datetime as dt
from ..base_schema import GeneralResponse


router = APIRouter()


class LogFile(BaseModel):
    id: int
    date: dt.datetime
    
    
def _ensure_mavsdk_backend():
    if telemetry.telemetry_backend != "mavsdk":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="flight log routes are only available with mavsdk backend",
        )


@router.get("/flight", response_model=list[LogFile], dependencies=[Read_Permission_Dep])
async def flight_logs():
    """
    Get flight details including flight mode, battery, position, speed, RC status, and signal strength.
    """
    from mavlink_rest.repository import telemetry
    try:
        _ensure_mavsdk_backend()
        logs = await telemetry.drone.log_files.get_entries()
        return [LogFile(id=log.id, date=log.date) for log in logs]
    except Exception as e:
        logger.error(f"Exception in GET flight_logs: {e.__class__}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                            detail="Exception in flight_details")
    
    
    
@router.delete("/flight/all", dependencies=[Read_Permission_Dep], response_model=GeneralResponse)
async def delete_flight_logs():
    from mavlink_rest.repository import telemetry
    try:
        _ensure_mavsdk_backend()
        await telemetry.drone.log_files.erase_all_log_files()
        return GeneralResponse(msg="delete all flight logs success",
                            code=status.HTTP_202_ACCEPTED)
    except Exception as e:
        logger.error(f"Exception in DELETE flight_logs: {e.__class__}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                            detail="Exception in flight_details")
