"""HTTP 服务入口。

用法：python3 -m service_09261_001.server --db evidence_chain.db --port 8000
仅依赖标准库；每次写入即提交，进程重启后从同一 SQLite 文件恢复全部状态。
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .api import dispatch
from .store import SQLiteStore
from .workflow import EvidenceChain


def _make_handler(flow):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def _handle(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return self._reply(400, {"error": "bad_json", "message": "请求体不是合法 JSON"})
            path = self.path.split("?", 1)[0]
            status, payload = dispatch(flow, self.command, path, body)
            self._reply(status, payload)

        def _reply(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


def create_server(db_path, host="127.0.0.1", port=8000):
    """构建 HTTP 服务实例（未启动）；测试可传入端口 0 获取空闲端口。"""
    flow = EvidenceChain(SQLiteStore(db_path))
    return ThreadingHTTPServer((host, port), _make_handler(flow))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="evidence-chain", description="教材修订证据链服务")
    parser.add_argument("--db", default="evidence_chain.db", help="SQLite 数据库文件路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    httpd = create_server(args.db, args.host, args.port)
    print(f"listening on http://{args.host}:{httpd.server_address[1]}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
