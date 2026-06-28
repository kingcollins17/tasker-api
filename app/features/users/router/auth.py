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

router = APIRouter()


@router.post("/register", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_201_CREATED)
async def register(
    schema: UserRegister,
    response: Response,
    user_service: UserService = Depends(get_user_service)
):
    """Register a new user (customer or provider) and automatically create their profile."""
    try:
        user = await user_service.register_user(schema)
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(user),
            detail="User registered successfully.",
            status_code=status.HTTP_201_CREATED
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        print(e)
        traceback.print_exc()
        raise HTTPException(
            detail="An unexpected error occurred during registration.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    user_service: UserService = Depends(get_user_service)
):
    """Authenticate a user and return a JWT access token."""
    try:
        schema = UserLogin(email=form_data.username,
                           password=form_data.password)
        login_data = await user_service.login_user(schema)
        return LoginResponse(
            access_token=login_data["access_token"],
            token_type=login_data["token_type"],
            user=UserResponse.model_validate(login_data["user"])
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during login."
        )
