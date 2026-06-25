from typing import Optional

def format_nigerian_phone(phone: str) -> str:
    """Formats an incoming phone number into the standard Nigerian format (+234...).
    
    Examples:
        "08031234567" -> "+2348031234567"
        "8031234567"  -> "+2348031234567"
        "+2348031234567" -> "+2348031234567"
        "2348031234567" -> "+2348031234567"
    """
    if not phone:
        raise ValueError("Phone number cannot be empty")
        
    # Remove all whitespace and non-digit characters
    digits = "".join(char for char in phone if char.isdigit())
    
    # Standard 13-digit number starting with 234 (e.g. 2348031234567)
    if digits.startswith("234") and len(digits) == 13:
        return f"+{digits}"
        
    # Standard 11-digit number starting with 0 (e.g. 08031234567)
    if digits.startswith("0") and len(digits) == 11:
        return f"+234{digits[1:]}"
        
    # Standard 10-digit number without country code or leading 0 (e.g. 8031234567)
    if len(digits) == 10:
        return f"+234{digits}"
        
    return f"+{digits}"
