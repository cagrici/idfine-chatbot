"""OTP (One-Time Password) service for customer authentication via email."""

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass

import redis.asyncio as redis

from app.config import get_settings
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class OTPResult:
    success: bool
    message: str
    partner_id: int | None = None
    partner_name: str | None = None
    email: str | None = None


class OTPService:
    """Manages OTP generation, storage (Redis), verification, and email sending via SMTP."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._email_service = EmailService()

    def _email_hash(self, email: str) -> str:
        return hashlib.sha256(email.lower().strip().encode()).hexdigest()[:16]

    def _code_hash(self, code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def _otp_key(self, visitor_id: str, email_hash: str) -> str:
        return f"otp:{visitor_id}:{email_hash}"

    def _attempts_key(self, visitor_id: str) -> str:
        return f"otp_attempts:{visitor_id}"

    def _rate_key(self, email_hash: str) -> str:
        return f"otp_rate:{email_hash}"

    async def request_otp(
        self,
        visitor_id: str,
        email: str,
        odoo_adapter,
    ) -> OTPResult:
        """Generate OTP, store in Redis, send via Odoo email.

        Returns success even if email not found in Odoo (to prevent email enumeration).
        """
        email = email.lower().strip()
        email_hash = self._email_hash(email)

        # Rate limit: max N OTP requests per email per hour
        rate_key = self._rate_key(email_hash)
        rate_count = await self.redis.get(rate_key)
        if rate_count and int(rate_count) >= settings.otp_max_requests_per_hour:
            return OTPResult(
                success=False,
                message="Bu e-posta adresi icin cok fazla dogrulama kodu istendi. Lutfen daha sonra tekrar deneyin.",
            )

        # Check lockout
        attempts_key = self._attempts_key(visitor_id)
        attempts = await self.redis.get(attempts_key)
        if attempts and int(attempts) >= settings.otp_max_attempts:
            ttl = await self.redis.ttl(attempts_key)
            return OTPResult(
                success=False,
                message=f"Cok fazla basarisiz deneme. Lutfen {max(ttl // 60, 1)} dakika sonra tekrar deneyin.",
            )

        # Search partner in Odoo
        partner_id = None
        partner_name = None
        try:
            partners = await odoo_adapter.call(
                "res.partner",
                "search_read",
                [[["email", "=ilike", email]]],
                {"fields": ["id", "name", "email"], "limit": 1},
            )
            if partners:
                partner_id = partners[0]["id"]
                partner_name = partners[0].get("name", "")
        except Exception as e:
            logger.error("Odoo partner lookup failed: %s", e)
            return OTPResult(
                success=False,
                message="Kimlik dogrulama sistemi su anda kullanilamamaktadir. Lutfen daha sonra tekrar deneyin.",
            )

        # Generate 6-digit code
        code = f"{secrets.randbelow(900000) + 100000}"
        code_hashed = self._code_hash(code)

        # Store OTP in Redis
        otp_key = self._otp_key(visitor_id, email_hash)
        otp_data = json.dumps({
            "code_hash": code_hashed,
            "email": email,
            "partner_id": partner_id,
            "partner_name": partner_name,
            "attempts": 0,
        })
        await self.redis.set(otp_key, otp_data, ex=settings.otp_ttl_seconds)

        # Increment rate limit counter
        pipe = self.redis.pipeline()
        pipe.incr(rate_key)
        pipe.expire(rate_key, 3600)  # 1 hour window
        await pipe.execute()

        # Send OTP email via Odoo (only if partner found)
        if partner_id:
            try:
                await self._send_otp_email(odoo_adapter, email, code, partner_name)
            except Exception as e:
                logger.error("Failed to send OTP email: %s", e)
                return OTPResult(
                    success=False,
                    message="Dogrulama kodu gonderilirken bir hata olustu. Lutfen tekrar deneyin.",
                )

        # Always return success message (even if partner not found) to prevent email enumeration
        return OTPResult(
            success=True,
            message=f"{email} adresine 6 haneli dogrulama kodu gonderildi. Lutfen kodu buraya yazin. (5 dakika gecerlidir)",
        )

    async def verify_otp(self, visitor_id: str, email: str, code: str) -> OTPResult:
        """Verify OTP code against Redis stored hash."""
        email = email.lower().strip()
        email_hash = self._email_hash(email)
        otp_key = self._otp_key(visitor_id, email_hash)

        # Check lockout
        attempts_key = self._attempts_key(visitor_id)
        attempts = await self.redis.get(attempts_key)
        if attempts and int(attempts) >= settings.otp_max_attempts:
            ttl = await self.redis.ttl(attempts_key)
            return OTPResult(
                success=False,
                message=f"Cok fazla basarisiz deneme. Lutfen {max(ttl // 60, 1)} dakika sonra tekrar deneyin.",
            )

        # Get stored OTP
        otp_raw = await self.redis.get(otp_key)
        if not otp_raw:
            return OTPResult(
                success=False,
                message="Dogrulama kodu suresi dolmus veya bulunamadi. Lutfen yeni bir kod isteyin.",
            )

        otp_data = json.loads(otp_raw)
        stored_hash = otp_data["code_hash"]
        partner_id = otp_data.get("partner_id")
        partner_name = otp_data.get("partner_name")

        # Verify code
        if self._code_hash(code.strip()) != stored_hash:
            # Increment attempt counter
            pipe = self.redis.pipeline()
            pipe.incr(attempts_key)
            pipe.expire(attempts_key, settings.otp_lockout_seconds)
            await pipe.execute()

            remaining = settings.otp_max_attempts - (int(attempts or 0) + 1)
            if remaining <= 0:
                await self.redis.delete(otp_key)
                return OTPResult(
                    success=False,
                    message="Cok fazla basarisiz deneme. Hesabiniz gecici olarak kilitlendi.",
                )
            return OTPResult(
                success=False,
                message=f"Yanlis dogrulama kodu. {remaining} deneme hakkiniz kaldi.",
            )

        # Partner not found in Odoo - can't create session
        if not partner_id:
            await self.redis.delete(otp_key)
            return OTPResult(
                success=False,
                message="Bu e-posta adresi ile eslesen bir musteri kaydi bulunamadi. Lutfen kayitli e-posta adresinizi kullaniyor oldugunuzdan emin olun.",
            )

        # Success - clean up
        await self.redis.delete(otp_key)
        await self.redis.delete(attempts_key)

        return OTPResult(
            success=True,
            message=f"Basariyla dogrulandi! Merhaba {partner_name or 'degerli musterimiz'}.",
            partner_id=partner_id,
            partner_name=partner_name,
            email=email,
        )

    async def _send_otp_email(
        self, odoo_adapter, email: str, code: str, partner_name: str | None
    ) -> None:
        """Send OTP email via SMTP."""
        name = partner_name or "Degerli Musterimiz"
        subject = "ID Fine - Dogrulama Kodunuz"
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 20px;">
                <h2 style="color: #231f20; margin: 0;">ID Fine</h2>
                <p style="color: #666; margin: 5px 0;">Porser Porselen</p>
            </div>
            <div style="background: #f7f7f7; border-radius: 8px; padding: 24px; text-align: center;">
                <p style="margin: 0 0 10px 0;">Merhaba <strong>{name}</strong>,</p>
                <p style="margin: 0 0 20px 0;">Dogrulama kodunuz:</p>
                <div style="background: #231f20; color: white; font-size: 32px; letter-spacing: 8px;
                            padding: 16px 32px; border-radius: 8px; display: inline-block; font-weight: bold;">
                    {code}
                </div>
                <p style="margin: 20px 0 0 0; color: #888; font-size: 13px;">
                    Bu kod 5 dakika gecerlidir. Kodu kimseyle paylasmayiniz.
                </p>
            </div>
            <p style="color: #999; font-size: 11px; text-align: center; margin-top: 20px;">
                Bu e-postayi siz istemediyseniz, lutfen dikkate almayin.
            </p>
        </div>
        """

        # Send via SMTP in a thread to avoid blocking the event loop
        sent = await asyncio.to_thread(self._email_service.send, email, subject, body_html)
        if not sent:
            raise RuntimeError("SMTP email sending failed")
        logger.info("OTP email sent to %s via SMTP", email)
