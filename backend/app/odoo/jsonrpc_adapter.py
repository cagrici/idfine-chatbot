import base64
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.odoo.base_adapter import OdooAdapter

settings = get_settings()
from app.schemas.odoo import (
    DeliveryDetail,
    DeliveryLineDetail,
    DeliverySummary,
    InvoiceDetail,
    InvoiceLineDetail,
    InvoiceSummary,
    OrderDetail,
    OrderLineDetail,
    OrderStatusInfo,
    OrderSummary,
    PartnerInfo,
    PaymentInfo,
    PriceInfo,
    ProductInfo,
    QuotationResponse,
    StockInfo,
    TicketSummary,
)

logger = logging.getLogger(__name__)


class JsonRpcAdapter(OdooAdapter):
    """Odoo JSON-RPC adapter for v17/v18."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._uid: int | None = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    @property
    def _auth_credential(self) -> str:
        """Return the credential used for RPC calls (password or API key)."""
        if self.api_key:
            return self.api_key
        return self.password

    @property
    def _auth_login(self) -> str:
        """Return the login used for authentication."""
        if self.api_key:
            return "__api_key__"
        return self.username

    async def _jsonrpc(
        self, endpoint: str, method: str, params: dict
    ) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }

        async with httpx.AsyncClient(timeout=30, verify=self.verify_ssl) as client:
            response = await client.post(
                f"{self.url}{endpoint}",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()

        if "error" in result:
            error = result["error"]
            msg = error.get("data", {}).get("message", str(error))
            raise Exception(f"Odoo JSON-RPC error: {msg}")

        return result.get("result")

    async def authenticate(self) -> int:
        if self._uid:
            return self._uid

        result = await self._jsonrpc(
            "/jsonrpc",
            "call",
            {
                "service": "common",
                "method": "authenticate",
                "args": [self.db, self._auth_login, self._auth_credential, {}],
            },
        )

        if not result:
            raise Exception(
                f"Odoo authentication failed for {self._auth_login}@{self.db}"
            )

        self._uid = result
        logger.info("Odoo authenticated: uid=%d, db=%s", self._uid, self.db)
        return self._uid

    async def call(
        self, model: str, method: str, args: list, kwargs: Optional[dict] = None
    ) -> Any:
        uid = await self.authenticate()
        return await self._jsonrpc(
            "/jsonrpc",
            "call",
            {
                "service": "object",
                "method": "execute_kw",
                "args": [
                    self.db, uid, self._auth_credential,
                    model, method, args, kwargs or {},
                ],
            },
        )

    async def search_products(
        self, query: str, limit: int = 20
    ) -> list[ProductInfo]:
        domain = [
            "|",
            ["name", "ilike", query],
            ["default_code", "ilike", query],
        ]
        fields = ["name", "default_code", "description_sale", "list_price", "categ_id"]

        records = await self.call(
            "product.product",
            "search_read",
            [domain],
            {"fields": fields, "limit": limit},
        )

        return [
            ProductInfo(
                id=r["id"],
                name=r["name"],
                default_code=r.get("default_code") or None,
                description=r.get("description_sale") or None,
                list_price=r.get("list_price", 0),
                category=r["categ_id"][1] if r.get("categ_id") else None,
            )
            for r in records
        ]

    async def get_stock(
        self, product_ids: list[int], warehouse_id: Optional[int] = None
    ) -> list[StockInfo]:
        domain = [["product_id", "in", product_ids]]
        if warehouse_id:
            domain.append(["warehouse_id", "=", warehouse_id])

        fields = ["product_id", "quantity", "warehouse_id"]
        records = await self.call(
            "stock.quant",
            "search_read",
            [domain],
            {"fields": fields},
        )

        # Aggregate by product
        stock_map: dict[int, float] = {}
        name_map: dict[int, str] = {}
        warehouse_map: dict[int, str] = {}

        for r in records:
            pid = r["product_id"][0]
            stock_map[pid] = stock_map.get(pid, 0) + r.get("quantity", 0)
            name_map[pid] = r["product_id"][1]
            if r.get("warehouse_id"):
                warehouse_map[pid] = r["warehouse_id"][1]

        return [
            StockInfo(
                product_id=pid,
                product_name=name_map.get(pid, ""),
                qty_available=qty,
                warehouse=warehouse_map.get(pid),
                last_updated=datetime.now(timezone.utc),
            )
            for pid, qty in stock_map.items()
        ]

    async def get_prices(
        self, product_ids: list[int], pricelist_id: Optional[int] = None
    ) -> list[PriceInfo]:
        fields = ["name", "list_price"]
        records = await self.call(
            "product.product",
            "read",
            [product_ids],
            {"fields": fields},
        )

        return [
            PriceInfo(
                product_id=r["id"],
                product_name=r["name"],
                list_price=r.get("list_price", 0),
            )
            for r in records
        ]

    async def get_order_status(self, order_ref: str) -> Optional[OrderStatusInfo]:
        domain = [["name", "=", order_ref]]
        fields = [
            "name", "state", "partner_id", "date_order",
            "amount_total", "currency_id", "invoice_status",
        ]

        records = await self.call(
            "sale.order",
            "search_read",
            [domain],
            {"fields": fields, "limit": 1},
        )

        if not records:
            return None

        r = records[0]
        return OrderStatusInfo(
            order_ref=r["name"],
            state=r.get("state", ""),
            partner_name=r["partner_id"][1] if r.get("partner_id") else "",
            date_order=r.get("date_order", datetime.now(timezone.utc)),
            amount_total=r.get("amount_total", 0),
            currency=r["currency_id"][1] if r.get("currency_id") else "TRY",
            invoice_status=r.get("invoice_status"),
        )

    async def create_quotation(
        self,
        partner_id: int,
        lines: list[dict],
        notes: Optional[str] = None,
    ) -> QuotationResponse:
        order_lines = []
        for line in lines:
            order_lines.append(
                (0, 0, {
                    "product_id": line["product_id"],
                    "product_uom_qty": line["quantity"],
                    **({"price_unit": line["unit_price"]} if line.get("unit_price") else {}),
                })
            )

        # Resolve warehouse_id (mandatory in Odoo 17+)
        warehouse_id = settings.odoo_warehouse_id
        if not warehouse_id:
            warehouses = await self.call(
                "stock.warehouse", "search_read",
                [[["active", "=", True]]],
                {"fields": ["id"], "limit": 1, "order": "id asc"},
            )
            warehouse_id = warehouses[0]["id"] if warehouses else 1

        vals = {
            "partner_id": partner_id,
            "warehouse_id": warehouse_id,
        }
        if settings.odoo_analytic_account_id:
            vals["analytic_account_id"] = settings.odoo_analytic_account_id
        if order_lines:
            vals["order_line"] = order_lines
        if notes:
            vals["note"] = notes

        order_id = await self.call("sale.order", "create", [vals])

        # Odoo create() returns an int; wrap in list for read()
        order_id_int = order_id if isinstance(order_id, int) else order_id[0]

        # Read back the created order
        order_data = await self.call(
            "sale.order",
            "read",
            [[order_id_int]],
            {"fields": ["name", "amount_total", "state"]},
        )

        if isinstance(order_data, list):
            order_data = order_data[0]

        return QuotationResponse(
            order_id=order_id if isinstance(order_id, int) else order_id[0],
            order_ref=order_data.get("name", ""),
            amount_total=order_data.get("amount_total", 0),
            status=order_data.get("state", "draft"),
            message="Teklif başarıyla oluşturuldu",
        )

    # --- Customer / Partner methods ---

    async def search_partner_by_email(self, email: str) -> Optional[PartnerInfo]:
        records = await self.call(
            "res.partner", "search_read",
            [[["email", "=ilike", email], ["customer_rank", ">", 0]]],
            {"fields": [
                "name", "email", "phone", "mobile", "street", "street2",
                "city", "state_id", "zip", "country_id", "vat",
                "company_name", "customer_rank",
            ], "limit": 1},
        )
        if not records:
            return None
        return self._to_partner_info(records[0])

    async def get_partner(self, partner_id: int) -> Optional[PartnerInfo]:
        records = await self.call(
            "res.partner", "read", [[partner_id]],
            {"fields": [
                "name", "email", "phone", "mobile", "street", "street2",
                "city", "state_id", "zip", "country_id", "vat",
                "company_name", "customer_rank",
            ]},
        )
        if not records:
            return None
        r = records[0] if isinstance(records, list) else records
        return self._to_partner_info(r)

    async def update_partner(self, partner_id: int, vals: dict) -> bool:
        # Whitelist allowed fields
        allowed = {"phone", "mobile", "street", "street2", "city", "zip", "email"}
        safe_vals = {k: v for k, v in vals.items() if k in allowed}
        if not safe_vals:
            return False
        await self.call("res.partner", "write", [[partner_id], safe_vals])
        return True

    def _to_partner_info(self, r: dict) -> PartnerInfo:
        return PartnerInfo(
            id=r["id"],
            name=r.get("name", ""),
            email=r.get("email") or None,
            phone=r.get("phone") or None,
            mobile=r.get("mobile") or None,
            street=r.get("street") or None,
            street2=r.get("street2") or None,
            city=r.get("city") or None,
            state=r["state_id"][1] if r.get("state_id") else None,
            zip=r.get("zip") or None,
            country=r["country_id"][1] if r.get("country_id") else None,
            vat=r.get("vat") or None,
            company_name=r.get("company_name") or None,
            customer_rank=r.get("customer_rank", 0),
        )

    # --- Order methods ---

    async def get_partner_orders(
        self, partner_id: int, limit: int = 20, states: Optional[list[str]] = None
    ) -> list[OrderSummary]:
        domain = [["partner_id", "=", partner_id]]
        if states:
            domain.append(["state", "in", states])

        records = await self.call(
            "sale.order", "search_read", [domain],
            {"fields": [
                "name", "state", "date_order", "amount_total",
                "currency_id", "invoice_status",
            ], "limit": limit, "order": "date_order desc"},
        )

        return [
            OrderSummary(
                id=r["id"],
                name=r["name"],
                state=r.get("state", ""),
                date_order=str(r["date_order"]) if r.get("date_order") else None,
                amount_total=r.get("amount_total", 0),
                currency=r["currency_id"][1] if r.get("currency_id") else "TRY",
                invoice_status=r.get("invoice_status"),
            )
            for r in records
        ]

    async def get_order_details(self, order_id: int, partner_id: int) -> Optional[OrderDetail]:
        # Verify ownership
        records = await self.call(
            "sale.order", "search_read",
            [[["id", "=", order_id], ["partner_id", "=", partner_id]]],
            {"fields": [
                "name", "state", "date_order", "amount_untaxed", "amount_tax",
                "amount_total", "currency_id", "invoice_status", "note",
                "order_line",
            ], "limit": 1},
        )
        if not records:
            return None

        r = records[0]
        lines = []
        if r.get("order_line"):
            line_records = await self.call(
                "sale.order.line", "read", [r["order_line"]],
                {"fields": [
                    "product_id", "name", "product_uom_qty", "price_unit",
                    "price_subtotal", "product_uom",
                ]},
            )
            for lr in line_records:
                lines.append(OrderLineDetail(
                    id=lr["id"],
                    product_name=lr.get("name", ""),
                    product_code=None,
                    quantity=lr.get("product_uom_qty", 0),
                    price_unit=lr.get("price_unit", 0),
                    price_subtotal=lr.get("price_subtotal", 0),
                    product_uom=lr["product_uom"][1] if lr.get("product_uom") else None,
                ))

        return OrderDetail(
            id=r["id"],
            name=r["name"],
            state=r.get("state", ""),
            date_order=str(r["date_order"]) if r.get("date_order") else None,
            amount_untaxed=r.get("amount_untaxed", 0),
            amount_tax=r.get("amount_tax", 0),
            amount_total=r.get("amount_total", 0),
            currency=r["currency_id"][1] if r.get("currency_id") else "TRY",
            invoice_status=r.get("invoice_status"),
            note=r.get("note") or None,
            lines=lines,
        )

    # --- Invoice methods ---

    async def get_partner_invoices(self, partner_id: int, limit: int = 20) -> list[InvoiceSummary]:
        records = await self.call(
            "account.move", "search_read",
            [[
                ["partner_id", "=", partner_id],
                ["move_type", "in", ["out_invoice", "out_refund"]],
                ["state", "!=", "draft"],
            ]],
            {"fields": [
                "name", "state", "move_type", "date", "invoice_date_due",
                "amount_total", "amount_residual", "currency_id", "payment_state",
            ], "limit": limit, "order": "date desc"},
        )

        return [
            InvoiceSummary(
                id=r["id"],
                name=r["name"],
                state=r.get("state", ""),
                move_type=r.get("move_type", ""),
                date=str(r["date"]) if r.get("date") else None,
                invoice_date_due=str(r["invoice_date_due"]) if r.get("invoice_date_due") else None,
                amount_total=r.get("amount_total", 0),
                amount_residual=r.get("amount_residual", 0),
                currency=r["currency_id"][1] if r.get("currency_id") else "TRY",
                payment_state=r.get("payment_state"),
            )
            for r in records
        ]

    async def get_invoice_details(self, invoice_id: int, partner_id: int) -> Optional[InvoiceDetail]:
        records = await self.call(
            "account.move", "search_read",
            [[["id", "=", invoice_id], ["partner_id", "=", partner_id]]],
            {"fields": [
                "name", "state", "move_type", "date", "invoice_date_due",
                "amount_untaxed", "amount_tax", "amount_total", "amount_residual",
                "currency_id", "payment_state", "invoice_line_ids",
            ], "limit": 1},
        )
        if not records:
            return None

        r = records[0]
        lines = []
        if r.get("invoice_line_ids"):
            line_records = await self.call(
                "account.move.line", "read", [r["invoice_line_ids"]],
                {"fields": ["product_id", "name", "quantity", "price_unit", "price_subtotal"]},
            )
            for lr in line_records:
                if lr.get("price_subtotal", 0) == 0 and not lr.get("product_id"):
                    continue  # Skip tax/total lines
                lines.append(InvoiceLineDetail(
                    id=lr["id"],
                    product_name=lr.get("name") or (lr["product_id"][1] if lr.get("product_id") else None),
                    quantity=lr.get("quantity", 0),
                    price_unit=lr.get("price_unit", 0),
                    price_subtotal=lr.get("price_subtotal", 0),
                ))

        return InvoiceDetail(
            id=r["id"],
            name=r["name"],
            state=r.get("state", ""),
            move_type=r.get("move_type", ""),
            date=str(r["date"]) if r.get("date") else None,
            invoice_date_due=str(r["invoice_date_due"]) if r.get("invoice_date_due") else None,
            amount_untaxed=r.get("amount_untaxed", 0),
            amount_tax=r.get("amount_tax", 0),
            amount_total=r.get("amount_total", 0),
            amount_residual=r.get("amount_residual", 0),
            currency=r["currency_id"][1] if r.get("currency_id") else "TRY",
            payment_state=r.get("payment_state"),
            lines=lines,
        )

    async def get_invoice_pdf(self, invoice_id: int, partner_id: int) -> Optional[bytes]:
        # Verify ownership
        records = await self.call(
            "account.move", "search_read",
            [[["id", "=", invoice_id], ["partner_id", "=", partner_id]]],
            {"fields": ["id"], "limit": 1},
        )
        if not records:
            return None

        # Get PDF via report
        try:
            result = await self.call(
                "ir.actions.report", "render_qweb_pdf",
                ["account.report_invoice", [invoice_id]],
            )
            if result and isinstance(result, list) and len(result) > 0:
                pdf_data = result[0]
                if isinstance(pdf_data, str):
                    return base64.b64decode(pdf_data)
                return pdf_data
        except Exception as e:
            logger.error("Failed to get invoice PDF: %s", e)
        return None

    async def get_partner_payments(self, partner_id: int, limit: int = 20) -> list[PaymentInfo]:
        records = await self.call(
            "account.payment", "search_read",
            [[["partner_id", "=", partner_id], ["state", "!=", "draft"]]],
            {"fields": [
                "name", "date", "amount", "currency_id", "state", "payment_type",
            ], "limit": limit, "order": "date desc"},
        )

        return [
            PaymentInfo(
                id=r["id"],
                name=r.get("name", ""),
                date=str(r["date"]) if r.get("date") else None,
                amount=r.get("amount", 0),
                currency=r["currency_id"][1] if r.get("currency_id") else "TRY",
                state=r.get("state", ""),
                payment_type=r.get("payment_type", ""),
            )
            for r in records
        ]

    # --- Delivery methods ---

    async def get_partner_deliveries(self, partner_id: int, limit: int = 20) -> list[DeliverySummary]:
        records = await self.call(
            "stock.picking", "search_read",
            [[
                ["partner_id", "=", partner_id],
                ["picking_type_code", "=", "outgoing"],
            ]],
            {"fields": [
                "name", "state", "origin", "scheduled_date", "date_done",
                "carrier_id", "carrier_tracking_ref",
            ], "limit": limit, "order": "scheduled_date desc"},
        )

        return [
            DeliverySummary(
                id=r["id"],
                name=r["name"],
                state=r.get("state", ""),
                origin=r.get("origin") or None,
                scheduled_date=str(r["scheduled_date"]) if r.get("scheduled_date") else None,
                date_done=str(r["date_done"]) if r.get("date_done") else None,
                carrier=r["carrier_id"][1] if r.get("carrier_id") else None,
                tracking_ref=r.get("carrier_tracking_ref") or None,
            )
            for r in records
        ]

    async def get_delivery_details(self, picking_id: int, partner_id: int) -> Optional[DeliveryDetail]:
        records = await self.call(
            "stock.picking", "search_read",
            [[["id", "=", picking_id], ["partner_id", "=", partner_id]]],
            {"fields": [
                "name", "state", "origin", "scheduled_date", "date_done",
                "carrier_id", "carrier_tracking_ref", "move_ids_without_package",
            ], "limit": 1},
        )
        if not records:
            return None

        r = records[0]
        lines = []
        move_ids = r.get("move_ids_without_package", [])
        if move_ids:
            move_records = await self.call(
                "stock.move", "read", [move_ids],
                {"fields": ["product_id", "quantity_done", "product_uom"]},
            )
            for mr in move_records:
                lines.append(DeliveryLineDetail(
                    id=mr["id"],
                    product_name=mr["product_id"][1] if mr.get("product_id") else "",
                    quantity_done=mr.get("quantity_done", 0),
                    product_uom=mr["product_uom"][1] if mr.get("product_uom") else None,
                ))

        return DeliveryDetail(
            id=r["id"],
            name=r["name"],
            state=r.get("state", ""),
            origin=r.get("origin") or None,
            scheduled_date=str(r["scheduled_date"]) if r.get("scheduled_date") else None,
            date_done=str(r["date_done"]) if r.get("date_done") else None,
            carrier=r["carrier_id"][1] if r.get("carrier_id") else None,
            tracking_ref=r.get("carrier_tracking_ref") or None,
            lines=lines,
        )

    # --- Support ticket methods ---

    async def create_ticket(
        self, partner_id: int, subject: str, description: str, priority: str = "1"
    ) -> Optional[int]:
        try:
            ticket_id = await self.call(
                "helpdesk.ticket", "create", [{
                    "name": subject,
                    "description": description,
                    "partner_id": partner_id,
                    "priority": priority,
                }],
            )
            return ticket_id
        except Exception as e:
            logger.error("Failed to create ticket: %s", e)
            return None

    async def get_partner_tickets(self, partner_id: int, limit: int = 20) -> list[TicketSummary]:
        try:
            records = await self.call(
                "helpdesk.ticket", "search_read",
                [[["partner_id", "=", partner_id]]],
                {"fields": [
                    "name", "stage_id", "priority", "create_date", "description",
                ], "limit": limit, "order": "create_date desc"},
            )
        except Exception:
            logger.warning("helpdesk.ticket module may not be installed")
            return []

        return [
            TicketSummary(
                id=r["id"],
                name=r.get("name", ""),
                stage=r["stage_id"][1] if r.get("stage_id") else None,
                priority=r.get("priority"),
                create_date=str(r["create_date"]) if r.get("create_date") else None,
                description=r.get("description") or None,
            )
            for r in records
        ]

    async def add_ticket_message(self, ticket_id: int, partner_id: int, body: str) -> bool:
        try:
            await self.call(
                "helpdesk.ticket", "message_post",
                [[ticket_id]],
                {"body": body, "message_type": "comment"},
            )
            return True
        except Exception as e:
            logger.error("Failed to add ticket message: %s", e)
            return False

    # --- Order cancellation ---

    async def request_order_cancellation(
        self, order_id: int, partner_id: int, reason: str
    ) -> bool:
        """Request order cancellation by posting a note and setting to cancel (if draft/sent)."""
        # Verify ownership
        records = await self.call(
            "sale.order", "search_read",
            [[["id", "=", order_id], ["partner_id", "=", partner_id]]],
            {"fields": ["state"], "limit": 1},
        )
        if not records:
            return False

        state = records[0].get("state", "")
        # Post cancellation reason as internal note
        try:
            await self.call(
                "sale.order", "message_post",
                [[order_id]],
                {"body": f"Musteri iptal talebi: {reason}", "message_type": "comment"},
            )
        except Exception as e:
            logger.warning("Failed to post cancellation note: %s", e)

        # Only cancel if in draft or sent state
        if state in ("draft", "sent"):
            try:
                await self.call("sale.order", "action_cancel", [[order_id]])
                return True
            except Exception as e:
                logger.error("Failed to cancel order: %s", e)
                return False

        # For confirmed orders, just the note is sufficient (manual review needed)
        return True

    # --- Email ---

    async def send_email(self, to: str, subject: str, body_html: str, email_from: str = "") -> int:
        mail_vals = {
            "subject": subject,
            "body_html": body_html,
            "email_to": to,
            "auto_delete": True,
        }
        if email_from:
            mail_vals["email_from"] = email_from

        mail_id = await self.call("mail.mail", "create", [mail_vals])
        await self.call("mail.mail", "send", [[mail_id]])
        return mail_id
