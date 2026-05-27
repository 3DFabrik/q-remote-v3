"""Q-Remote V3 – ASGI entry point.

Wraps FastAPI with SocketIO ASGI app so both share the same port.
SocketIO handles its paths, everything else goes to FastAPI.

Usage:
    uvicorn backend.main:asgi_app --host 0.0.0.0 --port 8080
"""

from backend.app import asgi_app

__all__ = ['asgi_app']
