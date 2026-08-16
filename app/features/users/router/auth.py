from sqlalchemy.util.typing import Literal
from app.core.error_handler import AppErrorHandler
import traceback
from fastapi import APIRouter, Depends, status, Response, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from app.core.api_response import BaseAPIResponse
from app.features.users.schemas import (
    UserRegister,
    UserResponse,
    UserLogin,
    LoginResponse,
)
from app.features.users.services import UserService, get_user_service
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.utils.timer import Timer

router = APIRouter()


@router.post(
    "/register",
    response_model=BaseAPIResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register(
    schema: UserRegister,
    response: Response,
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Register a new user (customer or provider) and automatically create their profile."""
    try:
        timer = Timer()
        timer.start()
        user = await user_service.register_user(schema)

        await system_logger.metric(
            f"User registration: {schema.email}", timer.stop(), source="auth.register"
        )
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(user),
            detail="User registered successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        try:
            await system_logger.warn(
                f"User registration failed: {schema.email}",
                source="auth.register",
                metadata={"detail": str(e.detail)},
            )
        except Exception:
            pass
        raise e
    except Exception as e:
        try:
            await system_logger.error(
                f"Unexpected error during registration: {str(e)}",
                source="auth.register",
                metadata={"email": schema.email},
            )
        except Exception:
            pass
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        AppErrorHandler.handleError(e)
        raise HTTPException(
            detail="An unexpected error occurred during registration.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service),
    user_type: Literal["customer", "provider"] = "customer",
):
    """Authenticate a user and return a JWT access token."""
    try:
        schema = UserLogin(
            email=form_data.username,
            password=form_data.password,
            user_type=user_type,
        )
        timer = Timer()
        timer.start()
        login_data = await user_service.login_user(schema)

        await system_logger.metric(
            f"User login: {form_data.username}", timer.stop(), source="auth.login"
        )
        return LoginResponse(
            access_token=login_data["access_token"],
            token_type=login_data["token_type"],
            user=UserResponse.model_validate(login_data["user"]),
        )
    except HTTPException as e:
        try:
            await system_logger.warn(
                f"Failed login attempt: {form_data.username}",
                source="auth.login",
                metadata={"detail": str(e.detail)},
            )
        except Exception:
            pass
        raise e
    except Exception as e:
        try:
            await system_logger.error(
                f"Unexpected error during login: {str(e)}",
                source="auth.login",
                metadata={"username": form_data.username},
            )
        except Exception:
            pass
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during login.",
        )
