from mavlink_rest.config import ConfigManager, AppConfig, User
from mavlink_rest.routes.rest.authentication.router import get_current_user, require_permission, admin_permission_checker
from fastapi import Depends
from typing import Annotated

# config dependency
def get_config():
    return ConfigManager.get_config()
Config_Dep = Annotated[AppConfig, Depends(get_config)]


# security dependencies
## permissions
Read_Permission_Dep = Depends( require_permission(10) )
Write_Permission_Dep = Depends( require_permission(11) )
Admin_Permission_Dep = Depends(admin_permission_checker)
## current user
CurrentUser_Dep = Annotated[User, Depends(get_current_user)]
