from mavlink_rest.utils._request import AsyncRequest, Response
from mavlink_rest.config import ConfigManager
from mavlink_rest.repository.ground_station.schema import GPSData
from loguru import logger


def get_client():
    config = ConfigManager.get_config()
    req_conf = config.requests
    client = AsyncRequest(req_conf.retries, req_conf.timeout, 
                          config.ground_station.rest_api.base_url )
    return client
    

async def get_gps_data(raise_error: bool = True)-> GPSData:
    config = ConfigManager.get_config()
    client = get_client()
    route = config.ground_station.rest_api.routes.gps
    res: Response = await client._aget(route)
    if not res or res.text == '': 
        msg = f"no data gathered from {route}"
        logger.error(msg)
        if raise_error: raise ValueError(msg)
    gps_data = GPSData.model_validate_json(res.text)
    return gps_data    
