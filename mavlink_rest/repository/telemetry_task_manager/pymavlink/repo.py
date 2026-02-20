import math
import threading
import time
from enum import Enum
from typing import Optional, Callable, Iterable
from collections import defaultdict
import queue
from dataclasses import dataclass
from typing import Literal
from pymavlink import mavutil
from loguru import logger
import asyncio
import sys 
import psutil

# Assuming these imports exist in your project structure
from mavlink_rest.config import ConfigManager
# Ensure this points to the file we created earlier
from mavlink_rest.repository.external_devices.gps import AsyncGPSModule 
from ..schema import Telemetry
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from mavlink_rest.utils.utils import restart_app
from mavsdk.mission_raw import MissionItem as MissionRawItem
from .extensions import Action, Parameters, MissionRaw


# -------------------------- Message types --------------------------
class MsgNames(Enum):
    HEARTBEAT = "HEARTBEAT"
    EXTENDED_SYS_STATE = "EXTENDED_SYS_STATE"
    SYS_STATUS = "SYS_STATUS"
    BATTERY_STATUS = "BATTERY_STATUS"
    GLOBAL_POSITION_INT = "GLOBAL_POSITION_INT"
    LOCAL_POSITION_NED = "LOCAL_POSITION_NED"
    GPS_RAW_INT = "GPS_RAW_INT"
    ATTITUDE = "ATTITUDE"
    VFR_HUD = "VFR_HUD"
    ODOMETRY = "ODOMETRY"
    RC_CHANNELS = "RC_CHANNELS"
    HOME_POSITION = "HOME_POSITION"
    MISSION_CURRENT = "MISSION_CURRENT"
    EXTERNAL_GPS = "EXTERNAL_GPS"
    AUTOPILOT_VERSION = "AUTOPILOT_VERSION"
    SYS_TIME = "SYS_TIME"
    MISSION_ACK = "MISSION_ACK"


@dataclass
class MsgSlot:
    msg: object
    boot_ms: int
    wall_ts: float


# -------------------------- Telemetry Core --------------------------
class FlightTelemetry:
    """
    One background receiver thread:
      - recv_match()
      - updates latest message cache
      - updates Telemetry immediately via handlers
      - feeds a small message bus for mission/param RPCs.
    """

    def __init__(self, verbose: bool = True, global_delay: float = 0.01):
        config = ConfigManager.get_config(raise_ifNone=False)
        if config is not None:
            gps_cfg = config.external_devices.get("gps")
            default_gps = "external" if gps_cfg and gps_cfg.enabled else "internal"
        else:
            logger.warning("Config not loaded yet; using defaults.")
            default_gps = "internal"

        self.telemetry_data = Telemetry.model_construct(
            default_gps=default_gps,
        )

        self._latest: dict[str, MsgSlot] = {}
        self.master: Optional[mavutil.mavfile] = None
        self.system_address = None

        # threading.Lock is correct for hybrid Thread/Asyncio use, 
        # BUT it must be used with 'with', never 'async with'.
        self._lock = threading.Lock()
        
        self._connected = False
        self._verbose = verbose
        self.global_delay = global_delay

        # message-type -> handler
        self.msg_type_handler_mapper = {
            "HEARTBEAT": self._on_heartbeat,
            "EXTENDED_SYS_STATE": self._on_extended_sys_state,
            "SYS_STATUS": self._on_sys_status,
            "BATTERY_STATUS": self._on_battery_status,
            "GLOBAL_POSITION_INT": self._on_global_position_int,
            "LOCAL_POSITION_NED": self._on_local_position_ned,
            "GPS_RAW_INT": self._on_gps_raw_int,
            "ATTITUDE": self._on_attitude,
            "VFR_HUD": self._on_vfr_hud,
            "ODOMETRY": self._on_odometry,
            "RC_CHANNELS": self._on_rc_channels,
            "HOME_POSITION": self._on_home_position,
            "MISSION_CURRENT": self._on_mission_current,
            "AUTOPILOT_VERSION": self._on_autopilot_version,
            "SYS_TIME": self._on_sys_time,
            # MISSION_ACK is handled via bus usually, but we can hook it if needed
        }

        # message bus: type -> Queue (used for mission/param wait_for)
        self._bus: dict[str, queue.Queue] = defaultdict(lambda: queue.Queue(maxsize=64))
        self._mission_proto_types = {
            "MISSION_COUNT",
            "MISSION_ITEM",
            "MISSION_ITEM_INT",
            "MISSION_ACK",
            "MISSION_REQUEST",
            "MISSION_REQUEST_INT",
        }
        self._param_proto_types = {"PARAM_VALUE"}

        # helpers (initialized in connect)
        self.action: Optional[Action] = None
        self.mission_raw: Optional[MissionRaw] = None
        self.parameters: Optional[Parameters] = None

    @staticmethod
    def _mission_item_to_dict(item: MissionRawItem) -> dict:
        mission_type = getattr(item, "mission_type", None)
        if hasattr(mission_type, "value"):
            mission_type = mission_type.value

        x = int(getattr(item, "x", 0))
        y = int(getattr(item, "y", 0))
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
            "x": x,
            "y": y,
            "z": float(getattr(item, "z", 0.0)),
            "lat_deg": x / 1e7 if x else None,
            "lon_deg": y / 1e7 if y else None,
            "mission_type": mission_type,
        }

    async def _sync_mission_plan(self):
        if not self.mission_raw:
            return
        try:
            mission_items = await self.mission_raw.download_mission(timeout_per_item=2.0)
        except Exception as e:
            logger.debug(f"Mission plan sync skipped: {e.__class__.__name__}: {e}")
            return

        mission_plan = [self._mission_item_to_dict(item) for item in mission_items]
        with self._lock:
            self.telemetry_data.mission.mission_plan = mission_plan
            if mission_plan:
                self.telemetry_data.mission.total_progress = len(mission_plan)
                if self.telemetry_data.mission.status in (None, "UNKNOWN", "NO_MISSION"):
                    self.telemetry_data.mission.status = "NOT_STARTED"
            else:
                self.telemetry_data.mission.total_progress = 0
                self.telemetry_data.mission.current_progress = 0
                self.telemetry_data.mission.status = "NO_MISSION"
                self.telemetry_data.mission.mission_id = None

    # ---- optional late config injection ----
    def init(self):
        cfg = ConfigManager.get_config()
        gps_cfg = cfg.external_devices.get("gps")
        self.telemetry_data.default_gps = "external" if gps_cfg and gps_cfg.enabled else "internal"


    # ---- connection & rates ---------------------------------------------------------
    @retry(
        stop=stop_after_attempt(4),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ConnectionError),
        before_sleep=lambda rs: logger.info(f"Retrying connection: attempt {rs.attempt_number}"),
    )
    def connect(
        self,
        system_address: str,
        baud: int = 57600,
        system_id: int = 240,
        component_id: int = 180,
        heartbeat_timeout: float = 10.0,
    ):
        self.system_address = system_address
        logger.info(f"Connecting to MAVLink at {system_address}")

        self.master = mavutil.mavlink_connection(
            system_address.replace("/", ""),
            baud=baud,
            source_system=system_id,
            source_component=component_id,
            autoreconnect=True,
        )

        hb = self.master.wait_heartbeat(timeout=heartbeat_timeout)
        if not hb:
            raise ConnectionError(f"No heartbeat within {heartbeat_timeout}s on {system_address}")

        self._connected = True
        self.telemetry_data.is_drone_connected = True

        logger.success(
            f"Connected! sysid={self.master.target_system} compid={self.master.target_component} "
            f"mode={mavutil.mode_string_v10(hb)}"
        )

        # high-level helpers
        self.action = Action(self.master, get_vehicle_type=lambda: self.telemetry_data.drone_type)
        self.mission_raw = MissionRaw(self.master, wait_for=self.wait_for)
        self.parameters = Parameters(self.master, wait_for=self.wait_for)

    def set_message_rates(self, hz: dict[int, float]):
        if not self.master:
            logger.warning("Cannot set message rates before connect()")
            return

        for msg_id, rate in hz.items():
            interval_us = -1 if rate <= 0 else int(1_000_000 / float(rate))
            try:
                self.master.mav.command_long_send(
                    self.master.target_system,
                    self.master.target_component,
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    0,
                    msg_id,
                    interval_us,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
                if self._verbose:
                    logger.debug(f"Requested rate {rate} Hz for msg_id={msg_id}")
            except Exception as e:
                logger.error(f"Failed to set rate for msg_id={msg_id}: {e}")

    async def subscribe_telemetry(
        self,
        msgs_to_subscribe: list[MsgNames]
        | None = [
            MsgNames.HEARTBEAT,
            MsgNames.GLOBAL_POSITION_INT,
            MsgNames.LOCAL_POSITION_NED,
            MsgNames.ATTITUDE,
            MsgNames.VFR_HUD,
            MsgNames.RC_CHANNELS,
            MsgNames.SYS_STATUS,
            MsgNames.EXTENDED_SYS_STATE,
            MsgNames.BATTERY_STATUS,
            MsgNames.HOME_POSITION,
            MsgNames.MISSION_CURRENT,
            MsgNames.ODOMETRY,
            MsgNames.GPS_RAW_INT,
        ],
    ):
        """
        Starts the receiver thread. Handlers run in that thread; this
        coroutine just keeps things alive until cancelled.
        """
        if not self.master:
            raise RuntimeError("Call connect() before subscribe_telemetry().")

        msg_types_str: list[str] = [m.value for m in msgs_to_subscribe] if msgs_to_subscribe else None

        stop = threading.Event()
        thread = threading.Thread(
            target=self._receiver_loop,
            args=(stop, msg_types_str, True),
            daemon=True,
        )
        thread.start()
        logger.success("Started MAVLink receiver thread.")

        external_gps_task = None
        disconnect_manager_task = None
        flight_info_task = None
        mission_changed_task = None
        
        try:
            config = ConfigManager.get_config()
            tasks = []
            
            # 1. External GPS
            gps_cfg = config.external_devices.get("gps")
            if gps_cfg and gps_cfg.enabled:
                logger.info("Subscribing to external GPS data")
                external_gps_task = asyncio.create_task(self._subscribe_external_gps(), name=MsgNames.EXTERNAL_GPS.value) 
                tasks.append(external_gps_task)
            
            # 2. Disconnect Manager (Smart Switching)
            logger.info("Starting disconnect/GPS manager")
            disconnect_manager_task = asyncio.create_task(
                self._manage_disconnection(reset_app_after_n_seconds=60, reset_telemetry_after_n_seconds=5), 
                name="Disconnect_manager"
            )
            tasks.append(disconnect_manager_task)

            # 3. Flight Info (UID, Boot time)
            flight_info_task = asyncio.create_task(self._subscribe_flight_info(interval=10), name="FlightInfoTask")
            tasks.append(flight_info_task)

            # 4. Mission Changed Monitor
            mission_changed_task = asyncio.create_task(self._subscribe_mission_changed(), name="MissionChangedTask")
            tasks.append(mission_changed_task)

            # Keep the loop alive
            await asyncio.gather(*tasks)
                
        except asyncio.CancelledError:
            logger.info("Telemetry subscription cancelled")
        finally:
            if external_gps_task: external_gps_task.cancel()
            if disconnect_manager_task: disconnect_manager_task.cancel()
            if flight_info_task: flight_info_task.cancel()
            if mission_changed_task: mission_changed_task.cancel()
            
            stop.set()
            thread.join(2)
            try:
                self.master.close()
            except Exception:
                pass
            logger.info("Stopped MAVLink receiver thread")
            
    
    @property
    def mission(self):
        return self        
    

    # ---- raw cache helpers ----------------------------------------------------------
    def _update_raw_data(self, msg) -> None:
        mtype = msg.get_type()
        if mtype == "HEARTBEAT" and (msg.type == 6 or msg.custom_mode is None):  # skip GCS
            return
        boot_ms = getattr(msg, "time_boot_ms", getattr(msg, "time_usec", -1))
        if hasattr(msg, "time_usec"):
            boot_ms = int(msg.time_usec // 1000)

        # NOTE: Lock is held by caller (_receiver_loop)
        prev = self._latest.get(mtype)
        if prev is None or boot_ms == -1 or boot_ms > prev.boot_ms:
            self._latest[mtype] = MsgSlot(msg=msg, boot_ms=boot_ms, wall_ts=time.monotonic())

    def _get_raw_msg(self, mtype: MsgNames) -> Optional[MsgSlot]:
        # Thread-safe access from Async loop or other threads
        with self._lock:
            return self._latest.get(mtype.value)

    # ---- message bus ---------------------------------------------------------------
    def mailbox(self, *types: str) -> dict[str, queue.Queue]:
        m = {}
        for t in types:
            if t not in self._bus:
                self._bus[t] = queue.Queue(maxsize=64)
            m[t] = self._bus[t]
        return m

    def _bus_put(self, msg):
        mtype = msg.get_type()
        if mtype not in self._mission_proto_types and mtype not in self._param_proto_types:
            return
        q = self._bus.get(mtype)
        if q:
            try:
                q.put_nowait(msg)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                q.put_nowait(msg)

    def wait_for(
        self,
        types: str | Iterable[str],
        timeout: float = 3.0,
        predicate: Optional[Callable] = None,
    ):
        if isinstance(types, str):
            types = [types]
        queues = [self.mailbox(t)[t] for t in types]
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            for q in queues:
                try:
                    msg = q.get(timeout=min(0.05, remaining))
                    if (predicate is None) or predicate(msg):
                        return msg
                except queue.Empty:
                    continue

    # ---- receiver loop --------------------------------------------------------------
    def _receiver_loop(self, stop: threading.Event, types=None, flush_start=True):
        try:
            self.set_message_rates(
                {
                    mavutil.mavlink.MAVLINK_MSG_ID_HEARTBEAT: 10,
                    mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE: 5,
                    mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS: 2,
                    mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 10,
                    mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED: 5,
                    mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 5,
                    mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE: 20,
                    mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD: 10,
                    mavutil.mavlink.MAVLINK_MSG_ID_RC_CHANNELS: 2,
                    mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION: 1,
                    mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT: 2,
                    mavutil.mavlink.MAVLINK_MSG_ID_MISSION_COUNT: 1,
                    mavutil.mavlink.MAVLINK_MSG_ID_BATTERY_STATUS: 2,
                }
            )
        except Exception:
            logger.warning("Failed to set message rates; continuing.")

        # always include mission/param protocol frames
        if types is not None:
            types = list(set(types) | self._mission_proto_types | self._param_proto_types | {"AUTOPILOT_VERSION", "SYS_TIME"})

        if flush_start:
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.5:
                if self.master.recv_match(blocking=False) is None:
                    break

        while not stop.is_set():
            msg = self.master.recv_match(type=types, blocking=True, timeout=0.1)
            if not msg:
                continue

            # CRITICAL: We must hold the lock while updating raw data AND while
            # updating telemetry_data via handlers, because the async GPS loop
            # also accesses telemetry_data from a different thread/context.
            with self._lock:
                self._update_raw_data(msg)
                
                # decode immediately for telemetry
                handler = self.msg_type_handler_mapper.get(msg.get_type().upper())
                if handler:
                    try:
                        handler(msg)
                    except Exception as e:
                        logger.error(f"Telemetry handler failed for {msg.get_type()}: {e}")

            # Put in bus outside lock (Queues are thread-safe)
            self._bus_put(msg)


    # ---- public telemetry getter ----------------------------------------------------
    async def get_latest_telemetry(self) -> Telemetry:
        # Ideally we return a copy or use the lock here if the user reads this, 
        # but Pydantic models aren't strictly thread-safe for concurrent read/write
        # without GIL help. For basic usage, this is fine.
        return self.telemetry_data
    
    
    async def _subscribe_external_gps(self):
        config = ConfigManager.get_config()
        gps_conf = config.external_devices.get("gps")
        if not gps_conf or not gps_conf.enabled:
            logger.debug("External GPS not enabled in config")
            return
        try:
            async with AsyncGPSModule(port=gps_conf.COM, baudrate=gps_conf.baud) as gps:
                async for pos in gps.stream_gps_data(interval=0.3):
                    # IMPORTANT: threading.Lock does not support 'async with'.
                    # We must use standard 'with' because we are syncing with a Thread.
                    with self._lock:
                        if pos is not None:
                            self.telemetry_data.Device_GPS_lat = pos.lat
                            self.telemetry_data.Device_GPS_lon = pos.lon
                            self.telemetry_data.Device_GPS_alt = pos.alt_rel
                            self.telemetry_data.Device_GPS_alt_abs = pos.alt_abs
        except asyncio.CancelledError:
            logger.info("External GPS task cancelled")
        except Exception as e:
            logger.error(f"Error while getting external gps data: {e}")
            with self._lock:
                self.telemetry_data.Device_GPS_lat = None
                self.telemetry_data.Device_GPS_lon = None
                self.telemetry_data.Device_GPS_alt = None
                self.telemetry_data.Device_GPS_alt_abs = None

    
    async def _manage_disconnection(self, reset_telemetry_after_n_seconds: int | None = None,
                                        reset_app_after_n_seconds: int | None = None):
            """
            Manage drone disconnection and dynamic GPS switching based on data activity.
            - Checks for GPS data changes every 5 seconds.
            - If 'Flight_GPS' (internal) changes, sets default to 'internal'.
            - Otherwise, if 'Device_GPS' (external) changes, sets default to 'external'.
            - Handles app restart and data reset on disconnection.
            """
            if reset_app_after_n_seconds is None and reset_telemetry_after_n_seconds is not None:
                reset_app_after_n_seconds = reset_telemetry_after_n_seconds * 3

            # Initialize previous GPS values
            with self._lock:
                prev_flight_lat = round(self.telemetry_data.Flight_GPS_lat, 6) if isinstance(self.telemetry_data.Flight_GPS_lat, float) else None
                prev_flight_lon = round(self.telemetry_data.Flight_GPS_lon, 6) if isinstance(self.telemetry_data.Flight_GPS_lon, float) else None
                prev_device_lat = round(self.telemetry_data.Device_GPS_lat, 6) if isinstance(self.telemetry_data.Device_GPS_lat, float) else None
                prev_device_lon = round(self.telemetry_data.Device_GPS_lon, 6) if isinstance(self.telemetry_data.Device_GPS_lon, float) else None

            init_time = None

            try:
                while True:
                    await asyncio.sleep(4)

                    with self._lock:
                        curr_flight_lat = round(self.telemetry_data.Flight_GPS_lat, 6) if isinstance(self.telemetry_data.Flight_GPS_lat, float) else None
                        curr_flight_lon = round(self.telemetry_data.Flight_GPS_lon, 6) if isinstance(self.telemetry_data.Flight_GPS_lon, float) else None
                        curr_device_lat = round(self.telemetry_data.Device_GPS_lat, 6) if isinstance(self.telemetry_data.Device_GPS_lat, float) else None
                        curr_device_lon = round(self.telemetry_data.Device_GPS_lon, 6) if isinstance(self.telemetry_data.Device_GPS_lon, float) else None
                        is_connected = self.telemetry_data.is_drone_connected

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

                        # Update Default GPS Source
                        if internal_changed or (is_connected and internal_valid):
                            self.telemetry_data.default_gps = "internal"
                        else:
                            self.telemetry_data.default_gps = "external"

                        # Update 'previous' values
                        prev_flight_lat = curr_flight_lat
                        prev_flight_lon = curr_flight_lon
                        prev_device_lat = curr_device_lat
                        prev_device_lon = curr_device_lon

                    # --- Disconnection Timer Logic ---
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
                                self.init() # Reset data to defaults

            except asyncio.CancelledError:
                logger.info("Disconnect manager task cancelled")
            except Exception as e:
                logger.error(f"Error in _manage_disconnection: {e}")


    async def _subscribe_flight_info(self, interval: int = 10):
        """Polls for Autopilot Version and System Time periodically."""
        while True:
            try:
                if self.master:
                    # Request Autopilot Version
                    self.master.mav.command_long_send(
                        self.master.target_system,
                        self.master.target_component,
                        mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                        0,
                        mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION,
                        0, 0, 0, 0, 0, 0
                    )
                    # Request System Time (for boot time)
                    self.master.mav.command_long_send(
                        self.master.target_system,
                        self.master.target_component,
                        mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                        0,
                        mavutil.mavlink.MAVLINK_MSG_ID_SYS_TIME,
                        0, 0, 0, 0, 0, 0
                    )
            except Exception as e:
                logger.debug(f"Failed to request flight info: {e}")
            
            await asyncio.sleep(interval)

    async def _subscribe_mission_changed(self):
        """
        Polls or listens for indication of mission change.
        For PyMavlink, we rely on observing MISSION_ACK (accepted) or similar events via the bus.
        """
        await self._sync_mission_plan()
        last_sync_at = time.monotonic()

        while True:
            should_sync = False

            ack_msg = self.wait_for("MISSION_ACK", timeout=0.2)
            if ack_msg and int(getattr(ack_msg, "type", -1)) == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                logger.debug("Mission ACK accepted, syncing mission plan")
                should_sync = True

            count_msg = self.wait_for("MISSION_COUNT", timeout=0.2)
            if count_msg:
                incoming_count = int(getattr(count_msg, "count", -1))
                current_count = len(self.telemetry_data.mission.mission_plan or [])
                if incoming_count != current_count:
                    logger.debug(f"Mission count changed ({current_count} -> {incoming_count}), syncing mission plan")
                    should_sync = True

            if (
                int(self.telemetry_data.mission.total_progress or 0) > 0
                and len(self.telemetry_data.mission.mission_plan or []) == 0
                and (time.monotonic() - last_sync_at) > 5
            ):
                should_sync = True

            if should_sync:
                await self._sync_mission_plan()
                last_sync_at = time.monotonic()

            await asyncio.sleep(1.0)


    # ---- decoders -------------------------------------------------------------------
    def _handle_msg(self, msg, type_filter: str | None = None):
        if type_filter:
            handler = self.msg_type_handler_mapper.get(type_filter.upper())
        else:
            handler = self.msg_type_handler_mapper.get(msg.get_type().upper())
        if handler:
            handler(msg)
        elif self._verbose:
            logger.debug(f"No handler for message type: {msg.get_type()}")

    @staticmethod
    def _translate__mode_name(mode_name: str) -> str:
        mapping = {
            "STABILIZE": "MANUAL",
            "ACRO": "ACRO",
            "ALT_HOLD": "ALT_HOLD",
            "AUTO": "MISSION",
            "GUIDED": "HOLD",
            "LOITER": "HOLD",
            "RTL": "RTL",
            "CIRCLE": "ORBIT",
            "LAND": "LAND",
            "DRIFT": "DRIFT",
            "SPORT": "SPORT",
            "FLIP": "FLIP",
            "AUTOTUNE": "AUTOTUNE",
            "POSHOLD": "POSHOLD",
            "BRAKE": "BRAKE",
            "THROW": "THROW",
            "AVOID_ADSB": "AVOID_ADSB",
            "GUIDED_NOGPS": "GUIDED_NOGPS",
            "SMART_RTL": "SMART_RTL",
        }
        return mapping.get(mode_name.upper(), mode_name.upper())

    def _on_heartbeat(self, hb):
        mode = mavutil.mode_string_v10(hb) or "UNKNOWN"
        mode = self._translate__mode_name(mode)
        armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if int(hb.type) == 6:  # GCS
            return

        self.telemetry_data.flight_mode = mode
        self.telemetry_data.custom_mode = int(hb.custom_mode)
        self.telemetry_data.is_armed = armed
        self.telemetry_data.is_drone_connected = True

        vt = int(hb.type)
        mapping = {
            1: "FIXED_WING",
            2: "QUADROTOR",
            13: "HEXAROTOR",
            4: "HELICOPTER",
            43: "GENERIC_MULTIROTOR",
        }
        self.telemetry_data.drone_type = mapping.get(vt, "GENERIC")
        if self.telemetry_data.drone_type in ("QUADROTOR", "HEXAROTOR", "GENERIC_MULTIROTOR"):
            self.telemetry_data.vtol_state = "MC"
        elif self.telemetry_data.drone_type == "FIXED_WING":
            self.telemetry_data.vtol_state = "FW"

        ap = int(hb.autopilot)
        self.telemetry_data.autopilot_type = (
            "PX4" if ap == 12 else ("APM" if ap == 3 else "GENERIC")
        )

        # Switch backend on nonGeneric type
        switch_backend_on_nonGeneric_type: Literal["mavsdk", "pymavlink"]|None = "mavsdk"
        FC_type = self.telemetry_data.autopilot_type
        if FC_type is not None and FC_type != "GENERIC" and switch_backend_on_nonGeneric_type is not None:
            restart_app(backend=switch_backend_on_nonGeneric_type)

        sid = int(hb.system_status)
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

        if self._verbose:
            logger.debug(f"Mode={mode} Armed={armed}")

    def _on_extended_sys_state(self, msg):
        # Update flying state
        in_air = msg.landed_state in (
            mavutil.mavlink.MAV_LANDED_STATE_IN_AIR,
            mavutil.mavlink.MAV_LANDED_STATE_TAKEOFF,
            mavutil.mavlink.MAV_LANDED_STATE_LANDING,
        )
        self.telemetry_data.is_flying = bool(in_air)
        
        # Update VTOL state if available
        vtol_st = msg.vtol_state
        # MAV_VTOL_STATE: 1=TRANSITION_TO_FW, 2=TRANSITION_TO_MC, 3=MC, 4=FW
        if vtol_st == mavutil.mavlink.MAV_VTOL_STATE_MC:
            self.telemetry_data.vtol_state = "MC"
        elif vtol_st == mavutil.mavlink.MAV_VTOL_STATE_FW:
            self.telemetry_data.vtol_state = "FW"
        elif vtol_st == mavutil.mavlink.MAV_VTOL_STATE_TRANSITION_TO_FW:
            self.telemetry_data.vtol_state = "TO_FW"
        elif vtol_st == mavutil.mavlink.MAV_VTOL_STATE_TRANSITION_TO_MC:
            self.telemetry_data.vtol_state = "TO_MC"

    def _on_sys_status(self, s):
        remain = s.battery_remaining if s.battery_remaining != 255 else None
        current_a = None if s.current_battery == 65535 else (s.current_battery / 100.0)
        self.telemetry_data.battery_remain = int(remain) if remain is not None else None
        self.telemetry_data.battery_amper = current_a
        if self._verbose:
            logger.debug(f"Battery: {remain}% {current_a}A")

    def _on_battery_status(self, b):
        remain = None if b.battery_remaining == 255 else int(b.battery_remaining)
        current_a = None if b.current_battery == -1 else (b.current_battery / 100.0)
        if remain is not None:
            self.telemetry_data.battery_remain = remain
        if current_a is not None:
            self.telemetry_data.battery_amper = current_a

    def _on_global_position_int(self, g):
        lat = g.lat / 1e7
        lon = g.lon / 1e7
        rel_alt = g.relative_alt / 1000.0
        abs_alt = g.alt / 1000.0
        self.telemetry_data.Flight_GPS_lat = lat
        self.telemetry_data.Flight_GPS_lon = lon
        self.telemetry_data.Flight_GPS_alt = rel_alt
        self.telemetry_data.Flight_GPS_alt_abs = abs_alt
        if self._verbose:
            logger.debug(
                f"Position: lat={lat:.6f} lon={lon:.6f} rel={rel_alt:.1f}m abs={abs_alt:.1f}m"
            )

    def _on_local_position_ned(self, l):
        self.telemetry_data.vx = float(l.vx)
        self.telemetry_data.vy = float(l.vy)
        self.telemetry_data.vz = float(l.vz)

    def _on_gps_raw_int(self, g):
        lat = None if g.lat in (0, None) else g.lat / 1e7
        lon = None if g.lon in (0, None) else g.lon / 1e7
        alt_abs = None if g.alt is None else g.alt / 1000.0
        if lat is not None:
            self.telemetry_data.Flight_GPS_lat = lat
        if lon is not None:
            self.telemetry_data.Flight_GPS_lon = lon
        if alt_abs is not None:
            self.telemetry_data.Flight_GPS_alt_abs = alt_abs

    def _on_attitude(self, a):
        self.telemetry_data.roll_deg = math.degrees(a.roll)
        self.telemetry_data.pitch_deg = math.degrees(a.pitch)
        self.telemetry_data.yaw_deg = math.degrees(a.yaw)

    def _on_vfr_hud(self, v):
        self.telemetry_data.speed = float(v.groundspeed)
        self.telemetry_data.air_speed = float(v.airspeed)

    def _on_odometry(self, o):
        self.telemetry_data.vx = float(o.vx)
        self.telemetry_data.vy = float(o.vy)
        self.telemetry_data.vz = float(o.vz)

    def _on_rc_channels(self, rc):
        rssi = None if rc.rssi == 255 else rc.rssi
        self.telemetry_data.rc_connected = (rssi is not None) and (rssi > 0)

    def _on_home_position(self, hp):
        self.telemetry_data.home_lat = hp.latitude / 1e7
        self.telemetry_data.home_lon = hp.longitude / 1e7
        self.telemetry_data.home_alt = hp.altitude / 1000.0

    def _on_mission_current(self, mc):
        self.telemetry_data.mission.current_progress = int(mc.seq)
        self.telemetry_data.mission.total_progress = int(getattr(mc, "total", 0))
        mstate = int(getattr(mc, "mission_state", 0))
        mapping = {1: "NO_MISSION", 2: "NOT_STARTED", 3: "ACTIVE", 4: "PAUSED", 5: "COMPLETE"}
        self.telemetry_data.mission.status = mapping.get(mstate, "UNKNOWN")
        
    def _on_autopilot_version(self, msg):
        if hasattr(msg, "uid"):
            self.telemetry_data.flight_info.flight_uid = msg.uid
            
    def _on_sys_time(self, msg):
        if hasattr(msg, "time_boot_ms"):
            self.telemetry_data.flight_info.time_boot_ms = msg.time_boot_ms

    # ---- background param helper example -------------------------------------------
    async def nav_radius_updater(self, interval_s: float = 10.0):
        if not self.parameters:
            raise RuntimeError("Parameters helper not initialized.")
        self.telemetry_data.FW_loiter_radius = None

        while True:
            try:
                if self.telemetry_data.vtol_state != "FW":
                    self.telemetry_data.FW_loiter_radius = None
                    await asyncio.sleep(interval_s)
                    continue

                # run param calls in a thread to avoid blocking loop
                radius = await asyncio.to_thread(
                    self.parameters.get_parameter, "NAV_LOITER_RAD" # for px4
                ) or await asyncio.to_thread(
                    self.parameters.get_parameter, "WP_LOITER_RAD" # for ardupilot
                )

                if radius is not None:
                    self.telemetry_data.FW_loiter_radius = float(radius)
            except Exception:
                logger.warning("Failed to refresh loiter radius")
            await asyncio.sleep(interval_s)


    def get_default_gps_data(self, relative_alt: bool = True) -> tuple[float, float, float]:
        # Use value set by _manage_disconnection
        data = self.telemetry_data
        match data.default_gps:
            case "internal":
                lat, lon, alt = data.Flight_GPS_lat, data.Flight_GPS_lon, data.Flight_GPS_alt if relative_alt else data.Flight_GPS_alt_abs
            case "external":
                lat, lon, alt = data.Device_GPS_lat, data.Device_GPS_lon, data.Device_GPS_alt
        return lat, lon, alt


    async def get_return_to_launch_after_mission(self) -> bool:
        return True

    async def set_return_to_launch_after_mission(self, enable: bool = True):
        self.telemetry_data.mission.must_rtl_at_end = enable
