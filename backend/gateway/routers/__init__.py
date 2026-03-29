from .permissions import router as permissions_router
from .notifications import router as notifications_router
from .payments import router as payments_router
from .esim import router as esim_router
from .esim_app import router as esim_app_router
from .flights import router as flights_router
from backend.communications.Telegram.router import router as telegram_support_router

__all__ = [
    "permissions_router",
    "notifications_router",
    "payments_router",
    "esim_router",
    "esim_app_router",
    "flights_router",
    "telegram_support_router",
]
