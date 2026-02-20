import asyncio

from fastapi import APIRouter, HTTPException, status
from loguru import logger
from mavsdk.action import ActionError

from mavlink_rest.repository.telemetry_task_manager.base import telemetry
from mavlink_rest.routes.dependencies import Config_Dep, Write_Permission_Dep
from mavlink_rest.routes.rest.base_schema import GeneralResponse
from mavlink_rest.routes.rest.commands.schema import (
    ChangeMode,
    DisableRcInterval,
    GoToLocation,
    SetMissionCurrent,
    SetSpeed,
)


router = APIRouter()


def _ensure_connected():
    if not telemetry.is_connected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="drone not connected")


async def force_hold(sleep_time: float = 0.3):
    logger.debug("executing force HOLD")
    try:
        await telemetry.drone.action.return_to_launch()
        await asyncio.sleep(sleep_time)
        await telemetry.drone.action.hold()
    except ActionError as e:
        logger.exception(f"Failed to hold: {e.__class__}: {str(e)}")
        raise
    except Exception as e:
        logger.exception(f"Failed to hold: {e.__class__}: {str(e)}")
        raise


@logger.catch
@router.post("/mode", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def change_mode(config: Config_Dep, inputs: ChangeMode):
    _ensure_connected()
    try:
        match inputs.flight_mode:
            case "HOLD":
                await force_hold(sleep_time=0.2)
            case "LAND":
                await telemetry.drone.action.land()
            case "RTL":
                await telemetry.drone.action.return_to_launch()
            case _:
                logger.error("invalid command type")
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid param")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Exception in command change_flight_mode: {e}, {e.__class__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Exception in change_flight_mode",
        )

    return GeneralResponse(msg="change flight mode command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/goto_location", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def goto_location(inputs: GoToLocation):
    _ensure_connected()
    lat, lon, alt_abs = telemetry.get_default_gps_data(relative_alt=False)
    try:
        await telemetry.drone.action.goto_location(
            inputs.lat or lat,
            inputs.lon or lon,
            inputs.alt_abs_m or alt_abs,
            inputs.yaw_deg or float("nan"),
        )
    except Exception as e:
        logger.error(f"Exception in command goto_location: {e}, {e.__class__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Exception in goto_location",
        )
    return GeneralResponse(msg="goto_location command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/hold", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def hold():
    _ensure_connected()
    try:
        await force_hold()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exception in hold")
    return GeneralResponse(msg="hold command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/rtl", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def rtl():
    _ensure_connected()
    try:
        await telemetry.drone.action.return_to_launch()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exception in rtl")
    return GeneralResponse(msg="rtl command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/land", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def land():
    _ensure_connected()
    try:
        await telemetry.drone.action.land()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exception in land")
    return GeneralResponse(msg="land command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/disarm", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def disarm():
    _ensure_connected()
    try:
        await telemetry.drone.action.disarm()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exception in disarm")
    return GeneralResponse(msg="disarm command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/speed", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def set_speed(inputs: SetSpeed):
    _ensure_connected()
    try:
        await telemetry.drone.action.set_current_speed(inputs.speed_m_s)
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exception in set_speed")
    return GeneralResponse(msg="set speed command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/rc/disable_interval", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def disable_rc_interval(inputs: DisableRcInterval):
    _ensure_connected()
    try:
        await telemetry.disable_RC_for_interval(interval=inputs.interval_sec)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Exception in disable_rc_interval",
        )
    return GeneralResponse(msg="disable RC command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/mission/start", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def mission_start():
    _ensure_connected()
    try:
        if telemetry.telemetry_backend == "mavsdk":
            await telemetry.drone.mission.start_mission()
        else:
            await telemetry.drone.mission_raw.start_mission()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exception in mission_start")
    return GeneralResponse(msg="mission start command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/mission/pause", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def mission_pause():
    _ensure_connected()
    try:
        if telemetry.telemetry_backend == "mavsdk":
            await telemetry.drone.mission.pause_mission()
        else:
            await telemetry.drone.mission_raw.pause_mission()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exception in mission_pause")
    return GeneralResponse(msg="mission pause command sent success", code=status.HTTP_202_ACCEPTED)


@router.post("/mission/current", response_model=GeneralResponse, dependencies=[Write_Permission_Dep])
async def mission_set_current(inputs: SetMissionCurrent):
    _ensure_connected()
    try:
        await telemetry.drone.mission_raw.set_current_mission_item(inputs.seq)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Exception in mission_set_current",
        )
    return GeneralResponse(msg="mission current item command sent success", code=status.HTTP_202_ACCEPTED)
