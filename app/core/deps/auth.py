from typing import List, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials

from app.core.utils import security
from app.core.models.users import UserType, KYCStatus
from app.features.users.services import UserService, get_user_service
from app.features.users.schemas import UserResponse

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/users/login", auto_error=False)
http_bearer = HTTPBearer(auto_error=False)

class GetCurrentUser:
    """Dependency class to retrieve and validate the currently authenticated user from the JWT token."""

    def __init__(
        self,
        *,
        required_active: bool = True,
        required_email_verified: bool = False,
        required_phone_verified: bool = False,
        require_phone_present: bool = False,
        required_type: Optional[UserType] = None,
        allowed_kyc_statuses: Optional[List[KYCStatus]] = None,
        active_error_status: int = status.HTTP_403_FORBIDDEN,
        active_error_detail: str = "User account is inactive",
        email_verified_error_status: int = status.HTTP_403_FORBIDDEN,
        email_verified_error_detail: str = "Email address not verified",
        phone_verified_error_status: int = status.HTTP_403_FORBIDDEN,
        phone_verified_error_detail: str = "Phone number not verified",
        phone_present_error_status: int = status.HTTP_400_BAD_REQUEST,
        phone_present_error_detail: str = "Phone number is required",
        type_error_status: int = status.HTTP_403_FORBIDDEN,
        type_error_detail: str = "User type is not authorized",
        kyc_status_error_status: int = status.HTTP_403_FORBIDDEN,
        kyc_status_error_detail: str = "KYC status requirement not met",
    ):
        self.required_active = required_active
        self.required_email_verified = required_email_verified
        self.required_phone_verified = required_phone_verified
        self.require_phone_present = require_phone_present
        self.required_type = required_type
        self.allowed_kyc_statuses = allowed_kyc_statuses
        self.active_error_status = active_error_status
        self.active_error_detail = active_error_detail
        self.email_verified_error_status = email_verified_error_status
        self.email_verified_error_detail = email_verified_error_detail
        self.phone_verified_error_status = phone_verified_error_status
        self.phone_verified_error_detail = phone_verified_error_detail
        self.phone_present_error_status = phone_present_error_status
        self.phone_present_error_detail = phone_present_error_detail
        self.type_error_status = type_error_status
        self.type_error_detail = type_error_detail
        self.kyc_status_error_status = kyc_status_error_status
        self.kyc_status_error_detail = kyc_status_error_detail

    async def __call__(
        self,
        token_oauth: str | None = Depends(oauth2_scheme),
        token_bearer: HTTPAuthorizationCredentials | None = Depends(http_bearer),
        user_service: UserService = Depends(get_user_service),
    ) -> UserResponse:
        token = None
        if token_bearer:
            token = token_bearer.credentials
        elif token_oauth:
            token = token_oauth

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        payload = security.decode_access_token(token)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = payload.get("id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = await user_service.get_user(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if self.required_active and not user.is_active:
            raise HTTPException(
                status_code=self.active_error_status,
                detail=self.active_error_detail,
            )

        if self.required_email_verified and not user.email_verified:
            raise HTTPException(
                status_code=self.email_verified_error_status,
                detail=self.email_verified_error_detail,
            )

        if self.require_phone_present and not user.phone_number:
            raise HTTPException(
                status_code=self.phone_present_error_status,
                detail=self.phone_present_error_detail,
            )

        if self.required_phone_verified and not user.phone_verified:
            raise HTTPException(
                status_code=self.phone_verified_error_status,
                detail=self.phone_verified_error_detail,
            )

        if self.required_type is not None and user.type != self.required_type:
            raise HTTPException(
                status_code=self.type_error_status,
                detail=self.type_error_detail,
            )

        if self.allowed_kyc_statuses is not None:
            if not user.provider_profile or user.provider_profile.status not in self.allowed_kyc_statuses:
                raise HTTPException(
                    status_code=self.kyc_status_error_status,
                    detail=self.kyc_status_error_detail,
                )

        return UserResponse.model_validate(user)


get_current_user = GetCurrentUser()

