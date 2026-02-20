import asyncio
import json
import math
from loguru import logger
from mavsdk.telemetry import VtolState
from mavsdk.mavlink_direct import MavlinkDirectError
from typing import Literal

from mavlink_rest.config import ConfigManager
from mavlink_rest.repository.external_devices.gps import AsyncGPSModule
from mavlink_rest.repository.healthcheck import push_flight_info
from mavlink_rest.utils.utils import flight_uid_convertor, restart_app
import time

class MavsdkTelemetryHandlersMixin:
    """
    Mixin containing message handlers and subscription loops for MAVSDK
    to keep the main FlightTelemetry class small for Pyarmor.
    """

    async def _subscribe_raw_message(self, name: str|None = None, interval: int = 0.02, switch_backend_on_generic_type: Literal["mavsdk", "pymavlink"]|None = None):
        try:
            async for message in self.drone.mavlink_direct.message("" if name is None else name):
                match message.message_name:
                    case "HEARTBEAT":
                        try:
                            self._handle_heartbeat_msg( json.loads(message.fields_json) )
                            break
                        except Exception as e:
                            logger.error(f"Error handling heartbeat message: {e}")
                await asyncio.sleep(interval)
            
            # Switch back to pymavlink if generic type
            FC_type = self.telemetry_data.autopilot_type
            if FC_type is not None and FC_type == "GENERIC" and switch_backend_on_generic_type is not None:
                restart_app(backend=switch_backend_on_generic_type)
                
        except MavlinkDirectError as e:
            logger.error(f"Raw message subscription failed: {e}")
            raise e
        except Exception as e:
            logger.exception(f"Raw message subscription failed: {e}")

    def _handle_heartbeat_msg(self, message: dict):
        if (_type:=int(message["type"])) == 6:
            return
        self.telemetry_data.is_drone_connected = True
        mapping = {
            1: "FIXED_WING",
            2: "QUADROTOR",
            13: "HEXAROTOR",
            4: "HELICOPTER",
            43: "GENERIC_MULTIROTOR",
        }
        self.telemetry_data.drone_type = mapping.get(_type, "GENERIC")
        ap = int(message["autopilot"])
        self.telemetry_data.autopilot_type = (
            "PX4" if ap == 12 else ("APM" if ap == 3 else "GENERIC")
        )
        sid = int(message["system_status"])
        sysmap = {
            0: "UNKNOWN",
            1: "BOOT",
            2: "CALIBRATING",
            3: "STANDBY",
            4: "ACTIVE",
            5: "CRITICAL",
            6: "EMERGENCY",
            7: "POWEROFF",
            8: "TERMINATION",
        }
        self.telemetry_data.system_status = sysmap.get(sid, "UNKNOWN")

    def _handle_mission_current_msg(self, message: dict):
        self.telemetry_data.mission.current_progress = int(message["seq"])
        self.telemetry_data.mission.total_progress = int(message.get("total", 0))
        mstate = int(message.get("mission_state", 0))
        mapping = {1: "NO_MISSION", 2: "NOT_STARTED", 3: "ACTIVE", 4: "PAUSED", 5: "COMPLETE"}
        self.telemetry_data.mission.status = mapping.get(mstate, "UNKNOWN")
        self.telemetry_data.mission.mission_id = int(message.get("mission_id", 0))

    @staticmethod
    def _mission_item_to_dict(item) -> dict:
        mission_type = getattr(item, "mission_type", None)
        if hasattr(mission_type, "value"):
            mission_type = mission_type.value

        return {
            "seq": int(getattr(item, "seq", 0)),
            "frame": int(getattr(item, "frame", 0)),
            "command": int(getattr(item, "command", 0)),
            "current": int(getattr(item, "current", 0)),
            "autocontinue": int(getattr(item, "autocontinue", 0)),
            "param1": float(getattr(item, "param1", 0.0)),
            "param2": float(getattr(item, "param2", 0.0)),
            "param3": float(getattr(item, "param3", 0.0)),
            "param4": float(getattr(item, "param4", 0.0)),
            "x": float(getattr(item, "x", 0.0)),
            "y": float(getattr(item, "y", 0.0)),
            "z": float(getattr(item, "z", 0.0)),
            "mission_type": mission_type,
        }

    async def _sync_mission_plan(self):
        """
        Keep `telemetry_data.mission.mission_plan` aligned with FC mission data.
        """
        try:
            mission_items = await self.drone.mission_raw.download_mission()
        except Exception as e:
            logger.debug(f"Mission plan sync skipped: {e.__class__.__name__}: {e}")
            return

        mission_plan = [self._mission_item_to_dict(item) for item in mission_items]
        self.telemetry_data.mission.mission_plan = mission_plan
        self.telemetry_data.mission.total_progress = len(mission_plan)

        if len(mission_plan) == 0:
            self.telemetry_data.mission.status = "NO_MISSION"
            self.telemetry_data.mission.current_progress = 0
            self.telemetry_data.mission.mission_id = None
        elif self.telemetry_data.mission.status in (None, "UNKNOWN", "NO_MISSION"):
            self.telemetry_data.mission.status = "NOT_STARTED"

    def _handle_status_msg(self, message: dict):
        match message["severity"]:
            case 0: logger.critical(f"Drone Emergency: {message['text']}")
            case 1: logger.warning(f"Drone Alert: {message['text']}")
            case 2: logger.critical(f"Drone Critical: {message['text']}")
            case 3: logger.error(f"Drone Error: {message['text']}")
            case 4: logger.warning(f"Drone Warning: {message['text']}")
            case 5: logger.info(f"Drone Notice: {message['text']}")
            case 6: logger.info(f"Drone Info: {message['text']}")
            case 7: logger.debug(f"Drone Debug: {message['text']}")

    async def _subscribe_flight_mode(self):
        try:
            async for flight_mode in self.drone.telemetry.flight_mode():
                self.telemetry_data.flight_mode = flight_mode.name
                if self.verbose:
                    logger.debug(f"Updated flight mode: {flight_mode.name}")
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Flight mode subscription failed: {e}")
            self.telemetry_data.flight_mode = None

    async def _subscribe_FW_loiter_radius(self, interval: int = 5):
        while True:
            if self.telemetry_data.vtol_state in ["FW", "TO_FW"]:
                try:
                    match self.telemetry_data.autopilot_type:
                        case "PX4":
                            self.telemetry_data.FW_loiter_radius = await self.drone.param.get_param_float("NAV_LOITER_RAD")
                        case "APM":
                            self.telemetry_data.FW_loiter_radius = await self.drone.param.get_param_int("WP_LOITER_RAD")
                except Exception as e:
                    logger.error(f"Error getting FW loiter radius param: {e}")
            await asyncio.sleep(interval)

    async def _subscribe_home(self):
        try:
            async for home in self.drone.telemetry.home():
                self.telemetry_data.home_lat = home.latitude_deg
                self.telemetry_data.home_lon = home.longitude_deg
                self.telemetry_data.home_alt = home.relative_altitude_m
                if self.verbose:
                    logger.debug(f"Updated home position: lat={home.latitude_deg}, lon={home.longitude_deg}, alt={home.relative_altitude_m}")
                await asyncio.sleep(self.global_delay+1)
        except Exception as e:
            logger.error(f"Home position subscription failed: {e}")
            self.telemetry_data.home_lat = None
            self.telemetry_data.home_lon = None
            self.telemetry_data.home_alt = None

    async def _subscribe_vtol_state(self):
        try:
            async for state in self.drone.telemetry.vtol_state():
                match state:
                    case VtolState.MC: self.telemetry_data.vtol_state = "MC"
                    case VtolState.FW: self.telemetry_data.vtol_state = "FW"
                    case VtolState.TRANSITION_TO_MC: self.telemetry_data.vtol_state = "TO_MC"
                    case VtolState.TRANSITION_TO_FW: self.telemetry_data.vtol_state = "TO_FW"
                    case _:
                        logger.warning(f"Undefined vtol state={state}, Defaulting to MC")
                        self.telemetry_data.vtol_state = "MC"
                await asyncio.sleep(self.global_delay+1)
        except Exception as e:
            logger.error(f"vtol state subscription failed: {e}")
            self.telemetry_data.vtol_state = None

    async def _subscribe_mission_changed(self):
        await self._sync_mission_plan()
        try:
            async for changed in self.drone.mission_raw.mission_changed():
                logger.debug(f"Mission changed: status:{changed}")
                if changed:
                    try:
                        await self._sync_mission_plan()
                    except Exception as e:
                        logger.exception(f"Error modifying mission: {e}")                        
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Mission subscription failed: {e}")


    async def _subscribe_flight_info(self, interval: int = 10):
        try:
            hardware_data = await self.drone.info.get_identification()
            hardware_uid = flight_uid_convertor(hardware_data.hardware_uid)
        except Exception as e:
            logger.exception(f"Error getting hardware UID: {e}")
            hardware_uid = None
            
        self.telemetry_data.flight_info.hardware_uid = hardware_uid
        try:
            async for info in self.drone.info.flight_information():
                if info:
                    self.telemetry_data.flight_info.flight_uid = info.flight_uid
                    self.telemetry_data.flight_info.duration_since_takeoff_ms = info.duration_since_takeoff_ms
                    self.telemetry_data.flight_info.duration_since_arming_ms = info.duration_since_arming_ms
                    self.telemetry_data.flight_info.time_boot_ms = info.time_boot_ms
                    self.telemetry_data.flight_info.check_interval = interval
                    if self.telemetry_data.flight_info.hardware_uid is None:
                        try:
                            hardware_data = await self.drone.info.get_identification()
                            hardware_uid = flight_uid_convertor(hardware_data.hardware_uid)
                        except Exception as e:
                            logger.exception(f"Error getting hardware UID: {e}")
                        self.telemetry_data.flight_info.hardware_uid = hardware_uid
                await asyncio.sleep(interval)  
        except Exception as e:
            logger.error(f"Flight info subscription failed: {e}")

    async def _subscribe_health(self):
        try:
            async for health in self.drone.telemetry.health():
                self.telemetry_data.is_armable = health.is_armable
                self.telemetry_data.is_global_position_ok = health.is_global_position_ok
                self.telemetry_data.is_local_position_ok = health.is_local_position_ok
                if self.verbose:
                    logger.debug(f"Updated health: [is_armable, global_pos, local_pos]={[health.is_armable, health.is_global_position_ok, health.is_local_position_ok]}")
                await asyncio.sleep(self.global_delay+1)
        except Exception as e:
            logger.error(f"Health subscription failed: {e}")
            self.telemetry_data.is_armable = self.telemetry_data.is_global_position_ok = False
            self.telemetry_data.is_local_position_ok = False

    async def _manage_disconnection(self, reset_telemetry_after_n_seconds: int | None = None,
                                        reset_app_after_n_seconds: int | None = None):
            if reset_app_after_n_seconds is None and reset_telemetry_after_n_seconds is not None:
                reset_app_after_n_seconds = reset_telemetry_after_n_seconds * 3

            prev_flight_lat = round(self.telemetry_data.Flight_GPS_lat, 6) if isinstance(self.telemetry_data.Flight_GPS_lat, float) else None
            prev_flight_lon = round(self.telemetry_data.Flight_GPS_lon, 6) if isinstance(self.telemetry_data.Flight_GPS_lon, float) else None
            prev_device_lat = round(self.telemetry_data.Device_GPS_lat, 6) if isinstance(self.telemetry_data.Device_GPS_lat, float) else None
            prev_device_lon = round(self.telemetry_data.Device_GPS_lon, 6) if isinstance(self.telemetry_data.Device_GPS_lon, float) else None

            init_time = None

            try:
                while True:
                    await asyncio.sleep(4)
                    curr_flight_lat = round(self.telemetry_data.Flight_GPS_lat, 6) if isinstance(self.telemetry_data.Flight_GPS_lat, float) else None
                    curr_flight_lon = round(self.telemetry_data.Flight_GPS_lon, 6) if isinstance(self.telemetry_data.Flight_GPS_lon, float) else None
                    curr_device_lat = round(self.telemetry_data.Device_GPS_lat, 6) if isinstance(self.telemetry_data.Device_GPS_lat, float) else None
                    curr_device_lon = round(self.telemetry_data.Device_GPS_lon, 6) if isinstance(self.telemetry_data.Device_GPS_lon, float) else None

                    internal_valid = None not in (curr_flight_lat, curr_flight_lon)
                    external_valid = None not in (curr_device_lat, curr_device_lon)
                    
                    internal_changed = False
                    if internal_valid:
                        if curr_flight_lat != prev_flight_lat or curr_flight_lon != prev_flight_lon:
                            internal_changed = True

                    external_changed = False
                    if external_valid:
                        if curr_device_lat != prev_device_lat or curr_device_lon != prev_device_lon:
                            external_changed = True

                    if internal_changed or (self.telemetry_data.is_drone_connected and internal_valid):
                        self.telemetry_data.default_gps = "internal"
                    else:
                        self.telemetry_data.default_gps = "external"

                    prev_flight_lat = curr_flight_lat
                    prev_flight_lon = curr_flight_lon
                    prev_device_lat = curr_device_lat
                    prev_device_lon = curr_device_lon

                    is_connected = self.telemetry_data.is_drone_connected
                    if is_connected and internal_changed and external_changed:
                        init_time = None
                    if not is_connected and not internal_changed and not external_changed:
                        if init_time is None:
                            init_time = time.time()
                    if init_time is not None:
                        elapsed_time = time.time() - init_time
                        if reset_app_after_n_seconds and elapsed_time > reset_app_after_n_seconds:
                            logger.critical(f"Drone disconnected for {elapsed_time:.0f}s. Restarting app...")
                            restart_app()
                        elif reset_telemetry_after_n_seconds and elapsed_time > reset_telemetry_after_n_seconds:
                            if self.telemetry_data.flight_mode is not None:
                                logger.warning(f"Drone disconnected for {elapsed_time:.0f}s. Resetting telemetry data...")
                                self.reset_telemetry_data()
            except Exception as e:
                logger.error(f"Error in _manage_disconnection: {e}")

    async def _subscribe_is_connected(self):
        try:
            async for connection in self.drone.core.connection_state():
                self.telemetry_data.is_drone_connected = connection.is_connected                    
                await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"connection to drone failed: {e}")
            self.telemetry_data.is_drone_connected = False

    async def _subscribe_position(self):
        try:
            async for position in self.drone.telemetry.position():
                self.telemetry_data.Flight_GPS_lat = position.latitude_deg
                self.telemetry_data.Flight_GPS_lon = position.longitude_deg
                self.telemetry_data.Flight_GPS_alt = position.relative_altitude_m
                self.telemetry_data.Flight_GPS_alt_abs =  position.absolute_altitude_m
                if self.verbose:
                    logger.debug(f"Updated position: {position}")
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Position subscription failed: {e}")
            self.telemetry_data.Flight_GPS_lat = None
            self.telemetry_data.Flight_GPS_lon = None
            self.telemetry_data.Flight_GPS_alt = None

    async def _subscribe_battery(self):
        try:
            async for battery in self.drone.telemetry.battery():
                self.telemetry_data.battery_remain = int(pct) if (pct:=battery.remaining_percent) else None
                self.telemetry_data.battery_amper = battery.current_battery_a
                if self.verbose:
                    logger.debug(f"Updated battery: remain pct: {battery.remaining_percent}, battery amper: {battery.current_battery_a}")
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Battery subscription failed: {e}")
            self.telemetry_data.battery_remain = None
            self.telemetry_data.battery_amper = None

    async def _subscribe_velocity(self):
        try:
            async for vel in self.drone.telemetry.velocity_ned():
                if vel is not None:
                    self.telemetry_data.vx = vel.north_m_s
                    self.telemetry_data.vy = vel.east_m_s
                    self.telemetry_data.vz = vel.down_m_s
                    self.telemetry_data.speed = math.sqrt(vel.north_m_s**2 + vel.east_m_s**2)
                    self.telemetry_data.air_speed = self.telemetry_data.speed 
                    if self.verbose:
                        logger.debug(f"Ground Speed: {self.telemetry_data.speed:.2f} m/s")
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Speed subscription failed")
            self.telemetry_data.air_speed = self.telemetry_data.speed = None 
            self.telemetry_data.vx = self.telemetry_data.vy = self.telemetry_data.vz = None

    async def _subscribe_RC(self):
        try:
            async for rc in self.drone.telemetry.rc_status():
                if self.verbose:
                    logger.debug(f"RC Status: Available={rc.is_available}, Signal Strength={rc.signal_strength_percent}")
                if rc.is_available and rc.signal_strength_percent is not None and rc.signal_strength_percent > 0:
                    self.telemetry_data.rc_connected = True
                else: 
                    self.telemetry_data.rc_connected = False
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Error checking RC status: {e}")
            self.telemetry_data.rc_connected = None

    async def _subscribe_isFlying(self):
        try:
            async for is_flying in self.drone.telemetry.in_air():
                if self.verbose:
                    logger.debug(f"Drone Flying status: {is_flying}")
                self.telemetry_data.is_flying = is_flying
                await asyncio.sleep(self.global_delay + 0.5)
        except Exception as e:
            logger.error(f"Error checking flying status: {e}")
            self.telemetry_data.is_flying = None

    async def _subscribe_external_gps(self):
        config = ConfigManager.get_config()
        gps_conf = config.external_devices.get("gps")
        if not gps_conf or not gps_conf.enabled:
            logger.debug("External GPS not enabled in config")
            return
        try:
            async with AsyncGPSModule(port=gps_conf.COM, baudrate=gps_conf.baud) as gps:
                async for pos in gps.stream_gps_data(interval=0.3):
                    if pos is not None:
                        self.telemetry_data.Device_GPS_lat = pos.lat
                        self.telemetry_data.Device_GPS_lon = pos.lon
                        self.telemetry_data.Device_GPS_alt = pos.alt_rel
                        self.telemetry_data.Device_GPS_alt_abs = pos.alt_abs
                    await asyncio.sleep(self.global_delay)                
        except Exception as e:
            logger.error(f"Error while getting external gps data: {e}")
            self.telemetry_data.Device_GPS_lat = None
            self.telemetry_data.Device_GPS_lon = None
            self.telemetry_data.Device_GPS_alt = None
            self.telemetry_data.Device_GPS_alt_abs = None

    async def _subscribe_isArmed(self):
        prev_state = None
        try:
            async for is_armed in self.drone.telemetry.armed():
                if self.verbose:
                    logger.debug(f"Drone Arm status:{is_armed}")
                self.telemetry_data.is_armed = is_armed
                if is_armed and is_armed != prev_state:
                    try:
                        await push_flight_info()
                    except Exception as e:
                        logger.error(f"Error while pushing flight info: {e.__class__}: {e}")
                await asyncio.sleep(self.global_delay + 0.5)
                prev_state = is_armed
        except Exception as e:
            logger.error(f"Error checking Arm status: {e}")
            self.telemetry_data.is_armed = None

    async def _subscribe_euler_angles(self):
        try:
            async for euler in self.drone.telemetry.attitude_euler():
                self.telemetry_data.roll_deg = euler.roll_deg
                self.telemetry_data.pitch_deg = euler.pitch_deg
                self.telemetry_data.yaw_deg = euler.yaw_deg
                if self.verbose: 
                    logger.debug(f"Current attitude(deg): roll={euler.roll_deg:.2f}, pitch={euler.pitch_deg}, yaw={euler.yaw_deg}")
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Error subscribing attitude angle: {e.__class__}: {str(e)}")
            self.telemetry_data.roll_deg = None
            self.telemetry_data.pitch_deg = None
            self.telemetry_data.yaw_deg = None

    async def _subscribe_mission(self):
        last_plan_sync_at = 0.0
        try:
            async for mission in self.drone.mission.mission_progress():
                self.telemetry_data.mission.current_progress = mission.current
                self.telemetry_data.mission.total_progress = mission.total

                if mission.total == 0 and self.telemetry_data.mission.mission_plan:
                    self.telemetry_data.mission.mission_plan = []
                    self.telemetry_data.mission.status = "NO_MISSION"
                elif (
                    mission.total > 0
                    and not self.telemetry_data.mission.mission_plan
                    and (time.monotonic() - last_plan_sync_at) > 5
                ):
                    await self._sync_mission_plan()
                    last_plan_sync_at = time.monotonic()

                if self.verbose:
                    logger.debug(f"Mission Progress: Current={mission.current}, Total={mission.total}")
                await asyncio.sleep(self.global_delay)
        except Exception as e:
            logger.error(f"Mission subscription failed: {e}")
            self.telemetry_data.mission.current_progress = None
            self.telemetry_data.mission.total_progress = None
