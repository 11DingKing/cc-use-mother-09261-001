"""JSON API 路由适配：把 HTTP 语义映射到证据链服务。

dispatch 为纯函数，不依赖 socket，便于直接单测。
"""
from urllib.parse import unquote

from .workflow import ConflictError, DomainError, NotFoundError, RuleViolation


def _require(body, *fields):
    missing = [f for f in fields if body.get(f) in (None, "")]
    if missing:
        raise DomainError("缺少必填字段:" + ",".join(missing))


def _idempotent(store, key, produce):
    """携带 idempotency_key 的请求：命中则返回首次结果，否则执行并留存结果。"""
    if key:
        hit = store.get_idempotency(key)
        if hit is not None:
            return hit["status"], hit["body"]
    status, body = produce()
    if key:
        store.save_idempotency(key, status, body)
    return status, body


def dispatch(service, method, path, body=None):
    body = body or {}
    parts = [unquote(p) for p in path.split("?")[0].split("/") if p]
    try:
        if method == "GET" and parts == ["health"]:
            return 200, {"status": "ok"}
        if parts[:1] == ["materials"]:
            if len(parts) == 1:
                if method == "POST":
                    _require(body, "id", "title", "kind", "registered_by")
                    return _idempotent(service.store, body.get("idempotency_key"), lambda: (
                        201, service.register_material(
                            body["id"], body["title"], body["kind"],
                            body.get("content", ""), body["registered_by"])))
                if method == "GET":
                    return 200, {"materials": service.list_materials()}
            if len(parts) == 2 and method == "GET":
                return 200, service.get_material(parts[1])
        if parts[:1] == ["conclusions"]:
            if len(parts) == 1:
                if method == "POST":
                    _require(body, "id", "target", "material_ids", "created_by")
                    return _idempotent(service.store, body.get("idempotency_key"), lambda: (
                        201, service.generate_conclusion(
                            body["id"], body["target"],
                            body["material_ids"], body["created_by"])))
                if method == "GET":
                    return 200, {"conclusions": service.list_conclusions()}
            if len(parts) == 2 and method == "GET":
                return 200, service.get_conclusion(parts[1])
            if len(parts) >= 4 and parts[2] == "versions" and method == "GET":
                if not parts[3].isdigit():
                    raise DomainError("版本号须为正整数")
                version = int(parts[3])
                if len(parts) == 4:
                    return 200, service.get_conclusion(parts[1], version)
                if len(parts) == 5 and parts[4] == "chain":
                    return 200, service.get_chain(parts[1], version)
        return 404, {"error": "not_found", "message": "路由不存在"}
    except NotFoundError as exc:
        return 404, {"error": "not_found", "message": str(exc)}
    except ConflictError as exc:
        return 409, {"error": "conflict", "message": str(exc)}
    except RuleViolation as exc:
        return 422, {"error": "rule_violation", "message": str(exc)}
    except DomainError as exc:
        return 400, {"error": "bad_request", "message": str(exc)}
