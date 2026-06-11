import pytest
from unittest.mock import patch
from app.core.services import EmailService, SMSService, email_service, sms_service
from app.core.services.templates import render_template, jinja_env
from jinja2 import FileSystemLoader

def test_services_export_and_singleton():
    """Verify that services and their singletons are correctly exported."""
    assert isinstance(email_service, EmailService)
    assert isinstance(sms_service, SMSService)

@pytest.mark.anyio
async def test_email_service_send_single():
    """Verify EmailService.send_email executes and logs correctly for a single email."""
    service = EmailService()
    
    with patch("app.core.services.email.logger") as mock_logger:
        result = await service.send_email(
            to_emails="test@example.com",
            subject="Test Subject",
            body="Test plain body",
            html_body="<h1>Test html body</h1>"
        )
        assert result == {"test@example.com": True}
        mock_logger.info.assert_called_once_with(
            "[MOCK EMAIL] Sending email to: test@example.com | Subject: Test Subject"
        )
        mock_logger.debug.assert_called_once_with(
            "[MOCK EMAIL] HTML Body: <h1>Test html body</h1>"
        )

@pytest.mark.anyio
async def test_email_service_send_multiple():
    """Verify EmailService.send_email executes and logs correctly for multiple emails."""
    service = EmailService()
    
    with patch("app.core.services.email.logger") as mock_logger:
        result = await service.send_email(
            to_emails=["user1@example.com", "user2@example.com"],
            subject="Test Subject",
            body="Test plain body"
        )
        assert result == {"user1@example.com": True, "user2@example.com": True}
        assert mock_logger.info.call_count == 2
        mock_logger.info.assert_any_call(
            "[MOCK EMAIL] Sending email to: user1@example.com | Subject: Test Subject"
        )
        mock_logger.info.assert_any_call(
            "[MOCK EMAIL] Sending email to: user2@example.com | Subject: Test Subject"
        )

@pytest.mark.anyio
async def test_email_service_send_with_templates():
    """Verify EmailService.send_email correctly renders templates and supports personalization."""
    service = EmailService()
    
    with patch("app.core.services.email.render_template") as mock_render, \
         patch("app.core.services.email.logger") as mock_logger:
        
        mock_render.side_effect = lambda t, c: f"Rendered {t} for {c.get('name')}"
        
        result = await service.send_email(
            to_emails=["user1@example.com", "user2@example.com"],
            subject="Welcome!",
            template_name="welcome.txt",
            html_template_name="welcome.html",
            context={"name": "Valued Customer"},
            personalized_contexts={
                "user1@example.com": {"name": "Alice"},
                "user2@example.com": {"name": "Bob"}
            }
        )
        
        assert result == {"user1@example.com": True, "user2@example.com": True}
        assert mock_logger.info.call_count == 2
        
        mock_logger.debug.assert_any_call(
            "[MOCK EMAIL] HTML Body: Rendered welcome.html for Alice"
        )
        mock_logger.debug.assert_any_call(
            "[MOCK EMAIL] HTML Body: Rendered welcome.html for Bob"
        )

@pytest.mark.anyio
async def test_sms_service_send_single():
    """Verify SMSService.send_sms executes and logs correctly for a single phone number."""
    service = SMSService()
    
    with patch("app.core.services.sms.logger") as mock_logger:
        result = await service.send_sms(
            phone_numbers="+1234567890",
            message="Hello, this is a test SMS."
        )
        assert result == {"+1234567890": True}
        mock_logger.info.assert_called_once_with(
            "[MOCK SMS] Sending SMS to: +1234567890 | Message: Hello, this is a test SMS."
        )

@pytest.mark.anyio
async def test_sms_service_send_multiple():
    """Verify SMSService.send_sms executes and logs correctly for multiple phone numbers."""
    service = SMSService()
    
    with patch("app.core.services.sms.logger") as mock_logger:
        result = await service.send_sms(
            phone_numbers=["+111", "+222"],
            message="Hello!"
        )
        assert result == {"+111": True, "+222": True}
        assert mock_logger.info.call_count == 2




@pytest.mark.anyio
async def test_render_template(tmp_path):
    """Verify that render_template correctly loads and renders a template."""
    # Create a temporary template file
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    template_file = template_dir / "test.html"
    template_file.write_text("Hello {{ name }}!")
    
    # Temporarily set the jinja loader to the new temp directory
    original_loader = jinja_env.loader
    jinja_env.loader = FileSystemLoader(str(template_dir))
    
    try:
        rendered = render_template("test.html", {"name": "Alice"})
        assert rendered == "Hello Alice!"
    finally:
        jinja_env.loader = original_loader


