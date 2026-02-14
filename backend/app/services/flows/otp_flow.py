"""OTP authentication flow handler.

Steps: await_email → await_code → verified
"""

import logging
import re

from app.services.conversation_flow import (
    ConversationFlow,
    FlowHandler,
    FlowStepResult,
    FlowType,
)
from app.services.customer_session_service import CustomerSessionService
from app.services.otp_service import OTPService

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
CODE_REGEX = re.compile(r"^\d{6}$")


class OTPFlowHandler(FlowHandler):
    """Handles the OTP authentication flow: email → code → verified."""

    def __init__(
        self,
        otp_service: OTPService,
        session_service: CustomerSessionService,
        odoo_adapter,
    ):
        self.otp = otp_service
        self.session = session_service
        self.odoo_adapter = odoo_adapter

    @property
    def flow_type(self) -> FlowType:
        return FlowType.OTP_AUTH

    def initial_step(self) -> str:
        return "await_email"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        if step == "await_email":
            return await self._handle_email(flow, user_message, visitor_id)
        elif step == "await_code":
            return await self._handle_code(flow, user_message, visitor_id)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_email(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        """Validate email format and send OTP."""
        email = user_message.strip().lower()

        # Try to extract email from message if it's mixed with text
        email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", email)
        if email_match:
            email = email_match.group(0)

        if not EMAIL_REGEX.match(email):
            return FlowStepResult(
                message="Gecerli bir e-posta adresi giriniz. Ornegin: isim@firma.com",
            )

        # Request OTP
        result = await self.otp.request_otp(visitor_id, email, self.odoo_adapter)

        if not result.success:
            return FlowStepResult(message=result.message)

        # Move to next step
        flow.step = "await_code"
        flow.data["email"] = email

        return FlowStepResult(message=result.message)

    async def _handle_code(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        """Verify OTP code."""
        code = user_message.strip()

        # Extract 6-digit code from message
        code_match = re.search(r"\d{6}", code)
        if code_match:
            code = code_match.group(0)

        if not CODE_REGEX.match(code):
            return FlowStepResult(
                message="Lutfen 6 haneli dogrulama kodunu giriniz.",
            )

        email = flow.data.get("email", "")
        result = await self.otp.verify_otp(visitor_id, email, code)

        if not result.success:
            return FlowStepResult(message=result.message)

        # Create customer session
        await self.session.create_session(
            visitor_id=visitor_id,
            partner_id=result.partner_id,
            email=result.email,
            name=result.partner_name or "",
        )

        # Determine what the user originally wanted
        original_intent = flow.data.get("original_intent", "")
        follow_up = ""
        if original_intent:
            follow_up = " Simdi talebinizi islemekteyim..."

        return FlowStepResult(
            message=f"{result.message}{follow_up}",
            flow_completed=True,
            data={
                "partner_id": result.partner_id,
                "partner_name": result.partner_name,
                "email": result.email,
                "original_intent": original_intent,
            },
        )
