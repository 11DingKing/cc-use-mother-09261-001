"""教材修订证据链业务规则。

工作流程：工作人员先登记原始材料（登记后不可修改，附内容指纹），
再按规则引用材料生成修订结论；每次生成产生一个递增版本，
版本与证据链摘要一并持久化，可随时回溯查询与校验。

生成规则：
R1 至少引用两份原始材料，且不得重复引用；
R2 所有引用的材料必须已登记；
R3 至少引用一份「课程标准」类材料。
"""
import hashlib
from datetime import datetime, timezone

MATERIAL_KINDS = ("课程标准", "调研报告", "专家意见", "勘误记录")
STANDARD_KIND = "课程标准"


class DomainError(ValueError):
    """输入不合法（HTTP 400）。"""


class NotFoundError(DomainError):
    """资源不存在（HTTP 404）。"""


class ConflictError(DomainError):
    """资源冲突（HTTP 409）。"""


class RuleViolation(DomainError):
    """违反结论生成规则（HTTP 422）。"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def material_fingerprint(material_id, title, kind, content):
    """单份材料的内容指纹。"""
    return _sha256("\n".join((material_id, title, kind, content)))


def chain_digest(entries):
    """证据链摘要：entries 为 [(材料id, 材料指纹), ...]，按引用顺序串联。"""
    return _sha256("\n".join(f"{mid}:{fp}" for mid, fp in entries))


class EvidenceChainService:
    def __init__(self, store):
        self.store = store

    # ---- 原始材料登记 ----
    def register_material(self, material_id, title, kind, content, actor):
        if not material_id or not title or not actor:
            raise DomainError("id、title、registered_by 均为必填")
        if kind not in MATERIAL_KINDS:
            raise DomainError(f"kind 须为以下之一:{'、'.join(MATERIAL_KINDS)}")
        if self.store.get_material(material_id):
            raise ConflictError(f"材料已登记:{material_id}")
        content = content or ""
        row = {
            "id": material_id,
            "title": title,
            "kind": kind,
            "content": content,
            "registered_by": actor,
            "registered_at": _now(),
            "sha256": material_fingerprint(material_id, title, kind, content),
        }
        self.store.add_material(row)
        return row

    def get_material(self, material_id):
        row = self.store.get_material(material_id)
        if not row:
            raise NotFoundError(f"材料不存在:{material_id}")
        return row

    def list_materials(self):
        return self.store.list_materials()

    # ---- 结论生成与查询 ----
    def generate_conclusion(self, conclusion_id, target, material_ids, actor):
        if not conclusion_id or not target or not actor:
            raise DomainError("id、target、created_by 均为必填")
        material_ids = list(material_ids or [])
        if len(material_ids) < 2:
            raise RuleViolation("R1:至少引用两份原始材料")
        if len(set(material_ids)) != len(material_ids):
            raise RuleViolation("R1:材料引用重复")
        materials = []
        for mid in material_ids:
            row = self.store.get_material(mid)
            if not row:
                raise NotFoundError(f"材料不存在:{mid}")
            materials.append(row)
        if STANDARD_KIND not in {m["kind"] for m in materials}:
            raise RuleViolation(f"R3:至少引用一份「{STANDARD_KIND}」类材料")
        version = (self.store.latest_version(conclusion_id) or 0) + 1
        digest = chain_digest([(m["id"], m["sha256"]) for m in materials])
        titles = "、".join(f"《{m['title']}》" for m in materials)
        statement = f"依据{titles}共{len(materials)}份登记材料，生成对「{target}」的修订结论。"
        row = {
            "id": conclusion_id,
            "version": version,
            "target": target,
            "statement": statement,
            "material_ids": material_ids,
            "digest": digest,
            "created_by": actor,
            "created_at": _now(),
        }
        self.store.add_conclusion_version(row)
        return row

    def get_conclusion(self, conclusion_id, version=None):
        if version is None:
            version = self.store.latest_version(conclusion_id)
            if version is None:
                raise NotFoundError(f"结论不存在:{conclusion_id}")
        row = self.store.get_conclusion_version(conclusion_id, version)
        if not row:
            raise NotFoundError(f"结论不存在:{conclusion_id}@v{version}")
        return row

    def list_conclusions(self):
        return self.store.list_latest_conclusions()

    def get_chain(self, conclusion_id, version):
        """重算每份材料指纹与整链摘要，校验该版本证据链是否完整。"""
        row = self.get_conclusion(conclusion_id, version)
        entries = []
        for mid in row["material_ids"]:
            material = self.store.get_material(mid)
            if material is None:
                entries.append({"id": mid, "verified": False, "missing": True})
                continue
            recomputed = material_fingerprint(
                material["id"], material["title"], material["kind"], material["content"])
            entries.append({
                "id": material["id"],
                "title": material["title"],
                "kind": material["kind"],
                "sha256": material["sha256"],
                "verified": recomputed == material["sha256"],
            })
        digest_ok = chain_digest(
            [(e["id"], e["sha256"]) for e in entries if "sha256" in e]) == row["digest"]
        return {
            "conclusion_id": conclusion_id,
            "version": row["version"],
            "digest": row["digest"],
            "entries": entries,
            "verified": digest_ok and all(e["verified"] for e in entries),
        }
