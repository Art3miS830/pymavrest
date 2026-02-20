from pydantic import BaseModel
from mavlink_rest.config import ConfigManager
from typing import Any
from mavlink_rest.utils._request import AsyncRequest, Response, StatusCodeError, Request
import asyncio
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_random, stop_after_delay
from httpx import TimeoutException
from mavlink_rest.utils.utils import run_with_timeout
from serial.serialutil import SerialException, SerialTimeoutException
import os
import datetime as dt
import pathlib, aiofiles



def get_client():
    config = ConfigManager.get_config()
    req_conf = config.requests
    client = AsyncRequest(2, 2, config.server.base_url)
    return client


async def _update_msg(latitude: float,
                longitude: float, 
                altitude: float,
                speed: float,
                flight_mode: str,
                battery_level: int,
                save_cat129_logs: bool = True)-> dict[str, Any]:
    config = ConfigManager.get_config()
    serial_number = config.drone.properties.serial_number   
    data = {
            "action": "update",
            "serial_number": serial_number,
            "latitude": latitude,
            "longitude": longitude,
            "altitude": altitude,
            "speed": speed,
            "flight_mode": flight_mode,
            "battery_level": battery_level,
        }
    logger.info(f"healthcheck data: {data}")
    return data
    

async def _delete_msg()-> dict[str, Any]:
    config = ConfigManager.get_config()
    serial_number = config.drone.properties.serial_number
    return {"action": "delete", "serial_number": serial_number}


async def _authenticate_for_token(client: AsyncRequest, username: str,
                                  password:str, cache_token_in_telemetry: bool = True)-> str:
    config = ConfigManager.get_config()
    logger.debug("getting new access token")
    res = await client._apost(config.server.auth.route,
                  json={"username": username, "password":password} )
    token = res.json()["token"]
    if cache_token_in_telemetry:
        from mavlink_rest.repository import telemetry
        telemetry.server_token = token
    return token
    

async def generate_healthcheck_msg()-> dict[str, Any]|None:
    from mavlink_rest.repository import telemetry
    while True not in [telemetry.telemetry_data.is_drone_connected, telemetry.is_connected]:
        logger.info("waiting for drone connection to start healthcheck")
        await asyncio.sleep(1)
        continue
    
    if telemetry.telemetry_data.is_armed:
        lat, lon, alt = telemetry.get_default_gps_data()
        if None in [lat, lon, alt]: return 
        msg = await _update_msg(lat, lon, 
                            alt, telemetry.telemetry_data.speed,
                            telemetry.telemetry_data.flight_mode,
                            telemetry.telemetry_data.battery_remain)
    else: 
        msg = await _delete_msg()
    return msg


def convert_healthcheck_to_sms(data: dict[str, Any])-> str:
    match data["action"]:
        case "delete":
            msg_array = ["delete", data["serial_number"]]
        case "update":
            msg_array = ["update", data["serial_number"], 
                    str(data["latitude"]), str(data["longitude"]),
                    str(data["altitude"]), str(data["speed"]),
                    str(data["flight_mode"]), str(data["battery_level"]),
                    str(data["cat129"]) if data.get("cat129") else ""]
        case _:
            logger.error("unknown action when converting data to SMS msg")
    return ','.join(msg_array)


# @retry(
#     stop=(stop_after_attempt(5) | stop_after_delay(100)),
#     wait=wait_random(min=1, max=3)
# )
# @logger.catch
async def push_health_status_http_loop():
    config = ConfigManager.get_config()
    client = get_client()
    from mavlink_rest.repository import telemetry
    route = config.health_check.route
    interval = config.health_check.update_interval_sec
    if not config.health_check.enabled: return
    auth = config.server.auth
    token = None
    try:
        if config.health_check.push_to_server: 
            token = telemetry.server_token or await _authenticate_for_token(client, 
                                                                            auth.username,
                                                                            auth.password )
    except TimeoutException as e:
        logger.error(f"timeout occurred while getting access token: {e.__class__}: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"exception thrown while getting new access token from server: {str(e)}")
        raise e
    is_prev_msg_delete = False # holds prev msg to ignore sending delete 
    while True:
        msg = await generate_healthcheck_msg()
        if not config.health_check.push_to_server:
            await asyncio.sleep(interval)
            continue
        if msg is None:
            logger.debug("no data fetched yet, waiting for telemetry data...")
            await asyncio.sleep(interval)
            continue
        try:
            # ignore msg if prev was delete and current is delete
            if msg["action"] == "delete" and is_prev_msg_delete:
                logger.debug("ignoring delete msg as prev msg was delete")
                await asyncio.sleep(interval)
                continue
            if config.health_check.push_to_server:
                await client._apost(route, json=msg, headers={"Authorization": f"Bearer {token}"})
                logger.success(f"health_status: action={msg["action"]} sent to upstream successfully")
                is_prev_msg_delete = True if msg["action"] == "delete" else False
        except StatusCodeError:
            try:
                token = await _authenticate_for_token(client, auth.username, auth.password)
            except TimeoutException as e:
                logger.error(f"timeout occurred while getting access token: {e.__class__}: {str(e)}")
                raise e
            except Exception as e:
                logger.error(f"exception thrown while getting new access token from server: {str(e)}")
                raise e
            continue
        except TimeoutException as e:
            logger.error(f"timeout occurred while pushing health status to server: {e.__class__}: {str(e)}")
            raise e
        except Exception as e:
            logger.error(f"exception thrown while pushing health to server: {str(e)}")
            raise e
        await asyncio.sleep(interval)
        
        
        
async def push_health_status_sms():
    config = ConfigManager.get_config()
    if config.health_check.send_sms_in_fail:
        from mavlink_rest.repository.sms import send_multipart_sms
        logger.warning("retrying to send healthcheck data via sms...")
        sms_device = config.external_devices.get("sms")
        if sms_device.type == "serial":
            try:
                pass # toDo: add sms here
                logger.success("healthcheck data sent via SMS successfully")
            except SerialException as e:
                logger.error(f"serial exception while sending SMS msg: {e.__class__}: {str(e)}")
            except SerialTimeoutException as e:
                logger.error(f"serial timeout exception while sending SMS msg: {e.__class__}: {str(e)}")
            except Exception as e:
                logger.error(f"error thrown while sending SMS msg: {e.__class__}: {str(e)}")
        else:
            logger.error("device protocol not defined")
            raise ValueError("device protocol not defined")



async def push_health_status():
    while True:
        config = ConfigManager.get_config()
        if not config.health_check.enabled:
            await asyncio.sleep(config.health_check.update_interval_sec)
            continue
        try:
            await push_health_status_http_loop()
        except Exception as e:
            logger.error(f"sending healthcheck status to server:{config.server.base_url} via HTTP request failed: {e.__class__}: {str(e)}")
            await push_health_status_sms()
            await asyncio.sleep(config.health_check.update_interval_sec)
            
            

async def _push_flight_info(token: str):
    from mavlink_rest.repository import telemetry
    from mavlink_rest.utils.utils import get_machine_id, get_sha256
    client = get_client()
    config = ConfigManager.get_config()
    while None in (flight_id:=telemetry.telemetry_data.flight_info.flight_uid, flight_hardware_uid:=telemetry.telemetry_data.flight_info.hardware_uid):
        await asyncio.sleep(2)
        logger.debug("waiting for flight info to be available...")
    orbita_machine_id = await get_machine_id()
    serial_number = config.drone.properties.serial_number
    payload_hash = get_sha256(f"{flight_hardware_uid}_{orbita_machine_id}_{serial_number}")
    payload = {
        "serial_number" : serial_number,
        "flight_ID" : flight_id,
        "orbita_hardware_ID" : orbita_machine_id,
        "flight_hardware_ID" : flight_hardware_uid,
        "hash256": payload_hash}
    try:
        await client._apost(config.health_check.flight_info_route, json=payload,  
                            headers={"Authorization": f"Bearer {token}"})
        logger.debug(f"flight info sent to upstream: {payload}")
        logger.success(f"flight info sent to upstream successfully")
    except Exception as e:
        logger.error(f"sending flight info to server:{config.server.base_url} via HTTP request failed: {e.__class__}: {str(e)}")
        raise e
    
    
async def push_flight_info():
    config = ConfigManager.get_config()
    from mavlink_rest.repository import telemetry
    client = get_client()
    token = telemetry.server_token or await _authenticate_for_token(client, config.server.auth.username,
                                                                    config.server.auth.password) 
    try:
        await _push_flight_info(token)
    except StatusCodeError:
        try: 
            token = await _authenticate_for_token(client, config.server.auth.username,
                                                  config.server.auth.password) 
            await _push_flight_info(token)
        except TimeoutException as e:
            logger.error(f"timeout occurred while getting access token: {e.__class__}: {str(e)}")
            raise e
        except Exception as e:
            logger.error(f"exception thrown while getting new access token from server: {str(e)}")
            raise e
