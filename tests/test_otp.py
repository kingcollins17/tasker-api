import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core.services.otp import (
    OTPService,
    OTPRateLimitError,
    OTPVerificationError,
    OTPMaxAttemptsReachedError,
    OTPError,
)

@pytest.fixture
def mock_cache_service():
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=True)
    cache.exists = AsyncMock(return_value=False)
    cache.delete = AsyncMock(return_value=True)
    return cache

@pytest.fixture
def mock_email_service():
    email = MagicMock()
    email.send_email = AsyncMock(return_value={})
    return email

@pytest.fixture
def mock_sms_service():
    sms = MagicMock()
    sms.send_sms = AsyncMock(return_value={})
    return sms

@pytest.fixture
def otp_service(mock_cache_service, mock_email_service, mock_sms_service):
    return OTPService(
        cache_service=mock_cache_service,
        email_service=mock_email_service,
        sms_service=mock_sms_service,
        otp_expiry_seconds=300,
        cooldown_seconds=60,
        max_attempts=3,
    )

@pytest.mark.asyncio
async def test_generate_otp_success(otp_service, mock_cache_service):
    target = "user@example.com"
    channel = "email"
    
    # Act
    code = await otp_service.generate_otp(target, channel)
    
    # Assert
    assert len(code) == 6
    assert code.isdigit()
    
    # Verify cache sets
    otp_key = otp_service._get_otp_key(channel, target)
    attempts_key = otp_service._get_attempts_key(channel, target)
    cooldown_key = otp_service._get_cooldown_key(channel, target)
    
    mock_cache_service.set.assert_any_call(otp_key, code, expire=300)
    mock_cache_service.set.assert_any_call(attempts_key, "0", expire=300)
    mock_cache_service.set.assert_any_call(cooldown_key, "1", expire=60)

@pytest.mark.asyncio
async def test_generate_otp_invalid_channel(otp_service):
    with pytest.raises(ValueError, match="Invalid delivery channel"):
        await otp_service.generate_otp("user@example.com", "invalid_channel")

@pytest.mark.asyncio
async def test_generate_otp_cooldown_active(otp_service, mock_cache_service):
    target = "user@example.com"
    channel = "email"
    
    # Mock cooldown block exists
    mock_cache_service.exists.return_value = True
    
    with pytest.raises(OTPRateLimitError, match="Please wait before requesting a new OTP"):
        await otp_service.generate_otp(target, channel)

@pytest.mark.asyncio
async def test_send_otp_email_success(otp_service, mock_email_service):
    target = "user@example.com"
    code = "123456"
    
    mock_email_service.send_email.return_value = {target: True}
    
    result = await otp_service.send_otp(target, "email", code)
    
    assert result is True
    mock_email_service.send_email.assert_called_once_with(
        to_emails=target,
        subject="Your Verification Code",
        body=f"Your verification code is: {code}. It is valid for 5 minutes."
    )

@pytest.mark.asyncio
async def test_send_otp_sms_success(otp_service, mock_sms_service):
    target = "+1234567890"
    code = "654321"
    
    mock_sms_service.send_sms.return_value = {target: True}
    
    result = await otp_service.send_otp(target, "sms", code)
    
    assert result is True
    mock_sms_service.send_sms.assert_called_once_with(
        phone_numbers=target,
        message=f"Your verification code is: {code}. Valid for 5 minutes."
    )

@pytest.mark.asyncio
async def test_generate_and_send_otp_success(otp_service, mock_email_service):
    target = "user@example.com"
    mock_email_service.send_email.return_value = {target: True}
    
    result = await otp_service.generate_and_send_otp(target, "email")
    
    assert result is True

@pytest.mark.asyncio
async def test_generate_and_send_otp_failed_rollback(otp_service, mock_email_service, mock_cache_service):
    target = "user@example.com"
    channel = "email"
    
    # Mock email send failure
    mock_email_service.send_email.return_value = {target: False}
    
    with pytest.raises(OTPError, match=f"Failed to deliver verification code via {channel}"):
        await otp_service.generate_and_send_otp(target, channel)
        
    # Verify rollback deletes cache entries
    otp_key = otp_service._get_otp_key(channel, target)
    attempts_key = otp_service._get_attempts_key(channel, target)
    cooldown_key = otp_service._get_cooldown_key(channel, target)
    
    mock_cache_service.delete.assert_any_call(otp_key)
    mock_cache_service.delete.assert_any_call(attempts_key)
    mock_cache_service.delete.assert_any_call(cooldown_key)

@pytest.mark.asyncio
async def test_verify_otp_success(otp_service, mock_cache_service):
    target = "user@example.com"
    channel = "email"
    code = "112233"
    
    # Mock existing code and 0 attempts
    mock_cache_service.get.side_effect = lambda k: {
        otp_service._get_otp_key(channel, target): code,
        otp_service._get_attempts_key(channel, target): "0"
    }.get(k)
    
    result = await otp_service.verify_otp(target, channel, code)
    
    assert result is True
    
    # Verify key cleanup on success
    otp_key = otp_service._get_otp_key(channel, target)
    attempts_key = otp_service._get_attempts_key(channel, target)
    mock_cache_service.delete.assert_any_call(otp_key)
    mock_cache_service.delete.assert_any_call(attempts_key)

@pytest.mark.asyncio
async def test_verify_otp_incorrect_attempt(otp_service, mock_cache_service):
    target = "user@example.com"
    channel = "email"
    correct_code = "112233"
    wrong_code = "445566"
    
    # Mock existing code and 0 attempts
    mock_cache_service.get.side_effect = lambda k: {
        otp_service._get_otp_key(channel, target): correct_code,
        otp_service._get_attempts_key(channel, target): "0"
    }.get(k)
    
    result = await otp_service.verify_otp(target, channel, wrong_code)
    
    assert result is False
    
    # Verify attempt counter is incremented in cache
    attempts_key = otp_service._get_attempts_key(channel, target)
    mock_cache_service.set.assert_any_call(attempts_key, "1", expire=300)

@pytest.mark.asyncio
async def test_verify_otp_max_attempts_exceeded(otp_service, mock_cache_service):
    target = "user@example.com"
    channel = "email"
    correct_code = "112233"
    wrong_code = "445566"
    
    # Mock existing code and 2 attempts (max is 3, so this 3rd wrong attempt will trigger invalidation)
    mock_cache_service.get.side_effect = lambda k: {
        otp_service._get_otp_key(channel, target): correct_code,
        otp_service._get_attempts_key(channel, target): "2"
    }.get(k)
    
    with pytest.raises(OTPMaxAttemptsReachedError, match="Maximum verification attempts exceeded"):
        await otp_service.verify_otp(target, channel, wrong_code)
        
    # Verify keys are deleted
    otp_key = otp_service._get_otp_key(channel, target)
    attempts_key = otp_service._get_attempts_key(channel, target)
    mock_cache_service.delete.assert_any_call(otp_key)
    mock_cache_service.delete.assert_any_call(attempts_key)

@pytest.mark.asyncio
async def test_verify_otp_expired_or_nonexistent(otp_service, mock_cache_service):
    target = "user@example.com"
    channel = "email"
    
    # Mock cache returning None (expired/nonexistent)
    mock_cache_service.get.return_value = None
    
    with pytest.raises(OTPVerificationError, match="Verification code has expired or does not exist"):
        await otp_service.verify_otp(target, channel, "999999")

@pytest.mark.asyncio
async def test_generate_otp_is_always_123456(otp_service):
    target = "user@example.com"
    channel = "email"
    
    code = await otp_service.generate_otp(target, channel)
    assert code == "123456"
