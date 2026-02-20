from fastapi import FastAPI, status, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from mavlink_rest.routes.rest.config.router import router as config_router
from mavlink_rest.routes.rest.messages.router import router as msg_router
from mavlink_rest.routes.rest.authentication.router import router as auth_router
from mavlink_rest.routes.rest.commands.router import router as command_router 
from mavlink_rest.routes.rest.flight_logs.router import router as flight_log_router
from mavlink_rest.routes.rest.base_schema import GeneralResponse
from mavlink_rest.config import ConfigManager

import time, asyncio
from loguru import logger

 
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     pass
#     # config = ConfigManager.get_config()
#     # docker_prune()
#     # docker_rm_all()
#     # await merge_dockerCompose(config.services, config.docker_compose.dir)
#     # docker_compose_up(dir=config.docker_compose.dir)
#     # yield
#     # docker_prune()
#     # docker_rm_all()


app = FastAPI(title="mavlink_rest",
              description="a app for managing mavlink services",
            #   lifespan=lifespan
              )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compress any response bigger than 10 KB
app.add_middleware(GZipMiddleware, minimum_size=10000)


def _get_global_prefix() -> str:
    config = ConfigManager.get_config(raise_ifNone=False)
    return config.rest_api.global_prefix if config is not None else "/api/v1"


global_prefix = _get_global_prefix()
    
    
app.include_router(config_router, 
                   prefix=global_prefix + "/config",
                   tags=["config"])


app.include_router(auth_router,
                   # prefix='defined in authentication.router module', 
                   tags=["authentication"])


app.include_router(msg_router, 
                   prefix=global_prefix + "/messages",
                   tags=["messages"])


app.include_router(command_router,
                  prefix=global_prefix + "/commands",
                  tags=["commands"])


app.include_router(flight_log_router,
                  prefix=global_prefix + "/logs",
                  tags=["logs"])


@app.middleware("http")
async def global_timeout(request: Request, call_next):
    timeout = ConfigManager.get_config().rest_api.global_timeout
    try: 
        start_time = time.time()
        return await asyncio.wait_for(call_next(request), timeout=timeout)
    except asyncio.TimeoutError:
        process_time = time.time() - start_time
        
        logger.error(f"timeout {timeout}sec reached, route:{request.url}")
        return JSONResponse({"detail":"server timeout reached", "process_time": round(process_time, 2)},
                            status_code=status.HTTP_504_GATEWAY_TIMEOUT)


# other routes
@app.get("/ping", response_model=GeneralResponse, tags=["health"])
async def ping():
    return GeneralResponse(code=status.HTTP_200_OK, msg="ping success")
