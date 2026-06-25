from fastapi import APIRouter, Depends, status, Response, HTTPException
from app.core.api_response import BaseAPIResponse
from app.features.users.schemas import (
    UserResponse,
    RequestEmailOTP,
    VerifyEmailOTP,
    RequestPhoneOTP,
    VerifyPhoneOTP,
    VerifyOTP,
)
from app.features.users.services import UserService, get_user_service

router = APIRouter()


@router.post("/request-email-otp", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def request_email_otp(
    schema: RequestEmailOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service)
):
    """Request an OTP to be sent to the user's email for verification."""
    try:
        await user_service.request_email_otp(schema.email)
        return BaseAPIResponse[None](
            detail="Verification code sent to your email.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[None](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[None](
            detail="An unexpected error occurred while requesting email OTP.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.post("/verify-email", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def verify_email(
    schema: VerifyEmailOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service)
):
    """Verify the user's email using the provided OTP."""
    try:
        user = await user_service.verify_email_otp(schema.email, schema.code)
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(user),
            detail="Email verified successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[UserResponse](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[UserResponse](
            detail="An unexpected error occurred during email verification.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.post("/request-phone-otp", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def request_phone_otp(
    schema: RequestPhoneOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service)
):
    """Request an OTP to be sent to the user's phone number for verification."""
    try:
        await user_service.request_phone_otp(schema.phone_number)
        return BaseAPIResponse[None](
            detail="Verification code sent to your phone number.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[None](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[None](
            detail="An unexpected error occurred while requesting phone OTP.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.post("/verify-phone", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def verify_phone(
    schema: VerifyPhoneOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service)
):
    """Verify the user's phone number using the provided OTP."""
    try:
        user = await user_service.verify_phone_otp(schema.phone_number, schema.code)
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(user),
            detail="Phone number verified successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[UserResponse](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[UserResponse](
            detail="An unexpected error occurred during phone verification.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.post("/verify-otp", response_model=BaseAPIResponse[bool], status_code=status.HTTP_200_OK)
async def verify_otp(
    schema: VerifyOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service)
):
    """Verify an OTP code for a target (email or phone) without registering or updating user verification status."""
    try:
        await user_service.verify_otp(schema.target, schema.channel, schema.code)
        return BaseAPIResponse[bool](
            data=True,
            detail="OTP verified successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[bool](
            data=False,
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[bool](
            data=False,
            detail="An unexpected error occurred during OTP verification.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

