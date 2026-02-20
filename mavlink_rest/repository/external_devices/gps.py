#!/usr/bin/python3

import asyncio
import serial_asyncio
import re
import time
from typing import Tuple, List, Optional, AsyncIterator
from dataclasses import dataclass
from loguru import logger

@dataclass
class GPSPosition:
    """Data structure for GPS coordinates."""
    lat: float
    lon: float
    alt_abs: float
    alt_rel: float

class AsyncGPSModule:
    """
    Class to handle GPS module communication and data parsing asynchronously.
    """
    
    def __init__(self, port: str = "/dev/ttyUSB2", baudrate: int = 115200, timeout: float = 0.5):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def __aenter__(self):
        """Async context manager entry: open serial connection."""
        try:
            self.reader, self.writer = await serial_asyncio.open_serial_connection(
                url=self.port, 
                baudrate=self.baudrate
            )
            logger.info(f"Async serial connection opened on {self.port}.")
            return self
        except Exception as e:
            logger.error(f"Failed to open serial port: {e}")
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit: close serial connection."""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception as e:
                logger.warning(f"Error closing writer: {e}")
            logger.info("Serial connection closed.")

    async def send_at(self, command: str, back: str, timeout: float) -> bool:
        """
        Send AT command and await response asynchronously.
        Optimized to return early if response is received before timeout.
        """
        if not self.writer or not self.reader:
            logger.error("Serial connection not open.")
            return False

        try:
            self.writer.write((command + '\r\n').encode())
            await self.writer.drain()

            start_time = time.time()
            buffer = ""
            
            while (time.time() - start_time) < timeout:
                try:
                    remaining_time = timeout - (time.time() - start_time)
                    if remaining_time <= 0: break
                    
                    line_bytes = await asyncio.wait_for(self.reader.readline(), timeout=remaining_time)
                    line = line_bytes.decode('ascii', errors='ignore').strip()
                    buffer += line + "\n"
                    
                    if back in buffer:
                        logger.debug(f"{command} succeeded. Response: {buffer.strip()}")
                        self.last_response = buffer
                        return True
                        
                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    logger.error(f"Error reading serial: {e}")
                    break

            logger.error(f"{command} failed or timed out. Response: {buffer}")
            return False

        except Exception as e:
            logger.error(f"Exception in send_at: {e}")
            return False

    def parse_gps_data(self, gps_data: str) -> Optional[Tuple[float, float, float]]:
        """Parse GPS data from +CGPSINFO response."""
        target_line = ""
        for line in gps_data.split('\n'):
            if "+CGPSINFO:" in line:
                target_line = line
                break
        
        if not target_line:
            target_line = gps_data 

        pattern = r'\+CGPSINFO:\s*(\d+\.\d+),([NS]),(\d+\.\d+),([EW]),\d+,\d+\.\d,(\d+\.\d),.*'
        match = re.search(pattern, target_line)

        if not match or ',,,,,,' in target_line:
            logger.debug("No GPS fix or invalid data.") 
            return None

        try:
            lat_str, lat_dir, lon_str, lon_dir, alt_str = match.groups()

            lat_deg = float(lat_str[:2])
            lat_min = float(lat_str[2:])
            lat = lat_deg + (lat_min / 60.0)
            if lat_dir == 'S': lat = -lat

            lon_deg = float(lon_str[:3])
            lon_min = float(lon_str[3:])
            lon = lon_deg + (lon_min / 60.0)
            if lon_dir == 'W': lon = -lon

            alt = float(alt_str)

            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= alt <= 10000):
                logger.warning(f"Invalid coordinates: lat={lat}, lon={lon}, alt={alt}")
                return None

            return lat, lon, alt
        except Exception as e:
            logger.error(f"Error parsing GPS data: {e}")
            return None

    async def _enable_gps(self) -> bool:
        """Internal helper to reset and enable GPS."""
        # if not await self.send_at('AT+CGPS=0', 'OK', 1.0):
        #     return False
        if not await self.send_at('AT+CGPS=1', 'OK', 1.0):
            return False
        await asyncio.sleep(1.0) # Stabilization
        return True

    async def _disable_gps(self):
        """Internal helper to disable GPS."""
        await self.send_at('AT+CGPS=0', 'OK', 1.0)

    async def stream_gps_data(self, interval: float = 1.0) -> AsyncIterator[GPSPosition]:
        """
        Async generator that yields GPSPosition objects continuously.
        
        Usage:
            async for pos in gps.stream_gps_data(interval=2.0):
                print(pos.lat, pos.lon, pos.alt_rel, pos.alt_abs)
        """
        logger.info("Starting GPS stream...")
        if not await self._enable_gps():
            logger.error("Failed to enable GPS for streaming")
            

        initial_alt = None

        try:
            while True:
                if await self.send_at('AT+CGPSINFO', '+CGPSINFO: ', 1.0):
                    gps_data = self.last_response
                    if ',,,,,,' not in gps_data:
                        coords = self.parse_gps_data(gps_data)
                        if coords:
                            lat, lon, alt = coords
                            
                            # Set reference altitude on first valid fix
                            if initial_alt is None:
                                initial_alt = alt
                            
                            rel_alt = alt - initial_alt
                            yield GPSPosition(lat=lat, lon=lon, alt_abs=alt, alt_rel=rel_alt)
                    else:
                        logger.debug("GPS waiting for fix...")
                
                await asyncio.sleep(interval)
        finally:
            # This block executes when the loop is broken (e.g. via break) or an error occurs
            await self._disable_gps()
            logger.info("GPS stream ended.")

    async def get_gps_position(self, max_attempts: int = 20, sample_count: int = 1, 
                             session_timeout: float = 60.0, interval: float = 1.0) -> List[Tuple[float, float, float]]:
        """
        Activate GPS and collect a fixed number of GPS coordinates.
        Returns list of tuples (lat, lon, alt) for backward compatibility.
        """
        coordinates = []
        start_time = time.time()
        attempts = 0

        logger.info("Starting GPS session (Async)...")
        
        if not await self._enable_gps():
            return coordinates

        try:
            while len(coordinates) < sample_count and attempts < max_attempts:
                if time.time() - start_time > session_timeout:
                    logger.warning("GPS session timed out.")
                    break

                if await self.send_at('AT+CGPSINFO', '+CGPSINFO: ', 1.0):
                    gps_data = self.last_response
                    if ',,,,,,' not in gps_data:
                        coords = self.parse_gps_data(gps_data)
                        if coords:
                            lat, lon, alt = coords
                            coordinates.append(coords)
                            logger.info(f"Sample {len(coordinates)}: ({lat:.6f}, {lon:.6f}, {alt:.1f})")
                
                await asyncio.sleep(interval)
                attempts += 1
        finally:
            await self._disable_gps()
            logger.info(f"GPS session ended. Collected {len(coordinates)} samples.")
            
        return coordinates


async def get_gps_data_serial(max_attempts=20,
                              session_timeout=60.0, 
                              port: str = "/dev/ttyUSB2",
                              baudrate: int = 115200,
                              interval: float = 1.0):
    """Wrapper to run the session using the Async Context Manager"""
    try:
        async with AsyncGPSModule(port, baudrate) as gps:
            coordinates = await gps.get_gps_position(
                max_attempts=max_attempts, 
                sample_count=1,
                session_timeout=session_timeout,
                interval=interval
            )
            
            if coordinates:
                last_cord = coordinates[-1]
                logger.info(f"Last sample: Lat: {last_cord[0]:.6f}°, Lon: {last_cord[1]:.6f}°, Alt: {last_cord[2]:.1f}m")
                return last_cord
            else:
                logger.error("No valid gps data fetched")
                return None
    except Exception as e:
        logger.error(f"Error in serial wrapper: {e}")
        return None


async def get_gps_data(max_attempts=20,
                       session_timeout=60.0,
                       raise_exception: bool = False,
                       interval: float = 1.0) -> Optional[Tuple[float, float, float]]:
    """
    Main entry point. Reads config and calls the serial handler.
    """
    try:
        from mavlink_rest.config import ConfigManager
        config = ConfigManager.get_config()
        gps_data = config.external_devices.get("gps")
    except ImportError:
        from types import SimpleNamespace
        gps_data = SimpleNamespace(type="serial", COM="/dev/ttyUSB2", baud=115200)
        logger.warning("ConfigManager not found, using defaults.")

    if gps_data is None:
        msg = "no external device defined as gps"
        logger.error(msg)
        if raise_exception: raise ValueError(msg)
        return None

    match gps_data.type:
        case "serial":
            try:
                data = await get_gps_data_serial(max_attempts, session_timeout,
                                                 gps_data.COM, gps_data.baud,
                                                 interval=interval)
            except Exception as e:
                logger.exception(f"Exception thrown: {e.__class__}: {str(e)}")
                if raise_exception: raise e
                return None
            
            if data is None: 
                msg = "No valid gps data fetched"
                logger.error(msg)
                if raise_exception: raise ValueError(msg)
                return None
            return data
        case _: 
            msg = f"config external_devices.gps.type is not valid"
            logger.error(msg)
            if raise_exception: raise ValueError(msg)

# --- Test Harness ---
async def main_test():    
    from mavlink_rest.config import ConfigManager
    config = ConfigManager.get_config()
    logger.info("\n--- Testing Async Streaming ---")
    try:
        async with AsyncGPSModule(port=config.external_devices.get("gps").COM, baudrate=config.external_devices.get("gps").baud) as gps:
            # This loop will run until we break out of it
            async for pos in gps.stream_gps_data(interval=0.5):
                print(f"Streamed Update: Lat={pos.lat}, Lon={pos.lon}, AbsAlt={pos.alt_abs}m, RelAlt={pos.alt_rel:.1f}m")
    except Exception as e:
        logger.exception(f"Stream test error: {e}")

if __name__ == "__main__":
    from mavlink_rest.config import ConfigManager
    config = ConfigManager.read_multiple_config_files("config.json", "auth_config.py")
    try:
        asyncio.run(main_test())
    except KeyboardInterrupt:
        logger.info("Stopped by user")