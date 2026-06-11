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
            last_known_location="POINT(3.3792 6.5244)"
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

        # Verify location
        assert profile.last_known_location == "POINT(3.3792 6.5244)"

        # Verify created_at / updated_at exist and are populated
        assert user.created_at is not None
        assert user.updated_at is not None
        assert profile.created_at is not None
        assert profile.updated_at is not None
        assert ps_acc.created_at is not None
        assert ps_acc.updated_at is not None

def test_service_category_relationship():
    from app.core.models import Service, ServiceCategory
    
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        # Create a category
        cat = ServiceCategory(
            name="Cleaning Services",
            description="All cleaning related tasks"
        )
        session.add(cat)
        session.commit()
        session.refresh(cat)
        
        # Create services linked to the category
        svc1 = Service(
            name="Deep House Cleaning",
            take_rate=0.15,
            category_id=cat.id
        )
        svc2 = Service(
            name="Carpet Cleaning",
            take_rate=0.12,
            category_id=cat.id
        )
        session.add(svc1)
        session.add(svc2)
        session.commit()
        
        session.refresh(cat)
        
        # Verify relationship
        assert len(cat.services) == 2
        service_names = {s.name for s in cat.services}
        assert "Deep House Cleaning" in service_names
        assert "Carpet Cleaning" in service_names
        
        # pyrefly: ignore [missing-attribute]
        assert svc1.category.name == "Cleaning Services"
        # pyrefly: ignore [missing-attribute]
        assert svc2.category.name == "Cleaning Services"

        # Verify standard datetimes exist
        assert cat.created_at is not None
        assert cat.updated_at is not None
        assert svc1.created_at is not None
        assert svc1.updated_at is not None
        
        # Verify SET NULL behavior on delete of parent category
        session.delete(cat)
        session.commit()
        
        # Services should NOT be deleted, but category_id should be set to None
        db_svc1 = session.get(Service, svc1.id)
        db_svc2 = session.get(Service, svc2.id)
        
        assert db_svc1 is not None
        assert db_svc2 is not None
        assert db_svc1.category_id is None
        assert db_svc2.category_id is None
