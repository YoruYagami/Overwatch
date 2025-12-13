from .machines import router as machines_router
from .vpn import router as vpn_router
from .vouchers import router as vouchers_router
from .admin import router as admin_router

__all__ = ["machines_router", "vpn_router", "vouchers_router", "admin_router"]
