from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from app.core.api_response import BaseAPIResponse
from app.core.deps.auth import GetCurrentUser
from app.core.repository import Repository, GetRepository
from app.core.models.users import PaymentAccount, PaymentProvider
from app.core.services.payment import PaymentGateway, get_paystack_gateway
from app.core.schemas.users import (
    BankResponse,
    PaymentAccountCreate,
    PaymentAccountUpdate,
    PaymentAccountResponse,
    BankAccountVerificationResponse,
)
from app.features.users.schemas import UserResponse
from app.core.error_handler import AppErrorHandler

router = APIRouter(prefix="/payouts", tags=["payouts"])


@router.get("/banks", response_model=BaseAPIResponse[List[BankResponse]])
async def get_supported_banks():
    try:
        banks = [
            BankResponse(id="1", bank_code="044", name="Access Bank", logo_url=None),
            BankResponse(id="2", bank_code="011", name="First Bank", logo_url=None),
            BankResponse(
                id="3", bank_code="058", name="Guaranty Trust Bank", logo_url=None
            ),
            BankResponse(
                id="4", bank_code="033", name="United Bank for Africa", logo_url=None
            ),
            BankResponse(id="5", bank_code="032", name="Union Bank", logo_url=None),
            BankResponse(id="6", bank_code="057", name="Zenith Bank", logo_url=None),
        ]
        return BaseAPIResponse(
            message="Supported banks retrieved successfully", data=banks
        )
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve supported banks",
        )


@router.get("/account", response_model=BaseAPIResponse[PaymentAccountResponse])
async def get_payment_account(
    current_user: UserResponse = Depends(GetCurrentUser()),
    payment_repo: Repository[PaymentAccount] = Depends(GetRepository(PaymentAccount)),
):
    try:
        stmt = select(PaymentAccount).where(PaymentAccount.user_id == current_user.id)
        result = await payment_repo.execute(stmt)
        account = result.one_or_none()

        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment account not found",
            )

        return BaseAPIResponse(
            message="Payment account retrieved successfully", data=account
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve payment account",
        )


@router.post("/account", response_model=BaseAPIResponse[PaymentAccountResponse])
async def create_or_update_payment_account(
    account_data: PaymentAccountCreate,
    current_user: UserResponse = Depends(GetCurrentUser()),
    payment_repo: Repository[PaymentAccount] = Depends(GetRepository(PaymentAccount)),
    payment_gateway: PaymentGateway = Depends(get_paystack_gateway),
):
    try:
        if account_data.account_number and (len(account_data.account_number) != 11 or not account_data.account_number.isdigit()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account number must be exactly 11 digits",
            )

        stmt = select(PaymentAccount).where(PaymentAccount.user_id == current_user.id)
        result = await payment_repo.execute(stmt)
        account = result.one_or_none()

        gateway_resp = await payment_gateway.create_payment_account(
            bank_code=account_data.bank_code,
            account_number=account_data.account_number,
            account_name=account_data.account_name,
            email=current_user.email,
            user_id=current_user.id,
            phone_number=current_user.phone_number,
        )

        metadata = {
            "bank_code": account_data.bank_code,
            "bank_name": account_data.bank_name,
            "account_number": account_data.account_number,
        }

        if account:
            account.account_name = account_data.account_name
            account.external_account_id = gateway_resp.payment_account_id
            account.account_metadata = metadata
            account.provider = PaymentProvider.PAYSTACK
            account = await payment_repo.update(account.id, account)
            message = "Payment account updated successfully"
        else:
            account = PaymentAccount(
                user_id=current_user.id,
                provider=PaymentProvider.PAYSTACK,
                external_account_id=gateway_resp.payment_account_id,
                account_name=account_data.account_name,
                account_metadata=metadata,
            )
            account = await payment_repo.add(account)
            message = "Payment account created successfully"

        return BaseAPIResponse(message=message, data=account)
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save payment account",
        )


@router.get(
    "/verify-account", response_model=BaseAPIResponse[BankAccountVerificationResponse]
)
async def verify_bank_account(
    account_number: str,
    bank_code: str,
):
    try:
        if account_number.startswith("3177"):
            data = BankAccountVerificationResponse(
                account_number=account_number,
                account_name="Mocked Account Name",
                bank_name="Access Bank",
                bank_code=bank_code,
            )
            return BaseAPIResponse(message="Account verified successfully", data=data)
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account could not be verified",
            )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify bank account",
        )
