"""Quotation request flow handler.

Steps: await_products → await_confirm → created

Users enter product codes + quantities. The flow looks up odoo_product_id
from the local product database and creates real order lines in Odoo.
"""

import logging
import re
from typing import Optional

from sqlalchemy import select

from app.db.database import async_session
from app.models.product import Product
from app.services.conversation_flow import (
    ConversationFlow,
    FlowHandler,
    FlowStepResult,
    FlowType,
)
from app.services.customer_session_service import CustomerSessionService
from app.services.odoo_service import OdooService

logger = logging.getLogger(__name__)

# Matches: CODE , QTY  or  CODE : QTY  or  CODE  QTY
_LINE_RE = re.compile(
    r'^([A-Za-z0-9][A-Za-z0-9\-./]{2,})\s*[,:\s]\s*(\d+(?:[.,]\d+)?)\s*.*$'
)
# Code only (no quantity)
_CODE_ONLY_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9\-./]{4,})\s*$')


def _parse_product_lines(text: str) -> list[tuple[str, float]]:
    """Parse product lines from user input.

    Returns list of (urun_kodu, miktar) tuples.
    Supports formats:
    - "20257-111030, 50"
    - "20257-111030 50"
    - "20257-111030:50"
    - "20257-111030" (qty defaults to 1)
    """
    results = []
    for line in text.strip().split("\n"):
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if m:
            code = m.group(1).strip().upper()
            qty_str = m.group(2).replace(",", ".")
            qty = float(qty_str)
            if qty <= 0:
                qty = 1.0
            results.append((code, qty))
            continue
        m = _CODE_ONLY_RE.match(line)
        if m:
            code = m.group(1).strip().upper()
            results.append((code, 1.0))
    return results


async def _lookup_products(
    codes: list[str],
) -> dict[str, tuple[Optional[int], Optional[str]]]:
    """Look up odoo_product_id and urun_tanimi from local DB by urun_kodu.

    Returns {urun_kodu: (odoo_product_id, urun_tanimi)}
    """
    async with async_session() as db:
        result = await db.execute(
            select(Product.urun_kodu, Product.odoo_product_id, Product.urun_tanimi)
            .where(Product.urun_kodu.in_(codes))
            .where(Product.aktif == True)  # noqa: E712
        )
        rows = result.fetchall()
    return {row.urun_kodu: (row.odoo_product_id, row.urun_tanimi) for row in rows}


def _fmt_qty(qty: float) -> str:
    return str(int(qty)) if qty == int(qty) else str(qty)


class QuotationFlowHandler(FlowHandler):
    """Handles quotation: enter product codes+qty → confirm → create real Odoo order lines."""

    def __init__(
        self,
        odoo_service: OdooService,
        session_service: CustomerSessionService,
    ):
        self.odoo = odoo_service
        self.session = session_service

    @property
    def flow_type(self) -> FlowType:
        return FlowType.QUOTATION_CREATE

    def initial_step(self) -> str:
        return "await_products"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        if step == "await_products":
            return await self._handle_products(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message, visitor_id)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_products(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        parsed = _parse_product_lines(user_message)

        if not parsed:
            return FlowStepResult(
                message=(
                    "Urun kodu tespit edilemedi. Lutfen asagidaki formatta yazin:\n\n"
                    "**urun\\_kodu, miktar**\n"
                    "Her urunu ayri satira yazin.\n\n"
                    "Ornek:\n"
                    "20257-111030, 50\n"
                    "20257-111031, 10"
                ),
            )

        codes = [code for code, _ in parsed]
        product_map = await _lookup_products(codes)

        found_lines: list[dict] = []
        not_found_codes: list[str] = []

        for code, qty in parsed:
            if code in product_map:
                odoo_product_id, urun_tanimi = product_map[code]
                if odoo_product_id:
                    found_lines.append({
                        "urun_kodu": code,
                        "urun_tanimi": urun_tanimi or code,
                        "odoo_product_id": odoo_product_id,
                        "quantity": qty,
                    })
                else:
                    # Product exists in DB but no Odoo ID synced yet
                    not_found_codes.append(code)
            else:
                not_found_codes.append(code)

        if not found_lines:
            return FlowStepResult(
                message=(
                    f"Girilen urun kodlari sistemde bulunamadi: **{', '.join(not_found_codes)}**\n\n"
                    "Lutfen gecerli urun kodlarini girin veya urun arama yaparak kodu dogrulayin."
                ),
            )

        flow.data["lines"] = found_lines
        flow.data["not_found"] = not_found_codes
        flow.step = "await_confirm"

        # Build summary message
        parts = ["**Teklif ozeti:**\n"]
        for line in found_lines:
            parts.append(
                f"- **{line['urun_kodu']}** — {line['urun_tanimi']} x {_fmt_qty(line['quantity'])} adet"
            )

        if not_found_codes:
            parts.append(
                f"\n*Not: Asagidaki kodlar bulunamadi ve teklife eklenmedi: {', '.join(not_found_codes)}*"
            )

        parts.append("\nTeklif talebini gondermek istiyor musunuz? (**evet** / **hayir**)")

        return FlowStepResult(message="\n".join(parts))

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no", "h"):
            return FlowStepResult(
                message="Teklif talebi iptal edildi.",
                flow_cancelled=True,
            )

        if text not in ("evet", "yes", "e", "ok", "tamam", "onay", "onayla"):
            return FlowStepResult(
                message="Lutfen **evet** veya **hayir** yazin.",
            )

        session = await self.session.get_session(visitor_id)
        if not session:
            return FlowStepResult(
                message="Oturum suresi dolmus. Lutfen tekrar giris yapin.",
                flow_cancelled=True,
            )

        try:
            lines = flow.data.get("lines", [])
            order_lines = [
                {
                    "product_id": line["odoo_product_id"],
                    "quantity": line["quantity"],
                }
                for line in lines
            ]

            not_found = flow.data.get("not_found", [])
            notes = None
            if not_found:
                notes = f"Bulunamayan urun kodlari: {', '.join(not_found)}"

            result = await self.odoo.create_quotation(
                partner_id=session.partner_id,
                lines=order_lines,
                notes=notes,
            )

            line_summary = "\n".join(
                f"- {l['urun_kodu']} x {_fmt_qty(l['quantity'])} adet"
                for l in lines
            )

            return FlowStepResult(
                message=(
                    f"Teklif talebiniz basariyla olusturuldu!\n"
                    f"- **Teklif No:** {result.order_ref}\n\n"
                    f"**Urunler:**\n{line_summary}\n\n"
                    f"Satis ekibimiz en kisa surede fiyat teklifinizi hazirlayacaktir."
                ),
                flow_completed=True,
                data={"order_id": result.order_id, "order_ref": result.order_ref},
            )
        except Exception as e:
            logger.error(
                "Quotation creation error (partner_id=%s): %s",
                session.partner_id, e, exc_info=True,
            )
            return FlowStepResult(
                message="Teklif talebi olusturulurken bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                flow_cancelled=True,
            )
