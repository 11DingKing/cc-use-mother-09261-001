# 教材修订证据链

纯 Python 服务端项目：工作人员先登记原始材料，再按规则生成可追溯的修订结论。
结论按版本持久化于 SQLite，服务重启后仍可查询同一版本并校验证据链。

## 运行

```bash
python3 -m service_09261_001.server evidence.db 8000
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /materials | 登记原始材料（可选 idempotency_key 幂等键） |
| GET | /materials | 材料列表 |
| GET | /materials/{id} | 单份材料 |
| POST | /conclusions | 按规则生成结论新版本（可选幂等键） |
| GET | /conclusions | 各结论最新版本 |
| GET | /conclusions/{id} | 指定结论最新版本 |
| GET | /conclusions/{id}/versions/{n} | 指定历史版本 |
| GET | /conclusions/{id}/versions/{n}/chain | 该版本证据链校验 |

示例：

```bash
curl -X POST localhost:8000/materials -d '{"id":"M001","title":"义务教育数学课程标准","kind":"课程标准","content":"2022年版课标原文","registered_by":"张三"}'
curl -X POST localhost:8000/conclusions -d '{"id":"C001","target":"第三章第二节","material_ids":["M001","M002"],"created_by":"赵六","idempotency_key":"gen-1"}'
curl localhost:8000/conclusions/C001/versions/1/chain
```

## 生成规则

- R1 至少引用两份原始材料，且不得重复引用；
- R2 所有引用的材料必须已登记；
- R3 至少引用一份「课程标准」类材料。

材料类型：课程标准、调研报告、专家意见、勘误记录。
材料一经登记不可修改；每次生成产生递增版本，历史版本不可篡改，
版本内记录所引用材料的指纹与整链摘要（SHA-256），可通过 chain 接口重算校验。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 编译检查

```bash
python3 -m compileall -q service_09261_001 tests
```
