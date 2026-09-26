import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from service_09261_001 import EvidenceChainService, SQLiteStore, dispatch, make_server

MATERIAL_STANDARD = {"id": "M001", "title": "义务教育数学课程标准", "kind": "课程标准",
                     "content": "2022年版课标原文", "registered_by": "张三"}
MATERIAL_SURVEY = {"id": "M002", "title": "三年级学情调研报告", "kind": "调研报告",
                   "content": "调研样本1200份", "registered_by": "李四"}
MATERIAL_EXPERT = {"id": "M003", "title": "专家评审意见", "kind": "专家意见",
                   "content": "建议强化估算教学", "registered_by": "王五"}
MATERIALS = (MATERIAL_STANDARD, MATERIAL_SURVEY, MATERIAL_EXPERT)


def make_service():
    return EvidenceChainService(SQLiteStore(":memory:"))


def seed_materials(service):
    for m in MATERIALS:
        service.register_material(m["id"], m["title"], m["kind"], m["content"],
                                  m["registered_by"])


def post(service, path, body):
    return dispatch(service, "POST", path, body)


def get(service, path):
    return dispatch(service, "GET", path)


class TestMaterials(unittest.TestCase):
    def setUp(self):
        self.service = make_service()

    def test_register_material_returns_fingerprint(self):
        status, body = post(self.service, "/materials", MATERIAL_STANDARD)
        self.assertEqual(status, 201)
        self.assertEqual(body["id"], "M001")
        self.assertEqual(len(body["sha256"]), 64)
        status, body = get(self.service, "/materials/M001")
        self.assertEqual(status, 200)
        self.assertEqual(body["title"], "义务教育数学课程标准")

    def test_invalid_kind_rejected(self):
        status, body = post(self.service, "/materials",
                            {**MATERIAL_STANDARD, "kind": "内部通知"})
        self.assertEqual(status, 400)

    def test_missing_field_rejected(self):
        status, _ = post(self.service, "/materials", {"id": "M009"})
        self.assertEqual(status, 400)

    def test_duplicate_material_conflict(self):
        self.assertEqual(post(self.service, "/materials", MATERIAL_STANDARD)[0], 201)
        status, _ = post(self.service, "/materials", MATERIAL_STANDARD)
        self.assertEqual(status, 409)

    def test_idempotent_registration(self):
        first = post(self.service, "/materials", {**MATERIAL_STANDARD, "idempotency_key": "reg-1"})
        second = post(self.service, "/materials", {**MATERIAL_STANDARD, "idempotency_key": "reg-1"})
        self.assertEqual(first, second)
        _, body = get(self.service, "/materials")
        self.assertEqual(len(body["materials"]), 1)


class TestConclusions(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        seed_materials(self.service)

    def generate(self, material_ids, key=None):
        body = {"id": "C001", "target": "三年级上册第三章第二节",
                "material_ids": material_ids, "created_by": "赵六"}
        if key:
            body["idempotency_key"] = key
        return post(self.service, "/conclusions", body)

    def test_generate_success(self):
        status, body = self.generate(["M001", "M002"])
        self.assertEqual(status, 201)
        self.assertEqual(body["version"], 1)
        self.assertEqual(len(body["digest"]), 64)
        self.assertIn("三年级上册第三章第二节", body["statement"])
        self.assertIn("义务教育数学课程标准", body["statement"])

    def test_rule_requires_two_materials(self):
        status, body = self.generate(["M001"])
        self.assertEqual(status, 422)
        self.assertIn("R1", body["message"])

    def test_rule_rejects_duplicate_reference(self):
        status, _ = self.generate(["M001", "M001"])
        self.assertEqual(status, 422)

    def test_rule_requires_standard(self):
        status, body = self.generate(["M002", "M003"])
        self.assertEqual(status, 422)
        self.assertIn("R3", body["message"])

    def test_unknown_material_not_found(self):
        status, _ = self.generate(["M001", "M999"])
        self.assertEqual(status, 404)

    def test_version_history_preserved(self):
        _, v1 = self.generate(["M001", "M002"])
        _, v2 = self.generate(["M001", "M002", "M003"])
        self.assertEqual((v1["version"], v2["version"]), (1, 2))
        self.assertNotEqual(v1["digest"], v2["digest"])
        status, body = get(self.service, "/conclusions/C001/versions/1")
        self.assertEqual(status, 200)
        self.assertEqual(body, v1)
        _, latest = get(self.service, "/conclusions/C001")
        self.assertEqual(latest["version"], 2)
        _, listing = get(self.service, "/conclusions")
        self.assertEqual([c["version"] for c in listing["conclusions"]], [2])

    def test_chain_verified(self):
        self.generate(["M001", "M002"])
        status, chain = get(self.service, "/conclusions/C001/versions/1/chain")
        self.assertEqual(status, 200)
        self.assertTrue(chain["verified"])
        self.assertEqual([e["id"] for e in chain["entries"]], ["M001", "M002"])
        self.assertTrue(all(e["verified"] for e in chain["entries"]))

    def test_idempotent_generation_creates_single_version(self):
        first = self.generate(["M001", "M002"], key="gen-1")
        second = self.generate(["M001", "M002"], key="gen-1")
        self.assertEqual(first, second)
        _, latest = get(self.service, "/conclusions/C001")
        self.assertEqual(latest["version"], 1)

    def test_unknown_route(self):
        self.assertEqual(get(self.service, "/nope")[0], 404)
        self.assertEqual(get(self.service, "/conclusions/C404")[0], 404)


class TestRestartPersistence(unittest.TestCase):
    """重启后仍能查询同一版本：存储层与 HTTP 层各验证一次。"""

    def test_store_restart_keeps_same_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "evidence.db")
            service = EvidenceChainService(SQLiteStore(path))
            seed_materials(service)
            v1 = service.generate_conclusion("C001", "第三章第二节", ["M001", "M002"], "赵六")
            service.generate_conclusion("C001", "第三章第二节", ["M001", "M002", "M003"], "赵六")
            service.store.close()

            reopened = EvidenceChainService(SQLiteStore(path))
            try:
                self.assertEqual(reopened.get_conclusion("C001", 1), v1)
                self.assertEqual(reopened.get_conclusion("C001")["version"], 2)
                self.assertTrue(reopened.get_chain("C001", 1)["verified"])
            finally:
                reopened.store.close()

    def test_http_restart_keeps_same_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "evidence.db")
            with running_server(path) as port:
                for m in MATERIALS:
                    status, _ = http_request(port, "POST", "/materials", m)
                    self.assertEqual(status, 201)
                status, v1 = http_request(port, "POST", "/conclusions", {
                    "id": "C001", "target": "第三章第二节",
                    "material_ids": ["M001", "M002"], "created_by": "赵六",
                    "idempotency_key": "gen-1"})
                self.assertEqual(status, 201)
                status, v2 = http_request(port, "POST", "/conclusions", {
                    "id": "C001", "target": "第三章第二节",
                    "material_ids": ["M001", "M002", "M003"], "created_by": "赵六",
                    "idempotency_key": "gen-2"})
                self.assertEqual((v1["version"], v2["version"]), (1, 2))

            # 进程级重启：关闭旧服务，用同一数据库文件启动新服务。
            with running_server(path) as port:
                status, body = http_request(port, "GET", "/conclusions/C001/versions/1")
                self.assertEqual(status, 200)
                self.assertEqual(body, v1)
                status, chain = http_request(port, "GET", "/conclusions/C001/versions/1/chain")
                self.assertEqual(status, 200)
                self.assertTrue(chain["verified"])
                _, latest = http_request(port, "GET", "/conclusions/C001")
                self.assertEqual(latest["version"], 2)
                # 幂等键同样持久化：重放旧请求不产生新版本。
                status, replay = http_request(port, "POST", "/conclusions", {
                    "id": "C001", "target": "第三章第二节",
                    "material_ids": ["M001", "M002"], "created_by": "赵六",
                    "idempotency_key": "gen-1"})
                self.assertEqual(status, 201)
                self.assertEqual(replay, v1)
                _, latest = http_request(port, "GET", "/conclusions/C001")
                self.assertEqual(latest["version"], 2)


def http_request(port, method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class running_server:
    def __init__(self, db_path):
        self.server = make_server(db_path, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.port

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
