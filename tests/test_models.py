from sqlmodel import create_engine, Session, SQLModel
from app.core.models.users import (
    User,
    ProviderProfile,
    ProviderPaymentAccount,
    PaymentProvider,
    UserType,
)

def test_provider_payment_account_schema():
    # Create synchronous SQLite in-memory engine
    engine = create_engine("sqlite:///:memory:")
    
    # Create tables defined in SQLModel metadata
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        # 1. Create a user
        user = User(
            phone_number="+2348012345678",
            email="provider@tasker.com",
            type=UserType.PROVIDER,
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        
        # 2. Create a provider profile linked to the user
        profile = ProviderProfile(
            user_id=user.id,
            first_name="Jane",
            last_name="Doe",
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        
        # 3. Add a payment account (e.g. Paystack subaccount)
        paystack_account = ProviderPaymentAccount(
            provider_id=profile.id,
            provider=PaymentProvider.PAYSTACK,
            external_account_id="ACCT_98765432",
            account_name="Jane Doe Paystack",
            account_metadata={
                "bank_name": "Access Bank",
                "bank_code": "044",
                "recipient_code": "RCP_2yux9xyz"
            }
        )
        
        # 4. Add a secondary payment account (e.g. Flutterwave) to test multiple accounts support
        flutterwave_account = ProviderPaymentAccount(
            provider_id=profile.id,
            provider=PaymentProvider.FLUTTERWAVE,
            external_account_id="flw-sub-10029",
            account_name="Jane Doe Flutterwave",
            account_metadata={
                "bank_name": "GTBank",
                "bank_code": "058"
            }
        )
        
        session.add(paystack_account)
        session.add(flutterwave_account)
        session.commit()
        
        # Refresh profile to load updated relationship
        session.refresh(profile)
        
        # 5. Verify the relationship mapping
        assert len(profile.payment_accounts) == 2
        
        # Sort or index lookup to check properties
        accounts_by_provider = {acc.provider: acc for acc in profile.payment_accounts}
        
        assert PaymentProvider.PAYSTACK in accounts_by_provider
        assert PaymentProvider.FLUTTERWAVE in accounts_by_provider
        
        # Check Paystack details
        ps_acc = accounts_by_provider[PaymentProvider.PAYSTACK]
        assert ps_acc.external_account_id == "ACCT_98765432"
        assert ps_acc.account_metadata["bank_name"] == "Access Bank"
        assert ps_acc.account_metadata["recipient_code"] == "RCP_2yux9xyz"
        assert ps_acc.provider_profile.id == profile.id
        
        # Check Flutterwave details
        flw_acc = accounts_by_provider[PaymentProvider.FLUTTERWAVE]
        assert flw_acc.external_account_id == "flw-sub-10029"
        assert flw_acc.account_metadata["bank_name"] == "GTBank"
        assert flw_acc.is_active is True
