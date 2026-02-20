from typing import Any
from httpx import AsyncClient, Response, AsyncHTTPTransport, Timeout
from httpx import RequestError
from loguru import logger



from typing import Any
from httpx import Client, Response, HTTPTransport, Timeout, TimeoutException
from httpx import RequestError
from loguru import logger


class Request:
    def __init__(self, retries: int = 5, 
                 timeout: int = 10, BaseUrl: str = "", 
                 validate_response: bool = True, **kwargs) -> None:
        self._transport = HTTPTransport(retries=retries)
        self._timeout = Timeout(timeout, connect=timeout, read=timeout)
        self._BaseUrl = BaseUrl
        self._validate_response = validate_response
        

    def __call__(self, retries: int = 5, 
                 timeout: int = 10, BaseUrl: str = "") -> Any:
        self._transport = HTTPTransport(retries=retries)
        self._timeout = Timeout(timeout, connect=timeout, read=timeout)
        self._BaseUrl = BaseUrl
        

    def _get(self, _endpoint: str = '', **kwargs) -> Response:
        with Client(transport=self._transport, 
                    timeout=self._timeout, http2=True,
                    base_url=self._BaseUrl) as client:
            res = client.get(_endpoint, **kwargs)
            if self._validate_response:
                self.validate_response(res)
        return res
    

    def _post(self, _endpoint: str = '', **kwargs) -> Response:
        with Client(transport=self._transport,
                    timeout=self._timeout, http2=True,
                    base_url=self._BaseUrl) as client:
            res = client.post(_endpoint, **kwargs)
            if self._validate_response:
                self.validate_response(res)
        return res


    def validate_response(self, response: Response) -> None:
        """Validate the response, raising an error if it fails."""
        try:
            response.raise_for_status()
        except RequestError as e:
            logger.error(f"Request failed: {e}")
            raise


class AsyncRequest:
    def __init__(self, retries: int = 5, 
                 timeout: int = 10, BaseUrl: str = "", 
                 validate_response: bool = True, **kwargs) -> None:
        self._transport = AsyncHTTPTransport(retries=retries)
        self._timeout = Timeout(timeout, connect=timeout, read=timeout)
        self._BaseUrl = BaseUrl
        self._validate_response = validate_response
        
        
    def __call__(self, retries: int = 5, 
                 timeout: int = 10, BaseUrl: str = "") -> Any:
        self._transport = AsyncHTTPTransport(retries=retries)
        self._timeout = Timeout(timeout, connect=timeout, read=timeout)
        self._BaseUrl = BaseUrl
        
    

    async def _aget(self, _endpoint:str = '', **kwargs) -> Response:
        try:
            async with AsyncClient(transport=self._transport, 
                                timeout=self._timeout, http2=True,
                                base_url=self._BaseUrl) as client:
                res = await client.get(_endpoint, **kwargs)
                if self._validate_response: self.validate_response(res)
        except (TimeoutException, TimeoutError) as e:
            logger.error(f"timeout occurred during HTTP request: {e.__class__}: {str(e)}")
            raise e
        except Exception as e:
            logger.error(f"unknown error occurred during HTTP request: {e.__class__}: {str(e)}")
            raise e
        return res
    
    
    async def _apost(self, _endpoint: str = '', **kwargs) -> Response:
        try:
            async with AsyncClient(transport=self._transport,
                                timeout=self._timeout, http2=True,
                                base_url=self._BaseUrl) as client:
                res = await client.post(_endpoint, **kwargs)
                if self._validate_response: self.validate_response(res)
        except (TimeoutException, TimeoutError) as e:
            logger.error(f"timeout occurred during HTTP request: {e.__class__}: {str(e)}")
            raise e
        except Exception as e:
            logger.error(f"unknown error occurred during HTTP request: {e.__class__}: {str(e)}")
            raise e
        return res
    
    
    @staticmethod
    def validate_response(response: Response, ok_status_code: int = 200) -> None:
        status_code = response.status_code
        if status_code not in range(ok_status_code, ok_status_code + 100):
            response = response.text
            msg = f"something wrong with response status code, {status_code=}, {response=}"
            logger.error(msg)
            raise StatusCodeError(msg, status_code)
            
            
class StatusCodeError(Exception):
    def __init__(self, msg: str, code: int) -> None:
        self.msg = msg
        self.code = code
        
        
    def __str__(self) -> str:
        return self.msg
            
        