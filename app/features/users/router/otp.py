from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
from fastapi import APIRouter, Depends, status, Response, HTTPException
from app.core.error_handler import AppErrorHandler
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
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Request an OTP to be sent to the user's email for verification."""
    try:
        timer = Timer()
        timer.start()
        await user_service.request_email_otp(schema.email)
        await system_logger.metric('request_email_otp', timer.stop(), source='otp.request_email_otp')
        return BaseAPIResponse[None](
            detail="Verification code sent to your email.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('request_email_otp failed', source='otp.request_email_otp', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'request_email_otp error: {str(e)}', source='otp.request_email_otp')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while requesting email OTP."
        )


@router.post("/verify-email", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def verify_email(
    schema: VerifyEmailOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Verify the user's email using the provided OTP."""
    try:
        timer = Timer()
        timer.start()
        user = await user_service.verify_email_otp(schema.email, schema.code)
        await system_logger.metric('verify_email', timer.stop(), source='otp.verify_email')
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(user),
            detail="Email verified successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('verify_email failed', source='otp.verify_email', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'verify_email error: {str(e)}', source='otp.verify_email')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during email verification."
        )


@router.post("/request-phone-otp", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def request_phone_otp(
    schema: RequestPhoneOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Request an OTP to be sent to the user's phone number for verification."""
    try:
        timer = Timer()
        timer.start()
        await user_service.request_phone_otp(schema.phone_number)
        await system_logger.metric('request_phone_otp', timer.stop(), source='otp.request_phone_otp')
        return BaseAPIResponse[None](
            detail="Verification code sent to your phone number.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('request_phone_otp failed', source='otp.request_phone_otp', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'request_phone_otp error: {str(e)}', source='otp.request_phone_otp')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while requesting phone OTP."
        )


@router.post("/verify-phone", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def verify_phone(
    schema: VerifyPhoneOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Verify the user's phone number using the provided OTP."""
    try:
        timer = Timer()
        timer.start()
        user = await user_service.verify_phone_otp(schema.phone_number, schema.code)
        await system_logger.metric('verify_phone', timer.stop(), source='otp.verify_phone')
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(user),
            detail="Phone number verified successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('verify_phone failed', source='otp.verify_phone', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'verify_phone error: {str(e)}', source='otp.verify_phone')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during phone verification."
        )


@router.post("/verify-otp", response_model=BaseAPIResponse[bool], status_code=status.HTTP_200_OK)
async def verify_otp(
    schema: VerifyOTP,
    response: Response,
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Verify an OTP code for a target (email or phone) without registering or updating user verification status."""
    try:
        timer = Timer()
        timer.start()
        await user_service.verify_otp(schema.target, schema.channel, schema.code)
        await system_logger.metric('verify_otp', timer.stop(), source='otp.verify_otp')
        return BaseAPIResponse[bool](
            data=True,
            detail="OTP verified successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('verify_otp failed', source='otp.verify_otp', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'verify_otp error: {str(e)}', source='otp.verify_otp')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during OTP verification."
        )

