from typing import Union, Optional


def to_naira(amount: Union[float, int, str, None]) -> str:
    """Format an amount into Naira currency format (e.g., ₦145,000.00)."""
    if amount is None:
        return "₦0.00"
    try:
        val = float(amount)
        return f"₦{val:,.2f}"
    except (ValueError, TypeError):
        return "₦0.00"
