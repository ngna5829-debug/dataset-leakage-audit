# 输入验证审查（角色 22）

2026-10-23 完成。范围为 `validate`、`audit`、`import-reviews`、`propose-split` 的异常输入和本地工件绑定；没有运行模型、浏览器、远端服务或真人复核。核心回归 41/41 通过，其中新增 19 项测试。角色 16 的 `pair_scan_completed` 保护和迭代并查集保持；角色 18 的 `render_html` 整个函数与修改前逐字一致。

## 已修复边界

|输入/条件|修复前实跑|当前行为|
|---|---|---|
|复核顶层 `[]`，或 `reviews=[null]`|未捕获 AttributeError，退出 1，无结果封套|退出 2；新目录写 blocked；不写正式复核|
|手工候选 `reason=null`|被 `str(None)` 接受，生成成功版本|必须是非空字符串；错误整批阻断|
|NaN 元数据、重复 JSON 键、溢出浮点数|NaN 可落入 audit/HTML；重复键取最后值|输入 JSON 拒绝非有限数、重复键；输出 JSON 禁止 NaN|
|重复 CSV 表头|最后一列静默替换同名 ID|重复/空表头和行宽不符均拒绝|
|可选字段错误类型、布尔阈值、非字符串 split|存在静默空值或意外类型错误|按字段类型校验；布尔值不是数值阈值|
|媒体 `../`、绝对路径、越界符号链接|读取清单目录外媒体并计为成功|不读取外部文件；保留 `MEDIA_PATH_ESCAPE` ledger、计划分母与 incomplete|
|两条零字节素材|失败仍有空文件 SHA，形成 1 条跨集合同字节边|两条 `EMPTY_MEDIA`，无媒体 SHA，0 条同字节边；声明来源和文本仍依既有规则处理|
|已有文件作为 `--out`|错误处理自身再触发 NotADirectoryError|退出 2；原文件字节不变|
|输出放在旧审计内部，或新输出将包含计划媒体路径|可向输入版本写入，甚至后续读取刚生成的文件|`INPUT_OUTPUT_OVERLAP`，不创建输出/错误封套，以保护输入目录|
|`result.artifacts=[]`、漏必要工件、重复路径|空列表绕过完整性循环|必要工件和唯一原清单必须已列出且 SHA 匹配|
|artifact 路径 `../` 或越界符号链接|读取审计目录外文件验证 SHA|`ARTIFACT_PATH_ESCAPE`，不读取目录外工件|
|result/audit 输入摘要不一致|继续生成建议|校验 result、audit、原清单、快照、有效配置、复核模板与重新计算的输入摘要|
|被重新列入 SHA 的坏 audit|仅检查配置摘要|验证资产身份与顺序、文本特征、关系端点/唯一性/状态、正式复核、失败 ledger、汇总和关联组|
|额外未绑定的 evidence/manifest 文件|整个 evidence 树和 glob 匹配文件一起复制|只复制已验证的缩略图及原清单|

视频读取器的非有限/负声明帧数现在明确失败并释放 capture；该分支用受控 mock 测试，未冒充真实损坏容器验证。有限正时长在落盘前校验。

## 输入合同与兼容性

- 媒体路径只接受清单目录内的相对路径；`..` 分量、绝对路径与指向目录外的符号链接都会成为媒体失败。可以把素材复制或硬链接到清单目录的子目录，或把清单放在素材共同根目录。
- 清单必需字段仍为字符串，`source_group` 可为空。出现的 `media_type`、`text`、`prompt`、`license_note`、`parent_asset_id`、`provenance` 必须是字符串；可选字段缺省仍使用既有默认值。
- `reviews` 必须明确提供数组；`manual_pairs` 可省略，提供时必须为数组。每个手工 pair 在同批次只能出现一次，方向反转也视为重复。复核原有 origin、三项摘要和唯一 pair 约束保持。
- 无扫描或旧 validate 工件仍不能导入复核/生成划分；合法旧审计缺少显式扫描字段时继续使用 provenance 回退。字符串 `"false"` 不会被视为已扫描。
- SHA 及一致性检查不是身份认证。能同时重写全部文件及其摘要的攻击者仍可构造自洽材料；本次没有提供签名、防并发替换或来源真实性证明。
- 错误输入通常退出 2，新且可写的输出目录会得到 blocked/failed 结果。原输出已有内容、输出为现有文件、输入输出重叠时保护现有内容；无法写入封套仍会在 stderr 报告原因。

## 公开四组工件的只读验证

|目录|已验证 SHA 条目|检查|
|---|---:|---|
|`examples/demo_v2`|163|严格 load 通过；150 项、397 候选|
|`examples/validate_v2`|161|严格 load 通过；150 项、0 候选，仍禁止下游扫描用途|
|`examples/reviewed_v2`|161|严格 load 通过；150 项、397 候选|
|`examples/proposal_v2`|2|150 个唯一 ID；所有确认边同组同 split；source audit SHA 匹配|

建议目录只有 proposal 工件，不是可继续导入复核的 audit 源，因此不调用 `load_audit` 冒充该用途。以上共 487 项声明工件 SHA 全部匹配，没有改动 examples。

另在独立证据目录重跑四条 CLI：当前 audit/validate/reviewed 与公开版本的三个摘要、资产、候选、正式复核、summary、确认及候选关联组完全相同；当前 proposal 的 150 行与公开 proposal 完全相同。无需重算这四组公开工件。若历史运行曾含零字节、越界媒体、错误类型或不完整工件清单，应修正原输入并新跑，不能仅改摘要让旧结果过关。

## 实际命令与证据

以下命令在项目目录执行；`before` 于修复前运行，`after` 于修复后运行，各脚本采用新输出目录，重复运行需另给目录名。

```bash
../../runtime/bin/python ../../reviews/data_bad_inputs/probe.py before
../../runtime/bin/python ../../reviews/data_bad_inputs/probe.py after
../../runtime/bin/python -m unittest discover -s tests -v
PYTHONPATH=. ../../runtime/bin/python ../../reviews/data_bad_inputs/compatibility.py
PYTHONPATH=. ../../runtime/bin/python ../../reviews/data_bad_inputs/finish_evidence.py
```

- `../../reviews/data_bad_inputs/before/observations.json`、`after/observations.json`：12 次 CLI 的退出码、stdout/stderr 日志和结果封套。
- `../../reviews/data_bad_inputs/before_after.json`：逐项前后结果；用保存的修改前模块与当前模块实跑零字节反例。
- `../../reviews/data_bad_inputs/tests.log`：41 项测试完整输出。
- `../../reviews/data_bad_inputs/compatibility.json`：四条完整实际 CLI、耗时、退出码、公开工件 SHA 数量及比较结果。
- `../../reviews/data_bad_inputs/compatibility_runs/`：重新生成的独立输出和四条 CLI 日志。
- `../../reviews/data_bad_inputs/core.patch`、`leakage_audit.before.py`：核心差异和修改前快照。

核心文件完成时 SHA-256：`d40e2b9c67ea5ebc1bf13d772a07327fe504dd0aea9b6f4bb4787736fb02b19f`。
