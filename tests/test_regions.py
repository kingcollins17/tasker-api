from app.core.models.regions import Region
from app.core.models.users import User, UserType, UserLocation
from app.core.models.admins import AdminUser, AdminRole

def test_region_model_instantiation():
    # Arrange & Act
    region = Region(
        state="Lagos",
        address_line="123 Broad Street",
        is_active=True,
        total_providers=10,
        total_customers=150,
        total_tasks=25,
        total_staff=5,
        location="POINT(3.3792 6.5244)"
    )
    
    # Assert
    assert region.id is not None
    assert region.state == "Lagos"
    assert region.address_line == "123 Broad Street"
    assert region.is_active is True
    assert region.total_providers == 10
    assert region.total_customers == 150
    assert region.total_tasks == 25
    assert region.total_staff == 5
    assert region.location == "POINT(3.3792 6.5244)"
    assert region.created_at is not None
    assert region.updated_at is not None

def test_user_and_admin_region_id_field():
    user = User(
        email="test@example.com",
        hashed_password="hash",
        type=UserType.CUSTOMER,
        region_id="region-123"
    )
    admin = AdminUser(
        email="admin@example.com",
        hashed_password="hash",
        role=AdminRole.SUPER_ADMIN,
        region_id="region-123"
    )
    user_loc = UserLocation(
        user_id="user-123",
        region_id="region-123",
        address_line="123 Road"
    )
    
    assert user.region_id == "region-123"
    assert user.credibility_score == 25.0
    assert user.average_ratings == 0.0
    assert admin.region_id == "region-123"
    assert user_loc.region_id == "region-123"


