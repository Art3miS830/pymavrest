from mavlink_rest.Logging import initialize_logger
import uvicorn
from mavlink_rest.config import ConfigManager
import typer, asyncio
import time
import gc
from loguru import logger
from typing import List, Literal
from mavlink_rest.utils.utils import run_with_timeout
from mavlink_rest.utils.utils import kill_mavsdk_servers, log_resource_usage
import uvloop

    
    
async def run_main_tasks(server_conf: uvicorn.Config, backend: Literal["mavsdk", "pymavlink"] = "mavsdk",
                         verbose: bool = True):
    from mavlink_rest.repository import telemetry
    from mavlink_rest.repository.healthcheck import push_health_status

    if backend == "mavsdk":
        kill_mavsdk_servers()

    async def telemetry_supervisor():
        config = ConfigManager.get_config()
        connection_uri = config.drone.properties.URI
        while True:
            try:
                await run_with_timeout(
                    telemetry.connect(connection_uri, backend=backend),
                                       raise_exception=True, timeout=10)
                await telemetry.subscribe_telemetry()
                logger.warning("telemetry subscription stopped; trying to reconnect")
            except asyncio.CancelledError:
                logger.info("telemetry supervisor cancelled")
                raise
            except Exception as e:
                logger.error(f"failed to connect to drone: {e.__class__}: {str(e)}")
                await asyncio.sleep(1)
                continue
            finally:
                telemetry.is_connected = False
            
    async def monitor_app(interval: int = 5):
        while True:
            try:
                data = await telemetry.get_latest_telemetry()
                print("\n**************************************************************")
                logger.debug(f"telemetry data: {data.model_dump()}")
            except Exception as e:
                logger.debug(f"telemetry is not ready yet: {e.__class__.__name__}: {e}")

            task_data = {
                _task.get_name(): {"done": _task.done(), "cancelled": _task.cancelled()}
                for _task in asyncio.all_tasks()
            }
            print("\n****************************************************************")
            logger.debug(f"current running bg tasks: {task_data}")
            print("****************************************************************")
            log_resource_usage()
            await asyncio.sleep(interval)

    telemetry.telemetry_backend = backend
    server = uvicorn.Server(server_conf)
    tasks: list[asyncio.Task] = [
        asyncio.create_task(telemetry_supervisor(), name="TelemetrySupervisor"),
        asyncio.create_task(server.serve(), name="Server"),
        asyncio.create_task(push_health_status(), name="HealthCheck"),
    ]
    if verbose:
        tasks.append(asyncio.create_task(monitor_app(), name="AppMonitor"))
    
    
    try:
        await tasks[1]
    except asyncio.CancelledError:
        logger.info("Main tasks cancelled")
    except SystemExit as e:
        logger.warning(f"SystemExit triggered: {e}")
        raise e
    except Exception as e:
        logger.exception(f"Exception occurred in main tasks: {e}")
    finally:
        # Graceful cleanup
        logger.info("Cleaning up tasks...")
        current_task = asyncio.current_task()
        for task in tasks:
            if task is not current_task and not task.done():
                task.cancel()
        
        # return_exceptions=True is key to preventing crashes during cleanup
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Give MAVSDK/gRPC threads time to release handles before loop closes
        await asyncio.sleep(0.5)
    





def CLI(config_paths: List[str] = typer.Option(["config.json"], "-c", "--config-path", help="path of config files"),
        port: int|None = None,
        initialize_logs: bool = True,
        verbose: bool = True,
        backend: Literal["mavsdk", "pymavlink"] = typer.Option("pymavlink", "-b", "--backend", help="backend to use, choose either 'mavsdk' or 'pymavlink'")):
    from mavlink_rest.config import ConfigManager
    
    config = ConfigManager.read_multiple_config_files(*config_paths)
    config = ConfigManager.get_config()
        
    if config is None:
        logger.error("config is not initialized from server, using values from config file")
        return

    from mavlink_rest.repository import telemetry
    if initialize_logs: initialize_logger(__file__)
    from mavlink_rest.routes.rest.base_routes import app
    rest_conf = config.rest_api
    _port, _host = rest_conf.port, rest_conf.host
    is_ssl = rest_conf.as_https
    ssl_key_dir, ssl_cert_dir = (rest_conf.ssl_keyfile_dir, rest_conf.ssl_certfile_dir) if is_ssl else (None, None)
    uvicorn_config = uvicorn.Config(app, port=port or _port, host=_host,
                     ssl_keyfile=ssl_key_dir, ssl_certfile=ssl_cert_dir)
    
    while True:
        try:
            telemetry.init()
            
            asyncio.run(run_main_tasks(uvicorn_config, backend=backend,
                                       verbose=verbose),  loop_factory=uvloop.new_event_loop)
            break
            
        except SystemExit as e:
            logger.critical(f"exiting app with error_code={e.code} ...")
            kill_mavsdk_servers()
            raise e
        except Exception as e:
            logger.exception(f"exception occurred in main loop: {e.__class__}: {str(e)}")
            time.sleep(1)
            continue
        finally:
            # FIX 3: Force Garbage Collection
            # Destroys lingering MAVSDK objects from the previous loop iteration
            gc.collect()

if __name__ == "__main__":
    log_resource_usage() 
    typer.run(CLI)
