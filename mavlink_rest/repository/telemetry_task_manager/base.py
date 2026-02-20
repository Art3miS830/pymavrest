from .mavsdk.repo import FlightTelemetry as MavsdkTelemetry, MsgNames as _MavsdkTasks
from .pymavlink.repo import FlightTelemetry as PymavlinkTelemetry, MsgNames as _PymavlinkTasks
from typing import Literal, Optional
from .schema import Telemetry
from enum import Enum
from loguru import logger
import asyncio
from mavsdk import System
from mavsdk.mavlink_direct import MavlinkDirect, MavlinkMessage
from pymavlink.dialects.v20 import common as mavlink2
from mavlink_rest.config import ConfigManager
from mavlink_rest.utils.utils import log_exec_time


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
    
    
def _map_mavsdk_MsgName_to_mavlink_msgs(msg_name: MsgNames)-> list[_PymavlinkTasks]:
    match msg_name:
        case MsgNames.FLIGHT_MODE:
            return [_PymavlinkTasks.HEARTBEAT]
        case MsgNames.POSITION:
            return [_PymavlinkTasks.GLOBAL_POSITION_INT]
        case MsgNames.BATTERY:
            return [_PymavlinkTasks.BATTERY_STATUS, _PymavlinkTasks.SYS_STATUS]
        case MsgNames.VELOCITY:
            return [_PymavlinkTasks.VFR_HUD, _PymavlinkTasks.ODOMETRY]
        case MsgNames.RC_STATUS:
            return [_PymavlinkTasks.RC_CHANNELS]
        case MsgNames.IS_FLYING:
            return [_PymavlinkTasks.EXTENDED_SYS_STATE]
        case MsgNames.IS_ARMED:
            return [_PymavlinkTasks.HEARTBEAT]
        case MsgNames.HEALTH:
            return [_PymavlinkTasks.HEARTBEAT]
        case MsgNames.IS_CONNECTED:
            return [_PymavlinkTasks.HEARTBEAT]
        case MsgNames.EULER_ANGLES:
            return [_PymavlinkTasks.ATTITUDE]
        case MsgNames.HOME:
            return [_PymavlinkTasks.HOME_POSITION]
        case MsgNames.EXTERNAL_GPS:
            return []
        case MsgNames.MISSION:
            return [_PymavlinkTasks.MISSION_CURRENT]
        case _:
            raise ValueError(f"Unsupported MsgName: {msg_name}")
        


class FlightTelemetry:
    def __init__(self, verbose: bool = True,
                 global_delay: float = 0.01):
        self.mavsdk = MavsdkTelemetry(verbose, global_delay)
        self.pymavlink = PymavlinkTelemetry(verbose, global_delay)
        self.telemetry_backend: None | Literal["mavsdk", "pymavlink"] = None  # Track which backend is in use
        self.is_connected: bool = False  # Connection status
        self.server_token: Optional[str] = None
        # RC status
        self.RC_disabled: bool = False  # RC status
        # Defaults in case reading initial values fails
        self.init_RC_status: int = 1
        self.init_RC_lost_failsafe_action: int = 2
        self.init_RC_lost_failsafe_timeout: int = 5
        self.init_RC_lost_failsafe_activation_time: int = 5
        
    
    
    def init(self):
        """
        Initialize the telemetry data using the config data.

        This function reads the config data and populates the telemetry data
        with the relevant information. It also sets up the restriction zone
        action parameters if the service is enabled.
        It should be called after instantiating the FlightTelemetry class and reading the config.

        :param:
            None

        :return:
            None
        """
        config = ConfigManager.get_config()
        self.mavsdk.init()
        self.pymavlink.init()
    
    
    async def connect(self, connection_string: str, backend: Literal["mavsdk", "pymavlink"] = "mavsdk", **kwargs):
            self.telemetry_backend = backend
            match self.telemetry_backend:
                case "pymavlink":
                    # pymavlink connection is blocking; keep the event loop free for HTTP serving.
                    await asyncio.to_thread(self.pymavlink.connect, connection_string, **kwargs)
                case "mavsdk":
                    await self.mavsdk.connect(connection_string, **kwargs)
                case _:
                    raise ValueError("Invalid backend specified. Choose either 'pymavlink' or 'mavsdk'.")
            self.is_connected = True
        
        
    async def subscribe_telemetry(self, *args, **kwargs):
        match self.telemetry_backend:
            case "pymavlink":
                return await self.pymavlink.subscribe_telemetry(*args, **kwargs)
            case "mavsdk":
                return await self.mavsdk.subscribe_telemetry(*args, **kwargs)
            case _:
                raise ValueError("Invalid backend specified. Choose either 'pymavlink' or 'mavsdk'.")
        
        
    async def get_latest_telemetry(self) -> Telemetry:
        match self.telemetry_backend:
            case "mavsdk":
                return await self.mavsdk.get_latest_telemetry()
            case "pymavlink":
                return await self.pymavlink.get_latest_telemetry()
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
            
    
    def get_default_gps_data(self, relative_alt: bool = True) -> tuple[float, float, float]:
        match self.telemetry_backend:
            case "mavsdk":
                return self.mavsdk.get_default_gps_data(relative_alt)
            case "pymavlink":
                return self.pymavlink.get_default_gps_data(relative_alt)
            case _:
                raise ValueError("Invalid backend specified. Choose either 'pymavlink' or 'mavsdk'.")
            
            
    @property    
    def drone(self):
        match self.telemetry_backend:
            case "mavsdk":
                return self.mavsdk.drone
            case "pymavlink":
                return self.pymavlink
            case _:
                raise NotImplementedError("Invalid backend specified. Choose either 'pymavlink' or 'mavsdk'.")
            
    
    @property
    def telemetry_data(self) -> Telemetry:
        match self.telemetry_backend:
            case "mavsdk":
                return self.mavsdk.telemetry_data
            case "pymavlink":
                return self.pymavlink.telemetry_data
            case _:
                logger.error("Telemetry backend not set. Please call start() first.")
                raise NotImplementedError
                
            
            
    @telemetry_data.setter
    def telemetry_data(self, value: Telemetry):
        match self.telemetry_backend:
            case "mavsdk":
                self.mavsdk.telemetry_data = value
            case "pymavlink":
                self.pymavlink.telemetry_data = value
            case _:
                logger.error("Telemetry backend not set. Please call start() first.")
                raise NotImplementedError
                
            
    
    # set COM_RC_IN_MODE
    async def set_RC_param_int(self, value: int):
        """
        0	RC transmitter only (standard RC receiver) 
        \n1	Joystick only / “No RC checks” — enables joystick/gamepad control over telemetry instead of RC. 
        \n2	RC and Joystick with fallback — both input options enabled; fallback logic applies. 
        \n3	RC or Joystick — whichever is present/active (“keep first”) 
        \n4	Stick input disabled — disables all manual stick/joystick/RC input. Useful for fully-autonomous / external-control only setups (e.g. offboard control).
        """
        match self.telemetry_backend:
            case "mavsdk":
                return await self.mavsdk.drone.param.set_param_int("COM_RC_IN_MODE", int(value))
            case "pymavlink":
                return round(float(self.pymavlink.parameters.set_RC_param_int(value)))
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")


    #get COM_RC_IN_MODE
    async def read_RC_param(self) -> int:
        match self.telemetry_backend:
            case "mavsdk":
                return int(await self.mavsdk.drone.param.get_param_int("COM_RC_IN_MODE"))
            case "pymavlink":
                return int(self.pymavlink.parameters.read_RC_param())
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
            
    
    # set NAV_RCL_ACT
    async def set_RC_lost_failsafe_action_param(self, action: int = 0):
        """
        0	Hold — stop, hover, or maintain attitude; remain where you are
        \n1	Return / RTL — fly back to home position
        \n2	Land — descend straight down and disarm
        \n3	Mission — continue executing the active mission
        \n4	Offboard — stay in/offboard mode if offboard commands continue (used in robotics setups)
        """
        match self.telemetry_backend:
            case "mavsdk":
                return await self.mavsdk.drone.param.set_param_int("NAV_RCL_ACT", int(action))
            case "pymavlink":
                return self.pymavlink.parameters.set_RC_lost_failsafe_action_param(action)
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
    
    
    # get NAV_RCL_ACT
    async def get_RC_lost_failsafe_action_param(self) -> int:
        match self.telemetry_backend:
            case "mavsdk":
                return int(await self.mavsdk.drone.param.get_param_int("NAV_RCL_ACT"))
            case "pymavlink":
                return int(self.pymavlink.parameters.get_RC_lost_failsafe_action_param())
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
            
    # get COM_RC_LOSS_T
    async def get_RC_lost_failsafe_timeout_param(self):
        match self.telemetry_backend:
            case "mavsdk":
                return round(float(await self.mavsdk.drone.param.get_param_float("COM_RC_LOSS_T")))
            case "pymavlink":
                return round(float(self.pymavlink.parameters.get_RC_lost_failsafe_timeout_param()))
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
        
        
    # set COM_RC_LOSS_T
    async def set_RC_lost_failsafe_timeout_param(self, value: int = 5):
        match self.telemetry_backend:
            case "mavsdk":
                return await self.mavsdk.drone.param.set_param_float("COM_RC_LOSS_T", float(value))
            case "pymavlink":
                return self.pymavlink.parameters.set_RC_lost_failsafe_timeout_param(float(value)) 
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
            
    
    # get COM_FAIL_ACT_T
    async def get_RC_failsafe_activation_time_param(self):
        match self.telemetry_backend:
            case "mavsdk":
                return round(float(await self.mavsdk.drone.param.get_param_float("COM_FAIL_ACT_T")))
            case "pymavlink":
                return self.pymavlink.parameters.get_RC_failsafe_activation_time_param()
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
            
    
    # set COM_FAIL_ACT_T
    async def set_RC_failsafe_activation_time_param(self, value: int = 5):
        match self.telemetry_backend:
            case "mavsdk":
                return await self.mavsdk.drone.param.set_param_float("COM_FAIL_ACT_T", float(value))
            case "pymavlink":
                return self.pymavlink.parameters.set_RC_failsafe_activation_time_param(float(value))
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
            
    
    async def set_RC_failsafe_except_mode(self, value: int = 0):
        """
        0	No exceptions — RC loss failsafe always applies
        \n1	Exempt RC loss in Manual flight modes
        \n2	Exempt RC loss in Altitude mode
        \n4	Exempt RC loss in Position mode
        \n8	Exempt RC loss in all modes (generally used for joystick-only or fully-autonomous setups)
        """
        match self.telemetry_backend:
            case "mavsdk":
                return await self.mavsdk.drone.param.set_param_int("COM_RCL_EXCEPT", int(value))
            case "pymavlink":
                raise NotImplementedError # toDo: implement for pymavlink
                return self.pymavlink.parameters.set_RC_except_mode(int(value))
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
    
    
    # @log_exec_time    
    async def disable_RC_for_interval(self, interval: float = 5.0) -> None:
        """
        Temporarily disable RC input by tweaking RC-related parameters, then restore them.

        - If RC is already disabled (self.RC_disabled), this is a no-op.
        - Attempts to always restore the original parameters, even if something fails.
        - Handles cancellation cleanly (e.g. if the task is cancelled).
        """
        if interval <= 0:
            logger.warning(f"disable_RC_for_interval: non-positive interval={interval}, skipping.")
            return

        if getattr(self, "RC_disabled", False):
            logger.debug("disable_RC_for_interval: RC already disabled, skipping.")
            return

        # Mark as disabled *before* any await to avoid races
        self.RC_disabled = True
        print("\n**************************************************************")

        # Defaults in case reading initial values fails
        init_RC_status: int = 1
        init_RC_lost_failsafe_action: int = 2
        init_RC_lost_failsafe_timeout: int = 5
        init_RC_lost_failsafe_activation_time: int = 5

        # --- Read initial values (best effort) -----------------------------------
        try:
            init_RC_status = await self.read_RC_param()
        except Exception as e:
            logger.error(f"error reading RC param status: {e.__class__}: {str(e)}")

        try:
            init_RC_lost_failsafe_action = await self.get_RC_lost_failsafe_action_param()
        except Exception as e:
            logger.error(f"error reading RC failsafe_action_param: {e.__class__}: {str(e)}")

        try:
            init_RC_lost_failsafe_timeout = await self.get_RC_lost_failsafe_timeout_param()
        except Exception as e:
            logger.error(f"error reading RC failsafe_timeout_param: {e.__class__}: {str(e)}")

        try:
            init_RC_lost_failsafe_activation_time = await self.get_RC_failsafe_activation_time_param()
        except Exception as e:
            logger.error(f"error reading RC failsafe_activation_time_param: {e.__class__}: {str(e)}")

        logger.info(
            "initial RC params: "
            f"status={init_RC_status}, "
            f"failsafe_action={init_RC_lost_failsafe_action}, "
            f"failsafe_timeout={init_RC_lost_failsafe_timeout}, "
            f"failsafe_activation_time={init_RC_lost_failsafe_activation_time}"
        )

        # We track whether we *believe* RC was successfully disabled,
        # so that the logs make sense even if something fails mid-way.

        try:
            # --- Apply "disabled" configuration ---------------------------------
            try:
                # Disable RC input
                await self.set_RC_param_int(4)  # 4 == disabled (per your logic)
            except Exception as e:
                logger.error(f"error disabling RC: {e.__class__}: {str(e)}")
                return

            try:
                # Set RC lost failsafe action to HOLD (1)
                await self.set_RC_lost_failsafe_action_param(1)
            except Exception as e:
                logger.error(f"error disabling RC lost failsafe action: {e.__class__}: {str(e)}")
                return

            try:
                # Increase RC lost failsafe timeout to avoid unwanted triggers
                await self.set_RC_lost_failsafe_timeout_param(interval * 2)
            except Exception as e:
                logger.error(f"error disabling RC lost failsafe timeout: {e.__class__}: {str(e)}")
                return

            try:
                # Increase RC lost failsafe activation time as well
                await self.set_RC_failsafe_activation_time_param(interval * 2)
            except Exception as e:
                logger.error(f"error disabling RC lost failsafe activation time: {e.__class__}: {str(e)}")
                return

            logger.info(f"*** RC disabled for ~{interval:.1f} seconds.")

            # Log what the params look like now (best effort)
            rc_para, rc_lost_action, rc_lost_timeout, rc_lost_activation = await asyncio.gather(
                self.read_RC_param(),
                self.get_RC_lost_failsafe_action_param(),
                self.get_RC_lost_failsafe_timeout_param(),
                self.get_RC_failsafe_activation_time_param(),
                return_exceptions=True,
            )

            def _fmt(val: object) -> str:
                if isinstance(val, Exception):
                    return f"<error {val.__class__.__name__}: {val}>"
                return str(val)

            logger.debug(
                "*** RC disabled state: "
                f"status={_fmt(rc_para)}, "
                f"failsafe_action={_fmt(rc_lost_action)}, "
                f"failsafe_timeout={_fmt(rc_lost_timeout)}, "
                f"failsafe_activation_time={_fmt(rc_lost_activation)}"
            )

            # --- Keep RC disabled for the requested interval --------------------
            await asyncio.sleep(interval)
            

        except asyncio.CancelledError:
            # If called as a background task and cancelled, we still restore RC in finally
            logger.warning("disable_RC_for_interval task cancelled; restoring RC parameters early.")
            raise

        except Exception as e:
            logger.exception(
                f"Unexpected error while RC was disabled: {e.__class__}: {str(e)}. "
                "Attempting to restore RC parameters."
            )

        finally:
            # --- Restore original values (best effort) --------------------------
            # Even if we never managed to fully disable RC, restoring to the init
            # values is safe and idempotent.
            try:
                await self.set_RC_param_int(init_RC_status)
            except Exception as e:
                logger.error(f"error restoring RC param status: {e.__class__}: {str(e)}")
                
            await asyncio.sleep(interval)
            
            try:
                await self.set_RC_lost_failsafe_action_param(init_RC_lost_failsafe_action)
            except Exception as e:
                logger.error(f"error restoring RC lost failsafe action: {e.__class__}: {str(e)}")

            try:
                await self.set_RC_lost_failsafe_timeout_param(init_RC_lost_failsafe_timeout)
            except Exception as e:
                logger.error(f"error restoring RC lost failsafe timeout: {e.__class__}: {str(e)}")

            try:
                await self.set_RC_failsafe_activation_time_param(init_RC_lost_failsafe_activation_time)
            except Exception as e:
                logger.error(f"error restoring RC lost failsafe activation time: {e.__class__}: {str(e)}")

            # Mark as restored
            self.RC_disabled = False
            logger.info("*** RC enabled again (parameters restored).")

            # Final state log (best effort)
            try:
                rc_para, rc_lost_action, rc_lost_timeout, rc_lost_activation = await asyncio.gather(
                    self.read_RC_param(),
                    self.get_RC_lost_failsafe_action_param(),
                    self.get_RC_lost_failsafe_timeout_param(),
                    self.get_RC_failsafe_activation_time_param(),
                    return_exceptions=True,
                )
                def _fmt(val: object) -> str:
                    if isinstance(val, Exception):
                        return f"<error {val.__class__.__name__}: {val}>"
                    return str(val)

                logger.debug(
                    "*** RC restored state: "
                    f"status={_fmt(rc_para)}, "
                    f"failsafe_action={_fmt(rc_lost_action)}, "
                    f"failsafe_timeout={_fmt(rc_lost_timeout)}, "
                    f"failsafe_activation_time={_fmt(rc_lost_activation)}"
                )
            except Exception as e:
                logger.error(f"error reading RC params after restore: {e.__class__}: {str(e)}")

            print("**************************************************************\n")
            self.RC_disabled = False
    
    
    async def update_init_RC_params(self):
        # --- Read initial values (best effort) -----------------------------------
        try:
            self.init_RC_status = await self.read_RC_param()
        except Exception as e:
            logger.error(f"error reading RC param status: {e.__class__}: {str(e)}")

        try:
            self.init_RC_lost_failsafe_action = await self.get_RC_lost_failsafe_action_param()
        except Exception as e:
            logger.error(f"error reading RC failsafe_action_param: {e.__class__}: {str(e)}")

        try:
            self.init_RC_lost_failsafe_timeout = await self.get_RC_lost_failsafe_timeout_param()
        except Exception as e:
            logger.error(f"error reading RC failsafe_timeout_param: {e.__class__}: {str(e)}")

        try:
            self.init_RC_lost_failsafe_activation_time = await self.get_RC_failsafe_activation_time_param()
        except Exception as e:
            logger.error(f"error reading RC failsafe_activation_time_param: {e.__class__}: {str(e)}")

        logger.info(
            "initial RC params: "
            f"status={self.init_RC_status}, "
            f"failsafe_action={self.init_RC_lost_failsafe_action}, "
            f"failsafe_timeout={self.init_RC_lost_failsafe_timeout}, "
            f"failsafe_activation_time={self.init_RC_lost_failsafe_activation_time}"
        )
    
            
    async def disable_RC(self):
        # Disable RC
        # --- Apply "disabled" configuration ---------------------------------
        if self.RC_disabled:
            logger.debug("*** RC already disabled.")
            return
        
        try:
            # Disable RC input
            await self.set_RC_param_int(4)  # 4 == disabled (per your logic)
        except Exception as e:
            logger.error(f"error disabling RC: {e.__class__}: {str(e)}")
            self.RC_disabled = False

        try:
            # Set RC lost failsafe action to HOLD (1)
            await self.set_RC_lost_failsafe_action_param(1)
        except Exception as e:
            logger.error(f"error disabling RC lost failsafe action: {e.__class__}: {str(e)}")
            self.RC_disabled = False

        try:
            # Increase RC lost failsafe timeout to avoid unwanted triggers
            await self.set_RC_lost_failsafe_timeout_param(30)
        except Exception as e:
            logger.error(f"error disabling RC lost failsafe timeout: {e.__class__}: {str(e)}")
            self.RC_disabled = False

        try:
            # Increase RC lost failsafe activation time as well
            await self.set_RC_failsafe_activation_time_param(30)
        except Exception as e:
            logger.error(f"error disabling RC lost failsafe activation time: {e.__class__}: {str(e)}")
            self.RC_disabled = False

        self.RC_disabled = True
        logger.info("*** RC disabled.")
        
        
    async def enable_RC(self):
        # Enable RC
        # --- Apply "enabled" configuration ----------------------------------
        if not self.RC_disabled:
            logger.debug("*** RC already enabled.")
            return
        try:
            # Enable RC input
            await self.set_RC_param_int(self.init_RC_status) # RC + Joystick (fallback/dual input) 
        except Exception as e: 
            logger.error(f"error enabling RC: {e.__class__}: {str(e)}")
            
        try:
            # Set RC lost failsafe action to HOLD (1)
            await self.set_RC_lost_failsafe_action_param(self.init_RC_lost_failsafe_action)
        except Exception as e:
            logger.error(f"error enabling RC lost failsafe action: {e.__class__}: {str(e)}")

        try:
            # Set RC lost failsafe timeout to initial value
            await self.set_RC_lost_failsafe_timeout_param(self.init_RC_lost_failsafe_timeout)
        except Exception as e:
            logger.error(f"error enabling RC lost failsafe timeout: {e.__class__}: {str(e)}")

        try:
            # Set RC lost failsafe activation time to initial value
            await self.set_RC_failsafe_activation_time_param(self.init_RC_lost_failsafe_activation_time)
        except Exception as e:
            logger.error(f"error enabling RC lost failsafe activation time: {e.__class__}: {str(e)}")

        self.RC_disabled = False
        logger.info("*** RC enabled.")

        
    
    def _send_statustext_GS_mavsdk(self, text: str): # toDO : mavsdk not works properly on this
        pass 
        # if self.mavsdk.is_connected is False:
        #     return
        # text = "test text"
        # target_sys_id = 255
        # target_comp_id = 190
        # fields_json = {"message_id":253,"message_name":"STATUSTEXT","chunk_seq":0,"id":186,"severity":2,"text":"Failsafe activated      "}
        # msg = MavlinkMessage("STATUSTEXT", self.mavsdk.drone._sysid, 
        #                      self.mavsdk.drone._compid,
        #                      0, 0, 
        #                      fields_json=fields_json)
        # msg = MavlinkMessage(message_name="STATUSTEXT",system_id=1, component_id=1,
        #                      target_system_id=0, target_component_id=0,
        #                      fields_json=fields_json)
        # await self.mavsdk.drone.mavlink_direct.send_message(msg)
        
    
    
    def send_statustext_GS(self, text: str):
        match self.telemetry_backend:
            case "mavsdk":
                return self._send_statustext_GS_mavsdk(text)
            case "pymavlink":
                return self.pymavlink.action.send_statustext_GS(text)
            case _:
                raise RuntimeError("Telemetry backend not set. Please call start() first.")
        
        
            
    # def subscribe_task(self, task_name: MavsdkTasks|PymavlinkTasks):
    #     match self.telemetry_backend:
    #         case "mavsdk":
    #             return self.mavsdk.subscribe_task(task_name)
    #         case "pymavlink":
    #             return self.pymavlink.subscribe_task(task_name)
    #         case _:
    #             raise RuntimeError("Telemetry backend not set. Please call start() first.")            


telemetry = FlightTelemetry(verbose=False)
