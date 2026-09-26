"""教材修订证据链服务端包。"""
PROJECT_CODE = "service_09261_001"

from .api import dispatch
from .store import SQLiteStore
from .workflow import EvidenceChainService

__all__ = [
    "PROJECT_CODE",
    "EvidenceChainService",
    "SQLiteStore",
    "dispatch",
    "make_server",
]


def __getattr__(name):
    # 惰性导入，避免 python -m service_09261_001.server 时重复加载模块。
    if name == "make_server":
        from .server import make_server
        return make_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
