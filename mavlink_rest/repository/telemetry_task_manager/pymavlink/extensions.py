from loguru import logger
from mavlink_rest.config import ConfigManager
from typing import Callable, Optional
from pymavlink import mavutil
from mavsdk.mission_raw import MissionItem as MissionRawItem
import time, asyncio
from dataclasses import dataclass



# -------------------------- Actions --------------------------
class Action:
    def __init__(self, connection: mavutil.mavfile, get_vehicle_type: Callable[[], Optional[str]]):
        self.__master = connection
        self._get_vehicle_type = get_vehicle_type

    def _set_mode(self, custom_mode: int):
        self.__master.mav.set_mode_send(
            self.__master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            custom_mode,
        )
        self.__master.set_mode_apm(custom_mode)

    async def hold(self):
        config = ConfigManager.get_config()
        modes = config.drone.properties.FLIGHT_MODES
        hold_code = modes.get("HOLD") or modes.get("LOITER") or modes.get("GUIDED")
        if hold_code is None:
            logger.error("HOLD mode not configured.")
            return
        self._set_mode(hold_code)

    async def return_to_launch(self):
        self.__master.set_mode_rtl()

    async def land(self):
        config = ConfigManager.get_config()
        modes = config.drone.properties.FLIGHT_MODES
        land_code = modes.get("LAND")
        if land_code is None:
            logger.error("LAND mode not configured.")
            return
        self._set_mode(land_code)

    async def disarm(self):
        self.__master.mav.command_long_send(
            self.__master.target_system,
            self.__master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )

    async def set_current_speed(self, speed_m_s: float, *, speed_type: int | None = None):
        if speed_type is None:
            vtype = (self._get_vehicle_type() or "").upper()
            speed_type = 0 if "FIXED_WING" in vtype else 1

        self.__master.mav.command_long_send(
            self.__master.target_system,
            self.__master.target_component,
            mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            0,
            speed_type,
            float(speed_m_s),
            -1,
            0,
            0,
            0,
            0,
        )
        

    async def goto_location(
        self,
        latitude_deg: float,
        longitude_deg: float,
        altitude_m: float,
        yaw_deg: float | None = None,
        *,
        vehicle_hint: str | None = None,
    ):
        # Ensure guided/hold-like mode
        await self.hold()

        vtype = (vehicle_hint or (self._get_vehicle_type() or "")).upper()

        if any(x in vtype for x in ("PLANE", "FIXED_WING", "FW")):
            await self._plane_goto_location(latitude_deg, longitude_deg, altitude_m)
        else:
            await self._copter_goto_location(
                latitude_deg,
                longitude_deg,
                altitude_m,
                yaw_deg=yaw_deg,
            )


    async def _copter_goto_location(
        self,
        lat_deg: float,
        lon_deg: float,
        alt_m: float,
        *,
        frame: int = mavutil.mavlink.MAV_FRAME_GLOBAL,
        yaw_deg: float | None = None,
    ):
        # Best effort: go to GUIDED/LOITER/HOLD first
        try:
            await self.hold()
        except Exception:
            logger.warning("Failed to switch to HOLD mode before goto_location.")

        # Use documented sentinel values instead of NaN
        ground_speed = -1      # -1 => use current/default
        bitmask = 0            # 0 => use pos+alt+yaw
        radius = 0             # 0 => use default
        yaw = float('nan') if yaw_deg is None else yaw_deg

        self.__master.mav.command_int_send(
            self.__master.target_system,
            self.__master.target_component,
            frame,                                   # GLOBAL_RELATIVE_ALT_INT or GLOBAL_INT
            mavutil.mavlink.MAV_CMD_DO_REPOSITION,
            0,                                       # current (unused)
            0,                                       # autocontinue (unused)
            float(ground_speed),                     # param1
            float(bitmask),                          # param2
            float(radius),                           # param3
            float(yaw),                              # param4
            int(lat_deg * 1e7),
            int(lon_deg * 1e7),
            float(alt_m),
        )

        

    async def _plane_goto_location(
        self,
        lat_deg: float,
        lon_deg: float,
        alt_m: float,
        *,
        frame=mavutil.mavlink.MAV_FRAME_GLOBAL_INT,
    ):
        self.__master.mav.mission_item_int_send(
            self.__master.target_system,
            self.__master.target_component,
            0,
            frame,
            mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
            2,  # guided goto
            0,
            0,
            0,
            0,
            float("nan"),
            int(lat_deg * 1e7),
            int(lon_deg * 1e7),
            float(alt_m),
        )
        
    
    def send_statustext_GS(self, text: str):
        self.__master.mav.statustext_send( mavutil.mavlink.MAV_SEVERITY_WARNING, text.encode('utf-8') )



# -------------------------- Missions (raw) --------------------------
class MissionRaw:
    def __init__(self, connection: mavutil.mavfile, wait_for: Callable):
        self.__master = connection
        self._wait_for = wait_for
        self.mission_items: list[MissionRawItem] = []

    def _set_mode(self, custom_mode: int):
        self.__master.mav.set_mode_send(
            self.__master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            custom_mode,
        )
        self.__master.set_mode_apm(custom_mode)


    async def set_current_mission_item(self, seq: int):
        """
        Sets the current active mission waypoint sequence number.
        """
        self.__master.mav.mission_set_current_send(
            self.__master.target_system,
            self.__master.target_component,
            seq
        )
        # No ACK for this message in MAVLink v1/v2 usually, or it's just accepted silently.
        return True
    

    async def clear_mission(self):
        """
        Async wrapper for clearing mission to avoid blocking event loop.
        """
        def _clear():
            self.__master.mav.mission_clear_all_send(
                self.__master.target_system, self.__master.target_component
            )
            # Wait for ACK
            ack = self._wait_for("MISSION_ACK", timeout=2.0)
            if not ack or ack.type != mavutil.mavlink.MAV_MISSION_ACCEPTED:
                # If busy, try once more after sleep
                time.sleep(0.5)
                self.__master.mav.mission_clear_all_send(
                    self.__master.target_system, self.__master.target_component
                )
                ack = self._wait_for("MISSION_ACK", timeout=2.0)
                if not ack or ack.type != mavutil.mavlink.MAV_MISSION_ACCEPTED:
                    logger.warning(f"Clear mission failed or timed out: {ack}")
        
        await asyncio.to_thread(_clear)


    # ... [Keep pause_mission, start_mission as they were, or wrap them if needed] ...
    async def pause_mission(self):
        # Wrapper to match MAVSDK interface which is async
        def _pause():
            config = ConfigManager.get_config()
            flight_codes = config.drone.properties.FLIGHT_MODES
            hold_code = (
                flight_codes.get("HOLD")
                or flight_codes.get("LOITER")
                or flight_codes.get("GUIDED")
            )
            if hold_code is None:
                logger.error("HOLD mode not configured; cannot pause mission.")
                return
            self._set_mode(hold_code)
        await asyncio.to_thread(_pause)


    async def start_mission(self):
        def _start():
            config = ConfigManager.get_config()
            flight_codes = config.drone.properties.FLIGHT_MODES
            auto_code = flight_codes.get("MISSION") or flight_codes.get("AUTO")
            if auto_code is None:
                logger.error("MISSION/AUTO mode not configured; cannot start mission.")
                return
            self._set_mode(auto_code)
        await asyncio.to_thread(_start)

    # --- Fix 2: Sync -> Async wrapper for Download ---
    async def download_mission(self, timeout_per_item: float = 5.0) -> list[MissionRawItem]:
        return await asyncio.to_thread(self._download_mission_sync, timeout_per_item)


    def _download_mission_sync(self, timeout_per_item: float) -> list[MissionRawItem]:
        # [Existing synchronous logic goes here]
        self.__master.mav.mission_request_list_send(
            self.__master.target_system, self.__master.target_component
        )
        
        cnt_msg = self._wait_for("MISSION_COUNT", timeout=timeout_per_item)
        if not cnt_msg:
            # Retry request once
            self.__master.mav.mission_request_list_send(
                self.__master.target_system, self.__master.target_component
            )
            cnt_msg = self._wait_for("MISSION_COUNT", timeout=timeout_per_item)
            if not cnt_msg:
                raise TimeoutError("Timed out waiting for MISSION_COUNT")
        
        count = int(cnt_msg.count)
        if count == 0:
            self.mission_items = []
            return []

        self.mission_items = [None] * count

        for seq in range(count):
            # ... [Keep your existing download loop logic] ...
            self.__master.mav.mission_request_int_send(
                self.__master.target_system,
                self.__master.target_component,
                seq,
            )
            mi = self._wait_for(
                ["MISSION_ITEM_INT", "MISSION_ITEM"],
                timeout=timeout_per_item,
                predicate=lambda m, s=seq: int(m.seq) == s,
            )
            if not mi:
                # Retry item request
                self.__master.mav.mission_request_int_send(
                    self.__master.target_system, self.__master.target_component, seq,
                )
                mi = self._wait_for(["MISSION_ITEM_INT", "MISSION_ITEM"], timeout=timeout_per_item, predicate=lambda m, s=seq: int(m.seq) == s)
                if not mi:
                    raise TimeoutError(f"Timed out waiting for mission item {seq}")

            # ... [Parsing logic same as before] ...
            if mi.get_type() == "MISSION_ITEM_INT":
                lat_i, lon_i = int(mi.x), int(mi.y)
            else:
                lat_i, lon_i = int(mi.x * 1e7), int(mi.y * 1e7)

            item = MissionRawItem(
                seq=int(mi.seq), frame=int(mi.frame), command=int(mi.command),
                current=int(mi.current), autocontinue=int(mi.autocontinue),
                param1=float(mi.param1), param2=float(mi.param2),
                param3=float(mi.param3), param4=float(mi.param4),
                x=lat_i, y=lon_i, z=float(mi.z), mission_type=0,
            )
            self.mission_items[seq] = item

        self.__master.mav.mission_ack_send(
            self.__master.target_system,
            self.__master.target_component,
            mavutil.mavlink.MAV_MISSION_ACCEPTED,
        )
        return list(self.mission_items)

    # --- Fix 3: Robust Upload with Delay and Async Wrapper ---
    async def upload_mission(
        self,
        mission_items: list[MissionRawItem],
        timeout_per_item: float = 2.0,
        clear_first: bool = True,
    ) -> bool:
        return await asyncio.to_thread(self._upload_mission_sync, mission_items, timeout_per_item, clear_first)

    def _upload_mission_sync(self, mission_items: list[MissionRawItem], timeout_per_item: float, clear_first: bool):
        n = len(mission_items)
        logger.debug(f"Uploading mission: {n} items")

        if clear_first:
            self.__master.mav.mission_clear_all_send(
                self.__master.target_system, self.__master.target_component
            )
            # CRITICAL: Wait for flight controller to process clear before sending count
            # This fixes "Error: Mission upload busy, ignoring MISSION_COUNT"
            ack = self._wait_for("MISSION_ACK", timeout=2.0)
            if ack and ack.type != mavutil.mavlink.MAV_MISSION_ACCEPTED:
                logger.warning(f"Clear mission returned type {ack.type}")
            time.sleep(0.5) 

        self.__master.mav.mission_count_send(
            self.__master.target_system, self.__master.target_component, n
        )

        sent = 0
        # Loop to handle MISSION_REQUEST logic
        # Note: MAVLink protocol expects requests for sequence numbers
        while sent < n:
            # Wait for request for specific sequence
            req = self._wait_for(
                ["MISSION_REQUEST_INT", "MISSION_REQUEST"],
                timeout=timeout_per_item,
            )
            
            if not req:
                # Retry sending count if we get stuck at start
                if sent == 0:
                    logger.warning("Retrying MISSION_COUNT...")
                    self.__master.mav.mission_count_send(
                        self.__master.target_system, self.__master.target_component, n
                    )
                    req = self._wait_for(["MISSION_REQUEST_INT", "MISSION_REQUEST"], timeout=2.0)
                
                if not req:
                    raise TimeoutError(f"Timed out waiting for MISSION_REQUEST ({sent}/{n})")

            seq = int(req.seq)
            
            # Sanity check
            if seq >= n:
                logger.warning(f"FC requested invalid seq {seq}, max {n-1}")
                continue
                
            wp = mission_items[seq]

            # Send the item
            self.__master.mav.mission_item_int_send(
                self.__master.target_system,
                self.__master.target_component,
                int(wp.seq),
                int(wp.frame),
                int(wp.command),
                int(wp.current),
                int(wp.autocontinue),
                float(wp.param1),
                float(wp.param2),
                float(wp.param3),
                float(wp.param4),
                int(wp.x),
                int(wp.y),
                float(wp.z),
            )
            # We don't increment 'sent' strictly here because the FC controls the flow.
            # We assume success if we eventually get an ACK after the loop.
            sent = seq + 1 # Approximate progress

        ack = self._wait_for("MISSION_ACK", timeout=max(3.0, timeout_per_item))
        if not ack:
            raise TimeoutError("Timed out waiting for MISSION_ACK after upload")
        if int(ack.type) != mavutil.mavlink.MAV_MISSION_ACCEPTED:
            raise RuntimeError(f"Mission rejected: ACK type={int(ack.type)}")
        
        logger.success("Mission uploaded successfully (PyMavlink)")
        return True
    
    # ... [Keep _set_mode and _set_start_mission_seq helper] ...
    def _set_mode(self, custom_mode: int):
        self.__master.mav.set_mode_send(
            self.__master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            custom_mode,
        )
        self.__master.set_mode_apm(custom_mode)
    

# -------------------------- Parameters --------------------------
@dataclass
class ParamRequestState:
    name: str
    value: float | str | int | None = None
    request_dt: int = 0

    def __init__(self, name: str):
        self.name = name
        self.value = None
        self.request_dt = int(time.time())


class Parameters:
    def __init__(self, connection: mavutil.mavfile, wait_for: Callable):
        self.__master = connection
        self._wait_for = wait_for
        self.requested_params: dict[str, ParamRequestState] = {}

    @staticmethod
    def _param_id_str(msg) -> str:
        raw = msg.param_id
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("ascii", errors="ignore").rstrip("\x00")
        return str(raw).rstrip("\x00")

    def _request_param(self, name: str):
        self.__master.mav.param_request_read_send(
            self.__master.target_system,
            self.__master.target_component,
            name.encode("ascii"),
            -1,
        )
        self.requested_params[name] = ParamRequestState(name=name)

    def _on_param_value(self, msg):
        pname = self._param_id_str(msg)
        if pname not in self.requested_params:
            self.requested_params[pname] = ParamRequestState(name=pname)
        self.requested_params[pname].value = msg.param_value
        return msg.param_value

    def get_parameter(self, name: str, timeout: float = 2.0):
        self._request_param(name)
        msg = self._wait_for(
            "PARAM_VALUE",
            timeout=timeout,
            predicate=lambda m, n=name: self._param_id_str(m) == n,
        )
        if not msg:
            return None
        return self._on_param_value(msg)


    def set_parameter(self, name: str, value, timeout: float = 2.0):
        self.__master.mav.param_set_send(
            self.__master.target_system,
            self.__master.target_component,
            name.encode("ascii"),
            float(value),
            mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
        )
        msg = self._wait_for(
            "PARAM_VALUE",
            timeout=timeout,
            predicate=lambda m, n=name: self._param_id_str(m) == n,
        )
        if not msg:
            raise TimeoutError(f"Timeout waiting for PARAM_VALUE echo of {name}")
        return self._on_param_value(msg)

            
            
    def read_RC_param(self):
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                return self.get_parameter("COM_RC_IN_MODE")
            case "APM":
                return self.get_parameter("RC_OPTIONS")
            case _:
                logger.warning("read_RC_param not supported for this FC_type.")
                return None
            
            
    def set_RC_param(self, enable: bool):
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                val = 0 if enable else 4
                self.set_parameter("COM_RC_IN_MODE", val)
            case "APM":
                val = 0 if enable else 1
                self.set_parameter("RC_OPTIONS", val)
            case _:
                logger.warning("set_RC_status not supported for this FC_type.")
                return
            
    
    def set_RC_param_int(self, value: int):
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                self.set_parameter("COM_RC_IN_MODE", value)
            case "APM":
                self.set_parameter("RC_OPTIONS", value)
            case _:
                logger.warning("set_RC_param_int not supported for this FC_type.")
                return
            
            
    def set_RC_lost_failsafe_action_param(self, action: int = 0):
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                self.set_parameter("NAV_RCL_ACT", action)
            case "APM":
                self.set_parameter("FS_THR_ENABLE", action)
            case _:
                logger.warning("set_RC_lost_failsafe_action_param not supported for this FC_type.")
                return
            
    
    def get_RC_lost_failsafe_action_param(self) -> int | None:
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                return self.get_parameter("NAV_RCL_ACT")
            case "APM":
                return self.get_parameter("FS_THR_ENABLE")
            case _:
                logger.warning("get_RC_lost_failsafe_action_param not supported for this FC_type.")
                return None
            
            
    def set_RC_lost_failsafe_timeout_param(self, value: int = 5):
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                self.set_parameter("COM_RC_LOSS_T", value)
            case "APM":
                self.set_parameter("FS_THR_TIMEOUT", value)
            case _:
                logger.warning("set_RC_lost_failsafe_timeout_param not supported for this FC_type.")
                return
            
            
    def get_RC_lost_failsafe_timeout_param(self) -> int | None:
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                return self.get_parameter("COM_RC_LOSS_T")
            case "APM":
                return self.get_parameter("FS_THR_TIMEOUT")
            case _:
                logger.warning("get_RC_lost_failsafe_timeout_param not supported for this FC_type.")
                return None
            
    
    def get_RC_failsafe_activation_time_param(self):
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                return self.get_parameter("COM_FAIL_ACT_T")
            case "APM":
                return self.get_parameter("FS_THR_ACT")
            case _:
                logger.warning("get_RC_failsafe_activation_time_param not supported for this FC_type.")
                return None
            
    
    def set_RC_failsafe_activation_time_param(self, value: int = 5):
        config = ConfigManager.get_config()
        match config.drone.properties.FC_type.upper():
            case "PX4":
                self.set_parameter("COM_FAIL_ACT_T", value)
            case "APM":
                self.set_parameter("FS_THR_ACT", value)
            case _:
                logger.warning("set_RC_failsafe_activation_time_param not supported for this FC_type.")
                return
