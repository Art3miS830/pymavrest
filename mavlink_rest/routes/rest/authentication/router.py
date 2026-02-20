from fastapi import status, HTTPException, Depends, APIRouter
from typing import Annotated, Literal
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from mavlink_rest.config import User
from mavlink_rest.routes.rest.authentication.schema import Token, TokenData
from mavlink_rest.config import ConfigManager, AppConfig
import datetime as dt
import jwt


def get_config():
    return ConfigManager.get_config()
Config_Dep = Annotated[AppConfig, Depends(get_config)]

auth_prefix = "/auth"


def _get_global_prefix() -> str:
    config = ConfigManager.get_config(raise_ifNone=False)
    return config.rest_api.global_prefix if config is not None else "/api/v1"


global_prefix = _get_global_prefix()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{global_prefix}{auth_prefix}/token")
router = APIRouter()
    

############################## repository to handle jwt tokens from user Authorization header
def verify_jwt_token(token: Annotated[str, Depends(oauth2_scheme)], config: Config_Dep) -> TokenData:
    """Verify and decode the JWT token."""
    SECRET_KEY = config.auth.jwt_secret
    ALGORITHM = config.auth.jwt_algorithm
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return TokenData(username=payload["sub"], permission=payload["permission"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unknown error when handling token")


def get_users_usernameAsKey(config: Config_Dep)-> dict[str, User]:
    users = {_user.username: _user for _user in config.auth.users} # users with username as key
    return users
    

def encode_to_jwt_token(username: str, permission: Literal[0, 10, 11], 
                        expire_date: dt.datetime, secret: str,
                        algorithm: str):
    payload = {"sub": username, "permission": permission, "exp": expire_date}
    token = jwt.encode(payload, secret, algorithm)
    return token


def get_current_user(token_data: Annotated[TokenData, Depends(verify_jwt_token) ], 
                     users: Annotated[dict[str, User], Depends(get_users_usernameAsKey) ]) -> User:
    user = users.get(token_data.username)
    if not user: 
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token")
    if not user.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"user is not active")
    return user


def admin_permission_checker(current_user: Annotated[User, Depends(get_current_user)]) -> bool:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, 
                            detail=f"Access denied")
    return True
        

def require_permission(required_permission: Literal[0, 10, 11]):
    """Check if the user has the required permission using the decoded payload."""
    def permission_checker(current_user: Annotated[User, Depends(get_current_user)] ):
        if current_user.permission < required_permission:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, 
                                detail=f"Access denied")
        return current_user  # Return payload so it can be reused in the route
    def disabled_auth(): return None
    config = ConfigManager.get_config()
    return permission_checker if config.auth.enabled else disabled_auth

#############################################


# this route gives us new token by username and password 
@router.post(f'{global_prefix}{auth_prefix}/token', response_model=Token)
def login_for_token(form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
                    users: Annotated[dict[str, User], Depends(get_users_usernameAsKey) ]):
    config = ConfigManager.get_config()
    user = users.get(form_data.username)
    if not user or user.password != form_data.password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="username or password is invalid",
                            headers={"WWW-Authenticate": "Bearer"})
    expire_mins = dt.timedelta(minutes=config.auth.jwt_token_expire_minutes)
    expire_date = dt.datetime.now(dt.UTC) + expire_mins
    token = encode_to_jwt_token(user.username, user.permission,
                                expire_date, config.auth.jwt_secret,
                                config.auth.jwt_algorithm)
    return Token(access_token=token)
    
      
    
