from loguru import logger
from mavlink_rest.exceptions import NetworkException
from pythonping import ping
from ping3 import ping as ping3



def ping_host(host, raise_exception: bool = False, timeout: int|float = 4):
    response = ping(host, count=1, timeout=timeout).rtt_avg  # Returns response time in seconds or None if unreachable
    if response in [None, False, 0, timeout] and raise_exception:
        msg = f"connection to {host} failed, response: {response}"
        logger.error(msg)
        raise NetworkException(msg) 
    if response in [None, False, 0] or int(response) >= int(timeout): 
        logger.error(f"{timeout=} reached, ping from {host=} was unsuccessful")
    else: logger.debug(f"Ping successful: {response * 1000:.2f} ms from {host=}")
    return response


def is_network_enabled(host: str, raise_exception: bool = False, timeout: int = 4):
    response = ping_host(host, raise_exception, timeout)
    isConnected = False if response in [None, False, 0] or int(response) >= int(timeout) else True
    logger.debug(f"network status {isConnected=}")
    return isConnected


def ping3_host(host, raise_exception: bool = False, timeout: int|float = 60):
    response = ping3(host, timeout)  # Returns response time in seconds or None if unreachable
    if response is None and raise_exception:
        msg = f"connection to {host} failed"
        logger.error(msg)
        raise NetworkException(msg) 
    if response == None: 
        logger.error(f"{timeout=} reached, ping from {host=} was unsuccessful")
    else: logger.debug(f"Ping successful: {response * 1000:.2f} ms from {host=}")
    return response


def is_network_enabled_ping3(host: str, raise_exception: bool = False, timeout: int = 60):
    response = ping3_host(host, raise_exception, timeout)
    isConnected = False if response in [None, False] else True
    logger.debug(f"network status {isConnected=}")
    return isConnected