"""HTTP 服务入口。

用法: python3 -m service_09261_001.server [数据库路径] [端口]
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .api import dispatch
from .store import SQLiteStore
from .workflow import EvidenceChainService


class EvidenceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, store):
        super().__init__(address, handler)
        self.store = store

    def server_close(self):
        super().server_close()
        self.store.close()


def _make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if raw:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return self._reply(400, {"error": "bad_json", "message": "请求体须为 JSON"})
            else:
                payload = None
            status, body = dispatch(service, self.command, self.path, payload)
            self._reply(status, body)

        def _reply(self, status, body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


def make_server(db_path, host="127.0.0.1", port=8000):
    """基于同一 SQLite 文件构建服务；用同一文件再次调用即完成“重启”。"""
    store = SQLiteStore(db_path)
    service = EvidenceChainService(store)
    return EvidenceServer((host, port), _make_handler(service), store)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = argv[0] if len(argv) > 0 else "evidence.db"
    port = int(argv[1]) if len(argv) > 1 else 8000
    server = make_server(db_path, port=port)
    print(f"教材修订证据链服务已启动: http://127.0.0.1:{port} 数据库: {db_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
