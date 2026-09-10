import pytest
from app.core.utils.currency import to_naira


def test_to_naira_formatting():
    assert to_naira(145000) == "₦145,000.00"
    assert to_naira(145000.5) == "₦145,000.50"
    assert to_naira("145000") == "₦145,000.00"
    assert to_naira(0) == "₦0.00"
    assert to_naira(None) == "₦0.00"
    assert to_naira("invalid") == "₦0.00"
