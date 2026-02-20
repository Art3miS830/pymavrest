from pydantic import BaseModel, Field
from typing import Literal, Any
import datetime as dt


    


class FlightInfo(BaseModel):
    flight_uid: int|None = None
    hardware_uid: int|None = None
    duration_since_arming_ms: int|None = None
    duration_since_takeoff_ms: int|None = None
    time_boot_ms: int|None = None
    check_interval: int|None = None
    last_update: dt.datetime|None = None
    
    
    def __setattr__(self, name, value):
        # Call BaseModel’s __setattr__ first
        super().__setattr__(name, value)
        # Only update if we’re not setting `last_update` itself
        if name != "last_update":
            super().__setattr__("last_update", dt.datetime.now(dt.UTC))
        
    
    

# used to send drone status to server
class FlightDetails(BaseModel):
    flight_mode: str|None = None
    custom_mode: int|None = None
    drone_type: str|None = None
    autopilot_type: Literal["PX4", "APM", "GENERIC", "INVALID"]|None = None
    flight_info: FlightInfo
    vtol_state: Literal["MC", "FW", "TO_MC", "TO_FW"]|None = None
    FW_loiter_radius: float|None = None
    system_status: Literal["UNKNOWN", "BOOT", "CALIBRATING", "STANDBY", "ACTIVE", "CRITICAL", "EMERGENCY", "POWEROFF", "TERMINATION"]|None = None
    home_lat: float|None = None
    home_lon: float|None = None
    home_alt: float|None = None
    battery_remain: int|None = None
    battery_amper: float|None = None
    Flight_GPS_lat: float|None = None
    Flight_GPS_lon: float|None = None
    Flight_GPS_alt: float|None = None
    Flight_GPS_alt_abs: float|None = None
    Device_GPS_lat: float|None = None
    Device_GPS_lon: float|None = None
    Device_GPS_alt: float|None = None
    Device_GPS_alt_abs: float|None = None
    signal: float|None = None
    air_speed: float|None = None
    speed: float|None = None
    vx: float|None = None
    vy: float|None = None
    vz: float|None = None
    roll_deg: float|None = None
    pitch_deg: float|None = None
    yaw_deg: float|None = None
    rc_connected: bool|None = None
    default_gps: Literal["internal", "external"]
    is_armed: bool|None = None
    is_flying: bool|None = None
    is_drone_connected: bool|None = None
    is_global_position_ok: bool|None = None
    is_local_position_ok: bool|None = None
    is_armable: bool|None = None
    is_rc_ok: bool|None = None
    is_ekf_ok: bool|None = None
    is_battery_ok: bool|None = None
    is_mavsdk_server_healthy: bool = True
    mission_summary: dict[str, Any]|None = None
    last_update: dt.datetime|None = None
    
    
    

class MissionStatus(BaseModel):
    mission_id: int|None = None
    status: Literal["UNKNOWN", "NO_MISSION", "NOT_STARTED", "ACTIVE", "PAUSED", "COMPLETE"]|None = None # equivalent to https://mavlink.io/en/messages/common.html#MISSION_STATE
    must_rtl_at_end: bool|None = None
    current_progress: int|None = None
    total_progress: int|None = None  
    mission_plan: list[dict[str, Any]] = Field(default_factory=list)
    last_update: dt.datetime|None = None
    
    
    def __setattr__(self, name, value):
        # Call BaseModel’s __setattr__ first
        super().__setattr__(name, value)
        # Only update if we’re not setting `last_update` itself
        if name != "last_update":
            super().__setattr__("last_update", dt.datetime.now(dt.UTC))
            



class Telemetry(BaseModel):
    flight_mode: str|None = None
    custom_mode: int|None = None
    drone_type: str|None = None
    flight_info: FlightInfo = Field(default_factory=FlightInfo)
    autopilot_type: Literal["PX4", "APM", "GENERIC", "INVALID"]|None = None
    vtol_state: Literal["MC", "FW", "TO_MC", "TO_FW"]|None = None
    FW_loiter_radius: float|None = None 
    battery_remain: int|None = None
    battery_amper: float|None = None
    signal: float|None = None
    air_speed: float|None = None
    speed: float|None = None
    vx: float|None = None
    vy: float|None = None
    vz: float|None = None
    is_armed: bool|None = None
    is_global_position_ok: bool|None = None
    is_local_position_ok: bool|None = None
    is_armable: bool|None = None
    is_rc_ok: bool|None = None
    is_ekf_ok: bool|None = None
    is_battery_ok: bool|None = None
    is_drone_connected: bool|None = None
    rc_connected: bool|None = None
    Flight_GPS_lat: float|None = None
    Flight_GPS_lon: float|None = None
    Flight_GPS_alt: float|None = None
    Flight_GPS_alt_abs: float|None = None
    Device_GPS_lat: float|None = None
    Device_GPS_lon: float|None = None
    Device_GPS_alt: float|None = None
    Device_GPS_alt_abs: float|None = None
    default_gps: Literal["internal", "external"]
    is_flying: bool|None = None
    roll_deg: float|None = None
    pitch_deg: float|None = None
    yaw_deg: float|None = None
    home_lat: float|None = None
    home_lon: float|None = None
    home_alt: float|None = None
    mission: MissionStatus = Field(default_factory=MissionStatus)
    is_mavsdk_server_healthy: bool = True
    system_status: Literal["UNKNOWN", "BOOT", "CALIBRATING", "STANDBY", "ACTIVE", "CRITICAL", "EMERGENCY", "POWEROFF", "TERMINATION"]|None = None # equivalent to https://mavlink.io/en/messages/common.html#MAV_STATE 
    last_update: dt.datetime|None = None
    
    
    def __setattr__(self, name, value):
        # Call BaseModel’s __setattr__ first
        super().__setattr__(name, value)
        # Only update if we’re not setting `last_update` itself
        if name != "last_update":
            super().__setattr__("last_update", dt.datetime.now(dt.UTC))
    
    
