import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.odoo.base_adapter import OdooAdapter
from app.schemas.odoo import (
    OrderStatusInfo,
    PriceInfo,
    ProductInfo,
    QuotationResponse,
    StockInfo,
)

logger = logging.getLogger(__name__)


class Json2Adapter(OdooAdapter):
    """Odoo JSON-2 API adapter for v19+."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._session_id: str | None = None

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["API-KEY"] = self.api_key
        return headers

    def _cookies(self) -> dict:
        if self._session_id:
            return {"session_id": self._session_id}
        return {}

    async def _request(
        self, model: str, method: str, params: dict | None = None
    ) -> Any:
        url = f"{self.url}/json/2/{model}/{method}"

        async with httpx.AsyncClient(
            timeout=30, verify=self.verify_ssl
        ) as client:
            response = await client.post(
                url,
                json=params or {},
                headers=self._headers(),
                cookies=self._cookies(),
            )

            if response.status_code >= 400:
                raise Exception(
                    f"Odoo JSON-2 error ({response.status_code}): {response.text}"
                )

            return response.json().get("result")

    async def authenticate(self) -> int:
        if self._session_id:
            # Already authenticated via session
            result = await self._request(
                "res.users", "search_read",
                {"domain": [["login", "=", self.username]], "fields": ["id"], "limit": 1},
            )
            return result[0]["id"] if result else 0

        if self.api_key:
            # API key auth - no session needed
            result = await self._request(
                "res.users", "search_read",
                {"domain": [["id", "=", 1]], "fields": ["id"], "limit": 1},
            )
            return result[0]["id"] if result else 0

        # Username/password auth via JSON-RPC session
        async with httpx.AsyncClient(
            timeout=30, verify=self.verify_ssl
        ) as client:
            response = await client.post(
                f"{self.url}/web/session/authenticate",
                json={
                    "jsonrpc": "2.0",
                    "params": {
                        "db": self.db,
                        "login": self.username,
                        "password": self.password,
                    },
                },
            )
            data = response.json()
            if "error" in data:
                raise Exception(f"Odoo auth failed: {data['error']}")

            result = data.get("result", {})
            self._session_id = response.cookies.get("session_id")
            return result.get("uid", 0)

    async def call(
        self, model: str, method: str, args: list, kwargs: Optional[dict] = None
    ) -> Any:
        params = {}
        if args:
            params["args"] = args
        if kwargs:
            params.update(kwargs)
        return await self._request(model, method, params)

    async def search_products(
        self, query: str, limit: int = 20
    ) -> list[ProductInfo]:
        result = await self._request(
            "product.product",
            "search_read",
            {
                "domain": [
                    "|",
                    ["name", "ilike", query],
                    ["default_code", "ilike", query],
                ],
                "fields": ["name", "default_code", "description_sale", "list_price", "categ_id"],
                "limit": limit,
            },
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
            for r in (result or [])
        ]

    async def get_stock(
        self, product_ids: list[int], warehouse_id: Optional[int] = None
    ) -> list[StockInfo]:
        domain = [["product_id", "in", product_ids]]
        if warehouse_id:
            domain.append(["warehouse_id", "=", warehouse_id])

        result = await self._request(
            "stock.quant",
            "search_read",
            {
                "domain": domain,
                "fields": ["product_id", "quantity", "warehouse_id"],
            },
        )

        stock_map: dict[int, float] = {}
        name_map: dict[int, str] = {}
        warehouse_map: dict[int, str] = {}

        for r in (result or []):
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
        result = await self._request(
            "product.product",
            "read",
            {
                "ids": product_ids,
                "fields": ["name", "list_price"],
            },
        )

        return [
            PriceInfo(
                product_id=r["id"],
                product_name=r["name"],
                list_price=r.get("list_price", 0),
            )
            for r in (result or [])
        ]

    async def get_order_status(self, order_ref: str) -> Optional[OrderStatusInfo]:
        result = await self._request(
            "sale.order",
            "search_read",
            {
                "domain": [["name", "=", order_ref]],
                "fields": [
                    "name", "state", "partner_id", "date_order",
                    "amount_total", "currency_id", "invoice_status",
                ],
                "limit": 1,
            },
        )

        if not result:
            return None

        r = result[0]
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
            line_vals = {
                "product_id": line["product_id"],
                "product_uom_qty": line["quantity"],
            }
            if line.get("unit_price"):
                line_vals["price_unit"] = line["unit_price"]
            order_lines.append((0, 0, line_vals))

        vals = {
            "partner_id": partner_id,
            "order_line": order_lines,
        }
        if notes:
            vals["note"] = notes

        order_id = await self._request(
            "sale.order", "create", {"values": vals}
        )

        order_data = await self._request(
            "sale.order",
            "read",
            {"ids": [order_id], "fields": ["name", "amount_total", "state"]},
        )

        r = order_data[0] if order_data else {}
        return QuotationResponse(
            order_id=order_id,
            order_ref=r.get("name", ""),
            amount_total=r.get("amount_total", 0),
            status=r.get("state", "draft"),
            message="Teklif başarıyla oluşturuldu",
        )
