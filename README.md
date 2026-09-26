# 教材修订证据链

工作人员登记原始材料 → 材料核验 → 按规则生成可追溯结论（版本化 + 链式哈希）。
全部状态落 SQLite，每次写入即提交，进程重启后同一版本仍可查询。

## 运行

```
python3 -m service_09261_001.server --db evidence_chain.db --port 8000
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /materials | 登记原始材料 `{material_id, kind, title, content, actor, idempotency_key?}` |
| POST | /materials/{id}/verify | 核验材料 `{actor, approved}` → verified / rejected |
| GET | /materials 、 /materials/{id} | 查询材料 |
| POST | /conclusions | 生成结论 `{conclusion_id, content, material_ids[], actor, idempotency_key?}` |
| POST | /conclusions/{id}/revisions | 修订结论，产生 version+1，旧版本保留 |
| GET | /conclusions 、 /conclusions/{id} | 查询最新版本 |
| GET | /conclusions/{id}/versions/{n} | 查询指定历史版本 |
| GET | /conclusions/{id}/chain | 单条结论的证据链追溯 |
| GET | /verify | 全链完整性校验 |
| GET | /health | 健康检查 |

## 规则

- 结论必须引用至少一条材料，且所有引用材料必须已核验（verified）
- 结论生成时固化所引材料的内容哈希，并以 chain_hash 链接前一条结论
- 结论不可变：修订产生新版本，旧版本永久可查
- 任何事后篡改（改材料内容、改结论、断链）都会被 /verify 发现
- 写接口支持 idempotency_key 幂等重放，重复提交返回原记录

## 测试

```
python3 -m unittest discover -s tests -v
python3 -m compileall -q service_09261_001 tests
```

测试覆盖：登记/核验状态机、结论生成规则、幂等重放、篡改检测，以及
**重启持久化**——杀掉服务进程后用同一 SQLite 文件重启，仍能查到同一
结论版本且 chain_hash 一致。
