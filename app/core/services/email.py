from typing import Optional, List, Dict, Union
from app.core.logging import logger
from app.core.services.templates import render_template

class EmailService:
    """Service to handle email communications (Mock implementation)."""

    async def send_email(
        self,
        to_emails: Union[str, List[str]],
        subject: str,
        body: Optional[str] = None,
        html_body: Optional[str] = None,
        template_name: Optional[str] = None,
        html_template_name: Optional[str] = None,
        context: Optional[dict] = None,
        personalized_contexts: Optional[Dict[str, dict]] = None
    ) -> Dict[str, bool]:
        """Sends email to one or more recipients, with optional Jinja2 template rendering.

        Args:
            to_emails: A single recipient email address or a list of addresses.
            subject: Email subject line.
            body: Plain text email body (ignored if template_name is provided).
            html_body: HTML email body (ignored if html_template_name is provided).
            template_name: Optional path of the text template file under app/templates/
            html_template_name: Optional path of the HTML template file under app/templates/
            context: Default context dictionary for template rendering.
            personalized_contexts: Optional dict mapping email to recipient-specific context.

        Returns:
            Dict[str, bool]: Map of recipient email to success status.
        """
        # Normalize to list of emails
        emails = [to_emails] if isinstance(to_emails, str) else to_emails
        
        default_context = context or {}
        results = {}
        
        for email in emails:
            recipient_context = default_context
            if personalized_contexts and email in personalized_contexts:
                recipient_context = {**default_context, **personalized_contexts[email]}
            
            # Resolve body
            final_body = body
            if template_name:
                final_body = render_template(template_name, recipient_context)
            
            # Resolve html body
            final_html_body = html_body
            if html_template_name:
                final_html_body = render_template(html_template_name, recipient_context)
            
            # Perform mock send
            logger.info(
                f"[MOCK EMAIL] Sending email to: {email} | Subject: {subject}"
            )
            if final_html_body:
                logger.debug(f"[MOCK EMAIL] HTML Body: {final_html_body}")
            elif final_body:
                logger.debug(f"[MOCK EMAIL] Plain Body: {final_body}")
            
            results[email] = True
            
        return results


