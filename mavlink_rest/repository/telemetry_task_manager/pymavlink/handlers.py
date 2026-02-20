import math
from typing import Literal
from pymavlink import mavutil
from loguru import logger

class TelemetryHandlersMixin:
    """
    Mixin class containing all MAVLink message handlers to keep the main
    FlightTelemetry class small enough for Pyarmor obfuscation.
    """

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
        from mavlink_rest.utils.utils import restart_app  # Imported locally to avoid circular imports if any

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