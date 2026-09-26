"""教材修订证据链领域规则。

业务流程：登记原始材料 → 核验材料 → 按规则生成结论（可修订、版本化）。
每条结论生成时固化所引用材料的内容哈希，并与前一条结论链接成链式
结构：任何事后篡改（材料内容、结论内容、链序）都能被 verify_chain 发现。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone

GENESIS_HASH = "0" * 64

# 材料核验状态机：登记后只允许核验一次，结论只能引用已核验材料
_MATERIAL_TRANSITIONS = {"registered": {"verified", "rejected"}}


class DomainError(Exception):
    """业务规则错误；code 供 API 映射，status 为建议的 HTTP 状态码。"""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise DomainError("rule_violation", f"{label}不能为空")
    return value


def material_hash(material_id, kind, title, content):
    """材料内容指纹：绑定编号与全部实质内容。"""
    return _sha256("\n".join([material_id, kind, title, content]))


def chain_hash(prev_hash, conclusion_id, version, content, material_hashes):
    """结论链式哈希：链接前一条结论，并固化本条全部证据哈希。"""
    joined = ",".join(sorted(material_hashes))
    return _sha256("\n".join([prev_hash, conclusion_id, str(version), content, joined]))


@dataclass(frozen=True)
class Material:
    material_id: str
    kind: str
    title: str
    content: str
    material_hash: str
    state: str
    registered_by: str
    registered_at: str
    verified_by: str | None = None
    verified_at: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class Conclusion:
    conclusion_id: str
    version: int
    content: str
    material_ids: tuple
    prev_chain_hash: str
    chain_hash: str
    created_by: str
    created_at: str
    idempotency_key: str | None = None


def _to_material(row):
    return Material(**row)


def _to_conclusion(row, material_ids):
    return Conclusion(
        conclusion_id=row["conclusion_id"],
        version=row["version"],
        content=row["content"],
        material_ids=tuple(material_ids),
        prev_chain_hash=row["prev_chain_hash"],
        chain_hash=row["chain_hash"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        idempotency_key=row["idempotency_key"],
    )


class EvidenceChain:
    """教材修订证据链工作流；全部状态经 SQLiteStore 持久化，重启可恢复。"""

    def __init__(self, store):
        self.store = store

    # ---- 材料登记与核验 ----

    def register_material(self, material_id, kind, title, content, actor, idempotency_key=None):
        if idempotency_key:
            hit = self.store.material_by_key(idempotency_key)
            if hit:
                return _to_material(hit)
        _require_text(material_id, "材料编号")
        _require_text(kind, "材料类型")
        _require_text(title, "材料标题")
        _require_text(content, "材料内容")
        _require_text(actor, "登记人")
        if self.store.get_material(material_id):
            raise DomainError("duplicate", f"材料编号已存在: {material_id}", 409)
        record = Material(
            material_id=material_id,
            kind=kind,
            title=title,
            content=content,
            material_hash=material_hash(material_id, kind, title, content),
            state="registered",
            registered_by=actor,
            registered_at=_now(),
            idempotency_key=idempotency_key,
        )
        self.store.add_material(record.__dict__)
        return record

    def verify_material(self, material_id, actor, approved):
        _require_text(actor, "核验人")
        if not isinstance(approved, bool):
            raise DomainError("rule_violation", "approved 必须为布尔值")
        record = self._material_or_404(material_id)
        target = "verified" if approved else "rejected"
        if target not in _MATERIAL_TRANSITIONS.get(record.state, set()):
            raise DomainError("invalid_state", f"材料状态 {record.state} 不可流转为 {target}", 409)
        record = replace(record, state=target, verified_by=actor, verified_at=_now())
        self.store.set_material_state(material_id, record.state, record.verified_by, record.verified_at)
        return record

    def get_material(self, material_id):
        return self._material_or_404(material_id)

    def list_materials(self):
        return [_to_material(r) for r in self.store.list_materials()]

    # ---- 结论生成与修订 ----

    def generate_conclusion(self, conclusion_id, content, material_ids, actor, idempotency_key=None):
        if idempotency_key:
            hit = self.store.conclusion_by_key(idempotency_key)
            if hit:
                return self._load_conclusion(hit)
        _require_text(conclusion_id, "结论编号")
        if self.store.latest_version(conclusion_id) is not None:
            raise DomainError("duplicate", f"结论编号已存在，请使用修订接口: {conclusion_id}", 409)
        return self._append_conclusion(conclusion_id, 1, content, material_ids, actor, idempotency_key)

    def revise_conclusion(self, conclusion_id, content, material_ids, actor, idempotency_key=None):
        if idempotency_key:
            hit = self.store.conclusion_by_key(idempotency_key)
            if hit:
                return self._load_conclusion(hit)
        latest = self.store.latest_version(conclusion_id)
        if latest is None:
            raise DomainError("not_found", f"结论不存在: {conclusion_id}", 404)
        return self._append_conclusion(conclusion_id, latest + 1, content, material_ids, actor, idempotency_key)

    def get_conclusion(self, conclusion_id, version=None):
        if version is None:
            version = self.store.latest_version(conclusion_id)
            if version is None:
                raise DomainError("not_found", f"结论不存在: {conclusion_id}", 404)
        row = self.store.get_conclusion(conclusion_id, version)
        if row is None:
            raise DomainError("not_found", f"结论版本不存在: {conclusion_id} v{version}", 404)
        return self._load_conclusion(row)

    def list_conclusions(self):
        return [self._load_conclusion(r) for r in self.store.list_latest_conclusions()]

    # ---- 证据链追溯与校验 ----

    def trace(self, conclusion_id, version=None):
        """单条结论的证据链：结论版本 + 所引材料当前完整性。"""
        conclusion = self.get_conclusion(conclusion_id, version)
        cited = self.store.conclusion_materials(conclusion.conclusion_id, conclusion.version)
        materials = []
        intact = True
        for row in cited:
            current = self.store.get_material(row["material_id"])
            ok = current is not None and material_hash(
                current["material_id"], current["kind"], current["title"], current["content"]
            ) == row["material_hash"]
            intact = intact and ok
            materials.append({
                "material_id": row["material_id"],
                "cited_hash": row["material_hash"],
                "state": current["state"] if current else "missing",
                "intact": ok,
            })
        recomputed = chain_hash(
            conclusion.prev_chain_hash,
            conclusion.conclusion_id,
            conclusion.version,
            conclusion.content,
            [r["material_hash"] for r in cited],
        )
        return {
            "conclusion": _conclusion_dict(conclusion),
            "materials": materials,
            "chain_ok": intact and recomputed == conclusion.chain_hash,
        }

    def verify_chain(self):
        """全链校验：按写入顺序重放，验证链序、哈希与材料完整性。"""
        rows = self.store.conclusions_in_order()
        prev = GENESIS_HASH
        for row in rows:
            cited = self.store.conclusion_materials(row["conclusion_id"], row["version"])
            for c in cited:
                current = self.store.get_material(c["material_id"])
                if current is None or material_hash(
                    current["material_id"], current["kind"], current["title"], current["content"]
                ) != c["material_hash"]:
                    return {"ok": False, "checked": row["seq"],
                            "detail": f"材料被篡改或缺失: {c['material_id']}"}
            if row["prev_chain_hash"] != prev:
                return {"ok": False, "checked": row["seq"],
                        "detail": f"链式断点: {row['conclusion_id']} v{row['version']}"}
            expect = chain_hash(prev, row["conclusion_id"], row["version"],
                                row["content"], [c["material_hash"] for c in cited])
            if row["chain_hash"] != expect:
                return {"ok": False, "checked": row["seq"],
                        "detail": f"结论哈希不符: {row['conclusion_id']} v{row['version']}"}
            prev = row["chain_hash"]
        return {"ok": True, "checked": len(rows), "head": prev}

    # ---- 内部 ----

    def _append_conclusion(self, conclusion_id, version, content, material_ids, actor, idempotency_key):
        _require_text(content, "结论内容")
        _require_text(actor, "操作人")
        if not isinstance(material_ids, (list, tuple)) or not material_ids:
            raise DomainError("rule_violation", "结论必须至少引用一条材料")
        if len(set(material_ids)) != len(material_ids):
            raise DomainError("rule_violation", "引用材料重复")
        materials = []
        for mid in material_ids:
            row = self.store.get_material(mid)
            if row is None:
                raise DomainError("rule_violation", f"引用的材料不存在: {mid}")
            if row["state"] != "verified":
                raise DomainError("rule_violation", f"材料未核验通过，不可作为证据: {mid}")
            materials.append(row)
        materials.sort(key=lambda r: r["material_id"])
        hashes = [m["material_hash"] for m in materials]
        prev = self.store.latest_chain_hash() or GENESIS_HASH
        record = {
            "conclusion_id": conclusion_id,
            "version": version,
            "content": content,
            "prev_chain_hash": prev,
            "chain_hash": chain_hash(prev, conclusion_id, version, content, hashes),
            "created_by": actor,
            "created_at": _now(),
            "idempotency_key": idempotency_key,
        }
        self.store.add_conclusion(record, [(m["material_id"], m["material_hash"]) for m in materials])
        return self._load_conclusion(record)

    def _load_conclusion(self, row):
        cited = self.store.conclusion_materials(row["conclusion_id"], row["version"])
        return _to_conclusion(row, [c["material_id"] for c in cited])

    def _material_or_404(self, material_id):
        row = self.store.get_material(material_id)
        if row is None:
            raise DomainError("not_found", f"材料不存在: {material_id}", 404)
        return _to_material(row)


def _conclusion_dict(conclusion):
    return {
        "conclusion_id": conclusion.conclusion_id,
        "version": conclusion.version,
        "content": conclusion.content,
        "material_ids": list(conclusion.material_ids),
        "prev_chain_hash": conclusion.prev_chain_hash,
        "chain_hash": conclusion.chain_hash,
        "created_by": conclusion.created_by,
        "created_at": conclusion.created_at,
    }
