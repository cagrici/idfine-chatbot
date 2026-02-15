from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.products import router as products_router
from app.api.chat import router as chat_router
from app.api.customer import router as customer_router
from app.api.documents import router as documents_router
from app.api.canned_responses import router as canned_responses_router
from app.api.live_support import router as live_support_router
from app.api.reports import router as reports_router
from app.api.odoo_proxy import router as odoo_router
from app.api.source_groups import router as source_groups_router
from app.api.websocket import router as ws_router
from app.api.widget import router as widget_router
from app.api.widget_configs import router as widget_configs_router
from app.api.social_media import router as social_media_router
from app.meta.webhooks import router as meta_webhook_router
from app.odoo.webhooks import router as webhook_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(widget_router)
api_router.include_router(chat_router)
api_router.include_router(documents_router)
api_router.include_router(admin_router)
api_router.include_router(products_router)
api_router.include_router(widget_configs_router)
api_router.include_router(source_groups_router)
api_router.include_router(odoo_router)
api_router.include_router(webhook_router)
api_router.include_router(customer_router)
api_router.include_router(live_support_router)
api_router.include_router(canned_responses_router)
api_router.include_router(reports_router)
api_router.include_router(social_media_router)
api_router.include_router(meta_webhook_router)

# WebSocket routes are at root level (no /api prefix)
ws_router = ws_router
