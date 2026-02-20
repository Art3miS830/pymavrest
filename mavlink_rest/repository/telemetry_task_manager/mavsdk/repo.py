from mavsdk import System
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from mavlink_rest.config import ConfigManager
from ..schema import FlightDetails, Telemetry
import asyncio, math
from loguru import logger
from mavlink_rest.utils.utils import run_with_timeout
from mavlink_rest.repository.external_devices.gps import AsyncGPSModule
from enum import Enum
from grpc.aio import AioRpcError
import sys, psutil
from mavlink_rest.utils.utils import get_mavsdk_server_pids, kill_mavsdk_servers, restart_app
from mavsdk.telemetry import VtolState
import time, json
from mavsdk.mavlink_direct import MavlinkMessage, MavlinkDirectError, MavlinkDirectResult
from typing import Literal
from mavlink_rest.repository.healthcheck import push_flight_info
from .handlers import MavsdkTelemetryHandlersMixin  

EXIT_AFTER_N_RETRY = 3

class MsgNames(Enum):
    FLIGHT_MODE = "FlightModeTask"
    POSITION = "PositionTask"
    BATTERY = "BatteryTask"
    VELOCITY = "VelocityTask"
    RC_STATUS = "RcTask"
    IS_FLYING = "IsFlyingTask"
    IS_ARMED = "IsArmedTask"
    HEALTH = "HealthTask"
    IS_CONNECTED = "IsConnectedTask"
    EULER_ANGLES = "EulerAnglesTask"
    HOME = "HomeTask"
    EXTERNAL_GPS = "ExternalGPSTask"
    MISSION = "MissionTask"
    VTOL_STATE = "VtolStateTask"
    FW_LOITER_RADIUS = "FWLoiterRadiusTask"
    RAW_MESSAGE = "RawMessageTask"
    FLIGHT_INFO = "FlightInfoTask"
    

class FlightTelemetry(MavsdkTelemetryHandlersMixin): # <--- Inherit from Mixin
    
    telemetry_instanced = False
    
    def __init__(self, verbose: bool = True, global_delay: float = 0.01):
        self.drone = System()
        config = ConfigManager.get_config(raise_ifNone=False)
        if config is not None:
            external_gps = config.external_devices.get("gps")
            default_gps = "external" if external_gps and external_gps.enabled else "internal"
            self.telemetry_data = Telemetry.model_construct(default_gps=default_gps)
        else:
            logger.warning("couldn't initialize Telemetry with config data, use self.init() when config file is read successfully")
            self.telemetry_data = Telemetry.model_construct(default_gps="internal")
        self._lock = asyncio.Lock()  # For thread-safe cache updates
        self.tasks: list[asyncio.Task] = []
        self.verbose = verbose
        self.is_connected: bool = False
        self.global_delay = global_delay  # Delay for telemetry updates
        self.telemetry_instanced = True
        
        
    def init(self):
        """Inject configuration into the telemetry manager."""
        config = ConfigManager.get_config()
        external_gps = config.external_devices.get("gps")
        default_gps = "external" if external_gps and external_gps.enabled else "internal"
        self.telemetry_data.default_gps = default_gps


    @retry(
        stop=stop_after_attempt(4),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ConnectionError),
        before_sleep=lambda retry_state: logger.info(f"Retrying connection: attempt {retry_state.attempt_number}")
    )
    async def connect(self, system_address: str):
        """Connect to the drone with retries."""
        config = ConfigManager.get_config()
        self.system_address = system_address
        logger.info(f"Connecting to drone at {self.system_address}")
        try:
            await asyncio.wait_for(self.drone.connect(system_address=self.system_address),
                                timeout=config.requests.timeout)
        except asyncio.TimeoutError:
            logger.error(f"connection failed to drone at {self.system_address}")
        async for state in self.drone.core.connection_state():
            if state.is_connected:
                logger.success("Connected to drone!")
                self.is_connected = True
                return
        raise ConnectionError("Failed to connect to drone")


    @retry(
        stop=stop_after_attempt(4),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.info(f"Retrying rate setting: attempt {retry_state.attempt_number}")
    )
    async def set_telemetry_rates(self):
        """Set telemetry update rates."""
        freq = 10
        tasks = [
            self.drone.telemetry.set_rate_rc_status(freq//2),
            self.drone.telemetry.set_rate_position(freq),     # 10 Hz for position
            self.drone.telemetry.set_rate_battery(freq//2),       # 5 Hz for battery
            self.drone.telemetry.set_rate_velocity_ned(freq),
            self.drone.telemetry.set_rate_vtol_state(freq//2),
        ]
        asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Telemetry rates set")

    async def subscribe_telemetry(self, subscribe_flightMode: bool = True, subscribe_position: bool = True,
                                  subscribe_battery: bool = True, subscribe_velocity: bool = True,
                                  subscribe_RC: bool = True, subscribe_isFlying: bool = True,
                                  subscribe_isArmed: bool = True, subscribe_health: bool = True,
                                  subscribe_isConnected: bool = True, subscribe_euler_angles: bool = True,
                                  subscribe_home: bool = True, subscribe_mission: bool = True,
                                  subscribe_vtol_state: bool = True, subscribe_FW_loiter_radius: bool = True,
                                  subscribe_raw_message: bool = True, subscribe_flight_info: bool = True):
        """Subscribe to telemetry updates and cache them."""
        # Ensure telemetry rates are set
        config = ConfigManager.get_config()
        tasks = []
        running_tasks = self.tasks_by_name
        
        # tasks that does not depend on flight connection
        ## Check for external GPS subscription
        if gps_data:=config.external_devices.get("gps"):
            if gps_data.enabled and MsgNames.EXTERNAL_GPS.value not in running_tasks: 
                tasks.append(asyncio.create_task(self._subscribe_external_gps(), name=MsgNames.EXTERNAL_GPS.value))
        ## adding disconnect manager task
        tasks.append(asyncio.create_task(self._manage_disconnection(reset_app_after_n_seconds=60, reset_telemetry_after_n_seconds=5), name="Disconnect_manager"))
        
        
        while not self.is_connected:
            logger.error("waiting for drone connection to start telemetry")
            await asyncio.sleep(1)
            continue
            
        # flight dependent tasks
        try:
            await self.set_telemetry_rates()
        except Exception as e:
            logger.error(f"Failed to set telemetry rates: {e}")
        # Create background tasks for subscriptions
        if subscribe_flightMode and MsgNames.FLIGHT_MODE.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_flight_mode(), name=MsgNames.FLIGHT_MODE.value))
        if subscribe_position and MsgNames.POSITION.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_position(), name=MsgNames.POSITION.value))
        if subscribe_battery and MsgNames.BATTERY.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_battery(), name=MsgNames.BATTERY.value))
        if subscribe_velocity and MsgNames.VELOCITY.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_velocity(), name=MsgNames.VELOCITY.value))
        if subscribe_RC and MsgNames.RC_STATUS.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_RC(), name=MsgNames.RC_STATUS.value))
        if subscribe_isFlying and MsgNames.IS_FLYING.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_isFlying(), name=MsgNames.IS_FLYING.value))
        if subscribe_isArmed and MsgNames.IS_ARMED.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_isArmed(), name=MsgNames.IS_ARMED.value))
        if subscribe_health and MsgNames.HEALTH.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_health(), name=MsgNames.HEALTH.value))
        if subscribe_isConnected and MsgNames.IS_CONNECTED.value not in running_tasks:   
            global EXIT_AFTER_N_RETRY
            tasks.append(asyncio.create_task(self._subscribe_is_connected(),
                                             name=MsgNames.IS_CONNECTED.value))
        if subscribe_euler_angles and MsgNames.EULER_ANGLES.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_euler_angles(), name=MsgNames.EULER_ANGLES.value))
        if subscribe_home and MsgNames.HOME.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_home(), name=MsgNames.HOME.value))
        if subscribe_mission and MsgNames.MISSION.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_mission(), name=MsgNames.MISSION.value))
        if subscribe_vtol_state and MsgNames.VTOL_STATE.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_vtol_state(), name=MsgNames.VTOL_STATE.value))
        if subscribe_FW_loiter_radius and MsgNames.FW_LOITER_RADIUS.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_FW_loiter_radius(), name=MsgNames.FW_LOITER_RADIUS.value))
        if subscribe_raw_message and MsgNames.RAW_MESSAGE.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_raw_message(interval=0.02, switch_backend_on_generic_type="pymavlink"), name=MsgNames.RAW_MESSAGE.value))
        if subscribe_flight_info and MsgNames.FLIGHT_INFO.value not in running_tasks:
            tasks.append(asyncio.create_task(self._subscribe_flight_info(interval=10), name=MsgNames.FLIGHT_INFO.value))


        tasks.append(asyncio.create_task(self._subscribe_mission_changed(), name="MISSION_CHANGED_Task"))

        logger.info("Started telemetry subscriptions")
        self.tasks = tasks
        mavsdk_pid = get_mavsdk_server_pids()
        logger.info(f"\n*** mavsdk server running with pid={mavsdk_pid}\n")
        try:
            await asyncio.gather(*self.tasks, return_exceptions=False)
        except AioRpcError as e:
            logger.critical(f"mavsdk_server closed or faced errors, restarting app")
            kill_mavsdk_servers()
            await asyncio.sleep(1)
            restart_app()
        except SystemExit as e:
            logger.critical(f"exiting app with error_code:{e.code}")
            await asyncio.sleep(1)
            raise e
        return self.tasks
        
        
    async def unsubscribe_telemetry(self):
        for name, task in self.tasks_by_name.items():
            try:
                state = task.cancel()
                if state:
                    logger.debug(f"task:{name} canceled")
                    self.tasks.remove(task)
                else: 
                    logger.warning(f"task:{name} has done or already cancelled")
                    return
            except Exception as e:
                logger.error(f"cancelling task={name} failed")
        self.reset_telemetry_data()

    
    @property
    def tasks_by_name(self)-> dict[str, asyncio.Task]:
        return {task.get_name():task for task in self.tasks}
                
    
    def unsubscribe_msg(self, name: MsgNames):
        tasks_by_name = self.tasks_by_name
        if name.value not in tasks_by_name:
            logger.warning(f"task:{name.value} not found")
            return
        task: asyncio.Task = tasks_by_name[name.value]
        state = task.cancel()
        if state:
            logger.debug(f"task:{name.value} canceled")
            self.tasks.remove(task)
            self.reset_task_data(name)
        else: 
            logger.warning(f"task:{name.value} has done or already cancelled")
            
    
    def update_tasks(self):
        """Update the tasks list to remove completed or cancelled tasks."""
        self.tasks = [task for task in self.tasks if not task.done() and not task.cancelled()]
        logger.debug(f"Updated tasks list, remaining tasks: {[task.get_name() for task in self.tasks]}")
        
    
    def reset_task_data(self, name: MsgNames):
        """Reset the telemetry data for a specific task."""
        match name.value:
            case MsgNames.FLIGHT_MODE.value:
                self.telemetry_data.flight_mode = None
            case MsgNames.POSITION.value:
                self.telemetry_data.Flight_GPS_lat = None
                self.telemetry_data.Flight_GPS_lon = None
                self.telemetry_data.Flight_GPS_alt = None
                self.telemetry_data.Flight_GPS_alt_abs = None
            case MsgNames.BATTERY.value:
                self.telemetry_data.battery_remain = None
                self.telemetry_data.battery_amper = None
            case MsgNames.VELOCITY.value:
                self.telemetry_data.vx = None
                self.telemetry_data.vy = None
                self.telemetry_data.vz = None
                self.telemetry_data.speed = None
                self.telemetry_data.air_speed = None
            case MsgNames.RC_STATUS.value:
                self.telemetry_data.rc_connected = None
            case MsgNames.IS_FLYING.value:
                self.telemetry_data.is_flying = None
            case MsgNames.IS_ARMED.value:
                self.telemetry_data.is_armed = None
            case MsgNames.HEALTH.value:
                self.telemetry_data.is_armable = None
                self.telemetry_data.is_global_position_ok = None
                self.telemetry_data.is_local_position_ok = None
            case MsgNames.IS_CONNECTED.value:
                self.telemetry_data.is_drone_connected = None
            case MsgNames.EULER_ANGLES.value:
                self.telemetry_data.roll_deg = None
                self.telemetry_data.pitch_deg = None
                self.telemetry_data.yaw_deg = None
            case MsgNames.HOME.value:
                self.telemetry_data.home_lat = None
                self.telemetry_data.home_lon = None
                self.telemetry_data.home_alt = None
            case MsgNames.EXTERNAL_GPS.value:
                self.telemetry_data.Device_GPS_lat = None
                self.telemetry_data.Device_GPS_lon = None
                self.telemetry_data.Device_GPS_alt = None
            case MsgNames.MISSION.value:
                self.telemetry_data.mission.current_progress = None
                self.telemetry_data.mission.total_progress = None
                self.telemetry_data.mission.status = None
                self.telemetry_data.mission.mission_plan = []
                self.telemetry_data.mission.mission_id = None
            case MsgNames.VTOL_STATE.value:
                self.telemetry_data.vtol_state = None
            case MsgNames.FW_LOITER_RADIUS.value:
                self.telemetry_data.FW_loiter_radius = None
            case MsgNames.RAW_MESSAGE.value:
                self.telemetry_data.mission.mission_id = None
                self.telemetry_data.custom_mode = None
                self.telemetry_data.autopilot_type = None
            case MsgNames.FLIGHT_INFO.value:
                self.telemetry_data.flight_info.flight_uid = None
                self.telemetry_data.flight_info.duration_since_takeoff_ms = None
                self.telemetry_data.flight_info.duration_since_arming_ms = None
                self.telemetry_data.flight_info.time_boot_ms = None
            case _:
                logger.error(f"Unknown task name: {name.value}")
                return
        logger.debug(f"Telemetry data for task {name.value} reset")    
    
    
    def reset_telemetry_data(self):
        """Reset the telemetry data to default values."""
        config = ConfigManager.get_config()
        external_gps = config.external_devices.get("gps")
        default_gps = "external" if external_gps and external_gps.enabled else "internal"
        self.telemetry_data = Telemetry.model_construct(default_gps=default_gps)
        logger.debug("Telemetry data reset to default values")
            
            
    def subscribe_msg(self, name: MsgNames):
        tasks_by_name = self.tasks_by_name
        if name.value in tasks_by_name:
            logger.warning(f"task:{name.value} already exists")
            return
        match name.value:
            case MsgNames.FLIGHT_MODE.value:
                task = asyncio.create_task(self._subscribe_flight_mode(), name=name.value)
            case MsgNames.POSITION.value:
                task = asyncio.create_task(self._subscribe_position(), name=name.value)
            case MsgNames.BATTERY.value:
                task = asyncio.create_task(self._subscribe_battery(), name=name.value)
            case MsgNames.VELOCITY.value:
                task = asyncio.create_task(self._subscribe_velocity(), name=name.value)
            case MsgNames.RC_STATUS.value:
                task = asyncio.create_task(self._subscribe_RC(), name=name.value)
            case MsgNames.IS_FLYING.value:
                task = asyncio.create_task(self._subscribe_isFlying(), name=name.value)
            case MsgNames.IS_ARMED.value:
                task = asyncio.create_task(self._subscribe_isArmed(), name=name.value)
            case MsgNames.HEALTH.value:
                task = asyncio.create_task(self._subscribe_health(), name=name.value)
            case MsgNames.IS_CONNECTED.value:
                task = asyncio.create_task(self._subscribe_is_connected(),
                                           name=name.value)
            case MsgNames.EULER_ANGLES.value:
                task = asyncio.create_task(self._subscribe_euler_angles(), name=name.value)
            case MsgNames.HOME.value:
                task = asyncio.create_task(self._subscribe_home(), name=name.value)
            case MsgNames.EXTERNAL_GPS.value:
                task = asyncio.create_task(self._subscribe_external_gps(), name=name.value)
            case MsgNames.MISSION.value:
                task = asyncio.create_task(self._subscribe_mission(), name=name.value)
            case MsgNames.VTOL_STATE.value:
                task = asyncio.create_task(self._subscribe_vtol_state(), name=name.value)
            case MsgNames.FW_LOITER_RADIUS.value:
                task = asyncio.create_task(self._subscribe_FW_loiter_radius(), name=name.value)
            case MsgNames.RAW_MESSAGE.value:
                task = asyncio.create_task(self._subscribe_raw_message(interval=0.02, switch_backend_on_generic_type="pymavlink"), name=name.value)
            case MsgNames.FLIGHT_INFO.value:
                task = asyncio.create_task(self._subscribe_flight_info(), name=name.value)
            case _:
                logger.error(f"Unknown task name: {name.value}")
                return
        self.tasks.append(task)
        logger.info(f"task:{name.value} subscribed")
        return task
            
    
    def tasks_status(self)-> dict[str, dict[str, bool]]:
        self.update_tasks()  # Clean up completed tasks
        current_status = {name: {"is_done": task.done(), "is_cancelled": task.cancelled()} 
                          for name, task in self.tasks_by_name.items()}
        return current_status
    
    async def get_latest_telemetry(self):
        """Return the latest cached telemetry data."""
        return self.telemetry_data
        
        
    def get_default_gps_data(self, relative_alt: bool = True) -> tuple[float, float, float]:
        # Just read the value set by _manage_disconnection
        data = self.telemetry_data
        match data.default_gps:
            case "internal":
                lat, lon, alt = data.Flight_GPS_lat, data.Flight_GPS_lon, data.Flight_GPS_alt if relative_alt else data.Flight_GPS_alt_abs
            case "external":
                lat, lon, alt = data.Device_GPS_lat, data.Device_GPS_lon, data.Device_GPS_alt
        return lat, lon, alt
