class NetworkException(Exception):
    def __init__(self, msg):
        self.msg = msg
        
    def __str__(self):
        return self.msg
    

class UnknownMsgType(Exception):
    def __init__(self, msg: str = "unknown msg type={type}", _type: str = ''):
        self.msg = msg
        self.type = _type


    def __str__(self):
        return self.msg.format(type=self.type)
    
    
class UnknownFlightMode(Exception):
    def __init__(self, msg: str = "unknown flight mode={mode}", _mode: str = ''):
        self.msg = msg
        self.mode = _mode


    def __str__(self):
        return self.msg.format(mode=self.mode)
    
    
class DockerComposeError(Exception):
    def __init__(self, status_code: int,
                 msg: str = "error accrued when running docker-compose, exit_code={status_code}"):
        self.msg = msg
        self.status_code = status_code
        
        
    def __str__(self) -> str:
        return self.msg.format(status_code=self.status_code)
    
    
class GeneralDockerError(Exception):
    def __init__(self, status_code: int,
                 msg: str = "error accrued when running docker {cmd} command, exit_code={status_code}",
                 cmd: str = ""):
        self.msg = msg
        self.status_code = status_code
        self.cmd = cmd
        
        
    def __str__(self) -> str:
        _cmd = f"'{self.cmd}'" if self.cmd != '' else ''
        return self.msg.format(status_code=self.status_code, cmd=_cmd)
        
        

class NoAckReceived(Exception):
    def __init__(self, msg: str = "no ACK packet received"):
        self.msg = msg
    
    def __str__(self):
        return self.msg
    