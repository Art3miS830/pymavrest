from fastapi import APIRouter, HTTPException, status, WebSocket, WebSocketDisconnect, Depends
from mavlink_rest.utils.network import ping3_host
from mavlink_rest.routes.dependencies import Config_Dep as Config, Read_Permission_Dep
from mavlink_rest.routes.rest.authentication.router import get_current_user, verify_jwt_token, get_users_usernameAsKey
import asyncio
from loguru import logger
from mavlink_rest.repository.telemetry_task_manager.schema import FlightDetails, MissionStatus
from mavlink_rest.repository import telemetry



router = APIRouter()


def _build_flight_details(data, ping_host: str, timeout: int) -> FlightDetails:
    try:
        signal = ping3_host(ping_host, timeout=timeout / 2)
        data.signal = signal
    except Exception as e:
        logger.error(f"Ping check failed: {e.__class__}: {e}")

    payload = data.model_dump()
    mission_payload = payload.pop("mission", None)
    if isinstance(mission_payload, dict):
        payload["mission_summary"] = {
            "mission_id": mission_payload.get("mission_id"),
            "status": mission_payload.get("status"),
            "current_progress": mission_payload.get("current_progress"),
            "total_progress": mission_payload.get("total_progress"),
            "mission_plan_count": len(mission_payload.get("mission_plan") or []),
        }
    else:
        payload["mission_summary"] = None
    return FlightDetails.model_validate(payload)


@router.get("/flight_details", response_model=FlightDetails, dependencies=[Read_Permission_Dep])
async def flight_details(config: Config):
    """
    Get flight details including flight mode, battery, position, speed, RC status, and signal strength.
    """
    timeout = config.requests.timeout
    from mavlink_rest.repository import telemetry
    try:
        data = telemetry.telemetry_data
        return _build_flight_details(data, config.requests.ping_check_by_host, timeout)
    except Exception as e:
        logger.error(f"Exception in flight_details: {e}, {e.__class__}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                            detail="Exception in flight_details")


@router.get("/mission_plan", response_model=MissionStatus, dependencies=[Read_Permission_Dep])
async def mission_plan():
    """
    Get full mission status and mission plan from latest telemetry.
    """
    try:
        return telemetry.telemetry_data.mission
    except Exception as e:
        logger.error(f"Exception in mission_plan: {e}, {e.__class__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Exception in mission_plan",
        )


# websockets 
def ws_user_has_read_permission(token: str)-> bool:
    from mavlink_rest.config import ConfigManager
    config = ConfigManager.get_config()
    token_data = verify_jwt_token(token, config)
    users = get_users_usernameAsKey(config)
    current_user = get_current_user(token_data, users) 
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, 
                            detail=f"user not found")
    if current_user.permission < 10:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, 
                            detail=f"Access denied")
    return True    


@router.websocket("/flight_details", 
                  dependencies=[Depends(ws_user_has_read_permission)])
async def flight_details_websocket(websocket: WebSocket, config: Config, freq: float = 0.3):
    """
    WebSocket endpoint to stream flight details including flight mode, battery, position, speed, RC status, and signal strength.
    """
    # Accept the WebSocket connection
    await websocket.accept()
    timeout = config.requests.timeout
    from mavlink_rest.repository import telemetry
    try:
        while True:
            # Fetch latest telemetry data
            try:
                data = telemetry.telemetry_data
                flight_data = _build_flight_details(data, config.requests.ping_check_by_host, timeout)
                await websocket.send_text(flight_data.model_dump_json())  # Pydantic model to dict
            except Exception as e:
                logger.error(f"Exception in flight_details_websocket: {e}, {e.__class__}")
                await websocket.send_json({"error": f"Failed to fetch telemetry: {str(e)}"})
                await websocket.close(code=1011)  # Internal error
                return
            # Wait before sending the next update (match telemetry rate, e.g., 10 Hz)
            await asyncio.sleep(freq)  # 0.1 seconds = 10 Hz
            
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}, {e.__class__}")
        await websocket.send_json({"error": f"WebSocket error: {str(e)}"})
        await websocket.close(code=1011)  # Internal error
