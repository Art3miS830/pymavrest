from typing import Coroutine
from loguru import logger 
import asyncio
import uuid
from tenacity import AsyncRetrying, stop_after_attempt, retry_if_exception_type
from typing import Callable, Awaitable, Tuple, Type
import psutil, os, shlex, sys

import asyncio
from urllib.parse import urlencode, urljoin
from functools import wraps
from typing import TypeVar, Callable, Any, Awaitable, Union, Literal
from inspect import iscoroutinefunction
import time, aiofiles
import hashlib



Function = TypeVar('Function', bound=Union[Callable[..., Any], Callable[..., Awaitable[Any]]])


async def run_with_timeout(coro: Coroutine, timeout: int|None = None,
                           raise_exception: bool = False):
    from mavlink_rest.config import ConfigManager
    timeout = timeout or ConfigManager.get_config().requests.timeout
    tname = getattr(coro, '__name__', 'unknown_task')
    try: 
        return await asyncio.wait_for(coro, timeout)
    except asyncio.TimeoutError as e:
        logger.warning(f"task: {tname} cancelled due to timeout")
        if raise_exception: raise e 
        return None
    except Exception as e:
        logger.error(f"task: {tname} cancelled due to error: {e.__class__}: {str(e)}")
        if raise_exception: raise e
        return None
    
    
async def run_with_retry(
    coro: Callable[[], Awaitable],
    retries: int = 3,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,)
):
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(retries),
        retry=retry_if_exception_type(exceptions),
        reraise=True,
    ):
        with attempt:
            return await coro()

    

def get_mac_address():
    mac = uuid.getnode()  # Gets the hardware address as a 48-bit positive integer
    mac_address = ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
    return mac_address


async def get_machine_id() -> str:
    """
    Returns the Linux machine-id as a string.

    Raises:
        RuntimeError: if machine-id cannot be read or is empty.
    """
    path="/etc/machine-id"
    try:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            machine_id = (await f.read()).strip()

        if not machine_id:
            raise RuntimeError("machine-id is empty")

        return machine_id

    except FileNotFoundError:
        raise RuntimeError(f"{path} not found (not a systemd-based Linux?)")
    except PermissionError:
        raise RuntimeError(f"Permission denied reading {path}")



def log_resource_usage():
    process = psutil.Process(os.getpid())
    cpu_pct = process.cpu_percent() # 0 for non-blocking
    mem_mb = process.memory_info().rss / (1024**2)  # in MB
    # if 0 in [cpu_pct, mem_pct]: return
    print("\n******************************************************************")
    logger.info(f"Resource usage -> CPU_pct/CPU_total_pct: {cpu_pct}%/{psutil.cpu_percent()}%, RAM_MB: {mem_mb}, CPU_num: {process.cpu_num()}")    
    print("******************************************************************\n")
    
        

def get_mavsdk_server_pids()-> list[int]:
    mavsdk_pids = []
    for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            if "mavsdk_server" in proc.info["name"] or \
            any("mavsdk_server" in part for part in (proc.info["cmdline"] or [])):
                pid = proc.info['pid']
                proc = psutil.Process(pid)
                cmd_ls = [shlex.quote(arg) for arg in proc.cmdline()]
                if "mavsdk_server" in ' '.join(cmd_ls):
                    mavsdk_pids.append(pid)
                else: continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return mavsdk_pids


def kill_mavsdk_servers():
    pids = get_mavsdk_server_pids()
    for pid in pids:
        proc = psutil.Process(pid)
        logger.debug(f"Killing mavsdk_server (pid={proc.pid})...")
        proc.terminate()
        try: 
            proc.wait(timeout=2)
        except psutil.TimeoutExpired:
            proc.kill()
        
        
        
def get_process_init_cmd(proc: psutil.Process):
    cmdline_list = proc.cmdline()
    cmd = [shlex.quote(arg) for arg in cmdline_list]
    return cmd


def flight_uid_convertor(raw_uid: str) -> int:
    # Your raw ID from MAVSDK
    # 1. Clean the string: Remove the null byte (\x00) and any whitespace
    clean_hex_str = raw_uid.strip('\x00').strip()

    # 2. Convert to Integer (Base 16)
    # We use base 16 because the UID is represented as Hex characters (0-9, A-F)
    try:
        uid_int = int(clean_hex_str, 16)
        print(f"Integer ID: {uid_int}")
        return uid_int
    except ValueError:
        print("Error: The UID contains non-hex characters.")


def get_sha256(text: str) -> str:
    """
    Returns the SHA-256 hash of the input string as a hexadecimal string.
    """
    # 1. Encode the string to bytes (standard is utf-8)
    # 2. Hash the bytes
    # 3. Return the hexadecimal digest
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def restart_app(backend: Literal["mavsdk", "pymavlink"]|None = None):
    """Restarts the current program, with file objects and descriptors open"""
    logger.info("Restarting...")
    python = sys.executable
    cmd = [python] + sys.argv
    if backend is None:
        from mavlink_rest.repository import telemetry
        backend = telemetry.telemetry_backend
    cmd.append(f"--backend={backend}")
    logger.debug(f"{cmd = }")
    os.execv(python, cmd)
    
    
    
def log_exec_time(func: Function) -> Function:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        t1 = time.perf_counter()
        result = await func(*args, **kwargs)  # Await the async function
        t2 = time.perf_counter()
        exec_time = t2 - t1
        msg = f"Execution time for {func.__name__}: {exec_time:.4f} seconds"
        logger.debug(msg)
        return result

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        t1 = time.perf_counter()
        result = func(*args, **kwargs)  # Call the sync function
        t2 = time.perf_counter()
        exec_time = t2 - t1
        msg = f"Execution time for {func.__name__}: {exec_time:.4f} seconds "
        logger.debug(msg)
        return result

    if asyncio.iscoroutinefunction(func):
        return async_wrapper  # Return async wrapper for async functions
    else:
        return sync_wrapper  # Return sync wrapper for sync functions
    


