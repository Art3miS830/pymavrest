from loguru import logger
import asyncio 
from typing import Literal, TypeVar, Callable, Awaitable, Any
from functools import wraps



Function = TypeVar('Function', bound=Callable[..., Awaitable[Any] | Any])

def exception_handler(msg_format: str = "exception_thrown from function: {func}: {e}", raise_error: bool = False,
                      return_value_if_fail: bool|None = False):
    def handle_exception(func: Function) -> Function:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                result = asyncio.run(func(*args, **kwargs)) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs) 
            except Exception as e:
                logger.error(msg_format.format(e=e, func=func.__name__))
                if raise_error: raise e
                return return_value_if_fail
            return result
                
        return wrapper  # Return async wrapper for async functions
    return handle_exception
    
    