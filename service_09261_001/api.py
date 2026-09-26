"""JSON API 适配器：把 HTTP 语义映射到领域工作流，统一错误格式。"""
from dataclasses import asdict

from .workflow import DomainError


def _segments(path):
    return [s for s in path.split("/") if s]


def dispatch(flow, method, path, body=None):
    """返回 (status, payload)；flow 为 EvidenceChain 实例。"""
    body = body or {}
    seg = _segments(path)
    try:
        if method == "GET" and seg == []:
            return 200, {"service": "教材修订证据链", "ok": True}
        if method == "GET" and seg == ["health"]:
            return 200, {"ok": True}

        # 材料登记与核验
        if method == "POST" and seg == ["materials"]:
            record = flow.register_material(
                body.get("material_id"), body.get("kind"), body.get("title"),
                body.get("content"), body.get("actor"), body.get("idempotency_key"))
            return 201, asdict(record)
        if method == "POST" and len(seg) == 3 and seg[0] == "materials" and seg[2] == "verify":
            record = flow.verify_material(seg[1], body.get("actor"), body.get("approved"))
            return 200, asdict(record)
        if method == "GET" and seg == ["materials"]:
            return 200, {"materials": [asdict(m) for m in flow.list_materials()]}
        if method == "GET" and len(seg) == 2 and seg[0] == "materials":
            return 200, asdict(flow.get_material(seg[1]))

        # 结论生成、修订与版本查询
        if method == "POST" and seg == ["conclusions"]:
            record = flow.generate_conclusion(
                body.get("conclusion_id"), body.get("content"),
                body.get("material_ids"), body.get("actor"), body.get("idempotency_key"))
            return 201, asdict(record)
        if method == "POST" and len(seg) == 3 and seg[0] == "conclusions" and seg[2] == "revisions":
            record = flow.revise_conclusion(
                seg[1], body.get("content"), body.get("material_ids"),
                body.get("actor"), body.get("idempotency_key"))
            return 201, asdict(record)
        if method == "GET" and seg == ["conclusions"]:
            return 200, {"conclusions": [asdict(c) for c in flow.list_conclusions()]}
        if method == "GET" and len(seg) == 2 and seg[0] == "conclusions":
            return 200, asdict(flow.get_conclusion(seg[1]))
        if method == "GET" and len(seg) == 4 and seg[0] == "conclusions" and seg[2] == "versions":
            return 200, asdict(flow.get_conclusion(seg[1], int(seg[3])))

        # 证据链追溯与全链校验
        if method == "GET" and len(seg) == 3 and seg[0] == "conclusions" and seg[2] == "chain":
            return 200, flow.trace(seg[1])
        if method == "GET" and seg == ["verify"]:
            return 200, flow.verify_chain()

        return 404, {"error": "not_found", "message": f"无此路由: {method} {path}"}
    except DomainError as e:
        return e.status, {"error": e.code, "message": str(e)}
    except (TypeError, ValueError) as e:
        return 400, {"error": "bad_request", "message": str(e)}
