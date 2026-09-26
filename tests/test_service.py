"""教材修订证据链测试：领域规则、证据链完整性、HTTP 接口与重启持久化。"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from service_09261_001.server import create_server
from service_09261_001.store import SQLiteStore
from service_09261_001.workflow import EvidenceChain, DomainError, GENESIS_HASH

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def new_flow():
    return EvidenceChain(SQLiteStore(":memory:"))


def register_and_verify(flow, mid, key=None):
    flow.register_material(mid, "勘误记录", "标题-" + mid, "内容-" + mid, "张三", key)
    return flow.verify_material(mid, "李四", True)


def http(port, method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestMaterialRules(unittest.TestCase):
    def setUp(self):
        self.flow = new_flow()

    def test_register_then_verify(self):
        m = self.flow.register_material("m1", "勘误记录", "第3页错别字", "“即”应为“既”", "张三")
        self.assertEqual(m.state, "registered")
        self.assertEqual(len(m.material_hash), 64)
        v = self.flow.verify_material("m1", "李四", True)
        self.assertEqual(v.state, "verified")
        self.assertEqual(v.verified_by, "李四")

    def test_cannot_verify_twice(self):
        register_and_verify(self.flow, "m1")
        with self.assertRaises(DomainError) as ctx:
            self.flow.verify_material("m1", "王五", True)
        self.assertEqual(ctx.exception.code, "invalid_state")

    def test_unknown_material_is_not_found(self):
        with self.assertRaises(DomainError) as ctx:
            self.flow.verify_material("ghost", "李四", True)
        self.assertEqual(ctx.exception.code, "not_found")

    def test_missing_fields_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            self.flow.register_material("m1", "", "t", "c", "张三")
        self.assertEqual(ctx.exception.code, "rule_violation")

    def test_duplicate_id_rejected(self):
        self.flow.register_material("m1", "k", "t", "c", "张三")
        with self.assertRaises(DomainError) as ctx:
            self.flow.register_material("m1", "k", "t", "c2", "张三")
        self.assertEqual(ctx.exception.code, "duplicate")

    def test_idempotent_register_replays_same_record(self):
        a = self.flow.register_material("m1", "k", "t", "c", "张三", "key-1")
        b = self.flow.register_material("m1", "k", "t", "c", "张三", "key-1")
        self.assertEqual(a, b)
        self.assertEqual(len(self.flow.list_materials()), 1)


class TestConclusionRules(unittest.TestCase):
    def setUp(self):
        self.flow = new_flow()
        register_and_verify(self.flow, "m1")
        register_and_verify(self.flow, "m2")

    def test_cannot_cite_unverified_material(self):
        self.flow.register_material("m3", "会议纪要", "t", "c", "张三")  # 未核验
        with self.assertRaises(DomainError) as ctx:
            self.flow.generate_conclusion("c1", "应修订", ["m3"], "王五")
        self.assertEqual(ctx.exception.code, "rule_violation")

    def test_cannot_cite_missing_material(self):
        with self.assertRaises(DomainError) as ctx:
            self.flow.generate_conclusion("c1", "应修订", ["ghost"], "王五")
        self.assertEqual(ctx.exception.code, "rule_violation")

    def test_requires_at_least_one_material(self):
        with self.assertRaises(DomainError):
            self.flow.generate_conclusion("c1", "应修订", [], "王五")

    def test_versions_chain_and_old_version_queryable(self):
        c1 = self.flow.generate_conclusion("c1", "第3页“即”改“既”", ["m1"], "王五")
        self.assertEqual(c1.version, 1)
        self.assertEqual(c1.prev_chain_hash, GENESIS_HASH)
        c2 = self.flow.revise_conclusion("c1", "第3页改字，第5页更新图表", ["m1", "m2"], "王五")
        self.assertEqual(c2.version, 2)
        self.assertEqual(c2.prev_chain_hash, c1.chain_hash)
        old = self.flow.get_conclusion("c1", 1)
        self.assertEqual(old.content, "第3页“即”改“既”")
        self.assertEqual(old.chain_hash, c1.chain_hash)
        self.assertEqual(self.flow.get_conclusion("c1").version, 2)

    def test_duplicate_conclusion_id_rejected(self):
        self.flow.generate_conclusion("c1", "x", ["m1"], "王五")
        with self.assertRaises(DomainError) as ctx:
            self.flow.generate_conclusion("c1", "y", ["m1"], "王五")
        self.assertEqual(ctx.exception.code, "duplicate")

    def test_revise_unknown_conclusion_is_not_found(self):
        with self.assertRaises(DomainError) as ctx:
            self.flow.revise_conclusion("ghost", "x", ["m1"], "王五")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_idempotent_conclusion_replays_same_version(self):
        a = self.flow.generate_conclusion("c1", "x", ["m1"], "王五", "ck-1")
        b = self.flow.generate_conclusion("c1", "x", ["m1"], "王五", "ck-1")
        self.assertEqual(a.chain_hash, b.chain_hash)
        self.assertEqual(len(self.flow.list_conclusions()), 1)

    def test_chain_verify_and_tamper_detection(self):
        self.flow.generate_conclusion("c1", "结论一", ["m1"], "王五")
        self.flow.generate_conclusion("c2", "结论二", ["m1", "m2"], "王五")
        ok = self.flow.verify_chain()
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["checked"], 2)
        # 直接篡改数据库中的材料内容 → 证据链校验必须失败
        self.flow.store.db.execute("UPDATE materials SET content='篡改内容' WHERE material_id='m1'")
        self.flow.store.db.commit()
        self.assertFalse(self.flow.verify_chain()["ok"])
        self.assertFalse(self.flow.trace("c1")["chain_ok"])


class TestPersistenceAcrossRestart(unittest.TestCase):
    def test_same_version_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "chain.db")
            flow1 = EvidenceChain(SQLiteStore(path))
            register_and_verify(flow1, "m1")
            c1 = flow1.generate_conclusion("c1", "结论", ["m1"], "王五")
            flow1.store.close()
            # 模拟重启：全新连接读取同一数据库文件
            flow2 = EvidenceChain(SQLiteStore(path))
            again = flow2.get_conclusion("c1", 1)
            self.assertEqual(again.chain_hash, c1.chain_hash)
            self.assertEqual(again.content, c1.content)
            self.assertEqual(again.material_ids, ("m1",))
            self.assertTrue(flow2.verify_chain()["ok"])
            flow2.store.close()


class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.httpd = create_server(os.path.join(cls.tmp.name, "api.db"), "127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def test_full_flow_over_http(self):
        s, m = http(self.port, "POST", "/materials",
                    {"material_id": "m1", "kind": "勘误记录", "title": "t", "content": "c", "actor": "张三"})
        self.assertEqual(s, 201)
        self.assertEqual(m["state"], "registered")
        s, v = http(self.port, "POST", "/materials/m1/verify", {"actor": "李四", "approved": True})
        self.assertEqual(s, 200)
        self.assertEqual(v["state"], "verified")
        s, c = http(self.port, "POST", "/conclusions",
                    {"conclusion_id": "c1", "content": "结论", "material_ids": ["m1"], "actor": "王五"})
        self.assertEqual(s, 201)
        self.assertEqual(c["version"], 1)
        s, v1 = http(self.port, "GET", "/conclusions/c1/versions/1")
        self.assertEqual(s, 200)
        self.assertEqual(v1["chain_hash"], c["chain_hash"])
        s, tr = http(self.port, "GET", "/conclusions/c1/chain")
        self.assertEqual(s, 200)
        self.assertTrue(tr["chain_ok"])
        s, ver = http(self.port, "GET", "/verify")
        self.assertTrue(ver["ok"])

    def test_error_mapping(self):
        s, e = http(self.port, "GET", "/nope")
        self.assertEqual(s, 404)
        s, e = http(self.port, "POST", "/conclusions",
                    {"conclusion_id": "bad", "content": "x", "material_ids": [], "actor": "王五"})
        self.assertEqual(s, 400)
        self.assertEqual(e["error"], "rule_violation")
        s, e = http(self.port, "GET", "/conclusions/ghost")
        self.assertEqual(s, 404)
        self.assertEqual(e["error"], "not_found")


class TestHttpRestart(unittest.TestCase):
    """核心验收：真实进程重启后，同一结论版本仍可查询且哈希一致。"""

    def _start(self, db_path):
        port = free_port()
        proc = subprocess.Popen(
            [sys.executable, "-m", "service_09261_001.server", "--db", db_path, "--port", str(port)],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = proc.stdout.readline()
        if "listening" not in line:
            proc.terminate()
            self.fail(f"服务进程未正常启动: {proc.stderr.read()}")
        return proc, port

    def _stop(self, proc):
        proc.terminate()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()

    def test_restart_still_serves_same_version(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "restart.db")
            proc1, port1 = self._start(db)
            try:
                s, _ = http(port1, "POST", "/materials",
                            {"material_id": "m1", "kind": "专家意见", "title": "t",
                             "content": "c", "actor": "张三"})
                self.assertEqual(s, 201)
                s, _ = http(port1, "POST", "/materials/m1/verify", {"actor": "李四", "approved": True})
                self.assertEqual(s, 200)
                s, c = http(port1, "POST", "/conclusions",
                            {"conclusion_id": "c1", "content": "修订结论 v1",
                             "material_ids": ["m1"], "actor": "王五"})
                self.assertEqual(s, 201)
            finally:
                self._stop(proc1)
            # 重启：全新进程加载同一 SQLite 文件
            proc2, port2 = self._start(db)
            try:
                s, v1 = http(port2, "GET", "/conclusions/c1/versions/1")
                self.assertEqual(s, 200)
                self.assertEqual(v1["chain_hash"], c["chain_hash"])
                self.assertEqual(v1["content"], "修订结论 v1")
                self.assertEqual(v1["material_ids"], ["m1"])
                s, ver = http(port2, "GET", "/verify")
                self.assertTrue(ver["ok"])
            finally:
                self._stop(proc2)


if __name__ == "__main__":
    unittest.main()
