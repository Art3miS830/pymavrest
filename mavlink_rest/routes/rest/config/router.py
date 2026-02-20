from fastapi import APIRouter, status, Request, BackgroundTasks, Depends
from mavlink_rest.config import (DroneSetting, Requests,
                                 ServiceSetting,
                                 HealthCheck)
from mavlink_rest.config import ConfigManager
from mavlink_rest.routes.rest.base_schema import GeneralResponse
from mavlink_rest.routes.dependencies import Config_Dep, Write_Permission_Dep, Read_Permission_Dep
import asyncio
from mavlink_rest.utils.utils import restart_app, kill_mavsdk_servers


router = APIRouter()

def restart_app_process():
    kill_mavsdk_servers()
    from mavlink_rest.repository import telemetry
    restart_app()


@router.put("/drone", response_model=GeneralResponse,
            dependencies=[Write_Permission_Dep])
async def edit_drone_conf(drone_conf: DroneSetting,
                          config: Config_Dep,
                          bg: BackgroundTasks):
    config.drone = drone_conf
    ConfigManager.update_config(config)
    await ConfigManager.async_overwrite_config_file(config) 
    bg.add_task(restart_app_process)
    return GeneralResponse(code=status.HTTP_200_OK,
                           msg="config edited successfully")


@router.get("/drone", response_model=DroneSetting,
            dependencies=[Read_Permission_Dep])
async def get_drone_conf(config: Config_Dep):
    return config.drone
    

@router.put("/requests", response_model=GeneralResponse, 
            dependencies=[Write_Permission_Dep])
async def edit_requests_setting_conf(requests_conf: Requests,
                                     config: Config_Dep,
                                     bg: BackgroundTasks):
    config.requests = requests_conf
    ConfigManager.update_config(config)
    await ConfigManager.async_overwrite_config_file(config)
    bg.add_task(restart_app_process)
    return GeneralResponse(code=status.HTTP_200_OK,
                           msg="config edited successfully")
    
    
@router.get("/requests", response_model=Requests,
            dependencies=[Read_Permission_Dep])
async def get_requests_setting_conf(config: Config_Dep):
    return config.requests


@router.put("/health_check", response_model=GeneralResponse,
            dependencies=[Write_Permission_Dep])
async def edit_health_check_conf(healthcheck_conf: HealthCheck, 
                             config: Config_Dep,
                             bg: BackgroundTasks):
    config.health_check = healthcheck_conf
    ConfigManager.update_config(config)
    await ConfigManager.async_overwrite_config_file(config)
    bg.add_task(restart_app_process)
    return GeneralResponse(code=status.HTTP_200_OK,
                           msg="config edited successfully")
    

@router.get("/health_check", response_model=HealthCheck,
            dependencies=[Read_Permission_Dep])
async def get_health_check_conf(config: Config_Dep):
    return config.health_check



@router.put("/reload", response_model=GeneralResponse,
            dependencies=[Write_Permission_Dep])
async def reload_config_and_service_setting(bg: BackgroundTasks):
    path = ConfigManager.get_config_path()
    config = await ConfigManager.async_read_config_file(path)
    bg.add_task(restart_app_process)
    return GeneralResponse(code=status.HTTP_200_OK,
                           msg="config reloaded successfully")
    
    

    
    
    
    