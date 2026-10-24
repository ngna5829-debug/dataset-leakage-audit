# 一次可以复核的 150 项泄漏审计

设想一个小型素材库：原图进了 train，改名副本、JPEG、缩放版或裁剪版又被分到 calibration / test。按文件名随机划分会留下来源交叉。本项目用有明确构造来源的图片与短片把这个问题做成可检查的案例；它是教学审计，**不是自然数据基准，也没有真人评分，真人复核人数为 0**。

## 素材及运行结果

公开输入在 [manifest.jsonl](../examples/demo_v2/input/manifest.jsonl)，独立构造标签在 [construction_truth.json](../examples/demo_v2/input/construction_truth.json)。来源真值用于检查检索结果，不作为全部自动确认边注入审计。

|材料|构成|数量|
|---|---|---:|
|图片来源家族|24 个来源，每个有原图、改名副本、JPEG、缩放、裁剪|120 张|
|困难反例|独立绘制，但故意接近的轮廓和提示词|18 张|
|视频来源家族|4 个来源，每个有原片、真实再次编码、反序版；每段 3 秒|12 段|
|合计|138 张图片 + 12 段视频|150 项|

审计 [result.json](../examples/demo_v2/result.json) 为 `completed`，质量状态为 `confirmed_or_declared_cross_split`。这表示扫描执行完成且存在确认/声明跨集合关系，不能简写成“审计通过”。

|可核对字段|实际值|解释|
|---|---:|---|
|`planned_assets` / `audited_assets`|150 / 150|本夹具全部可读；不代表真实用户输入也全部可读|
|`planned_split_counts`|train 50、calibration 46、test 54|扫描前的原始划分|
|`all_pair_universe`|11,175|150 × 149 / 2，所有计划资产对|
|`candidate_pairs`|397|任一规则给出的候选并集，包含精确字节和声明关系|
|`cross_split_candidate_pairs`|304|上述候选中原 split 不同的对|
|`confirmed_or_declared_edges`|132|字节证据或来源声明纳入的边；初始正式复核为 0|
|`confirmed_cross_split_edges`|108|132 条中跨集合的边；不是 108 份真人判断|
|`confirmed_train_linked_test_assets` / `eligible_test_assets`|24 / 54|与 train 有直接确认/声明关系的可读 test 资产，按资产去重|
|`reviewed_pairs` / `precision_denominator`|0 / 0|初始审计没有复核标签，precision 为 null|

对应汇总见 [audit_summary.json](../examples/demo_v2/audit_summary.json)。24/54≈0.4444 是此夹具在当前证据政策下的污染下界；不能拿 304/11,175 当泄漏率，也不能把同一 test 的多条 train 边重复计数。

## 四层证据不能混写

|证据|能说什么|如何影响建议|
|---|---|---|
|原文件 SHA-256 相同|两个文件字节相同|直接形成不可拆分关系|
|原像素摘要、dHash、文本或有序视频匹配|某个特征满足规则，只是候选|等待来源证据和复核|
|相同且非 unknown 的 `source_group`|输入方声明同一来源，真实性仍由提供者负责|保守地纳入不可拆分关系|
|明确复核 `duplicate` / `related`|有记录的同内容复制 / 同源派生判断|加入确认关系；`distinct` / `unsure` 不新增这种关系|

dHash 先将图片复合到白底、转灰度并缩到 9×8，记录相邻像素亮度比较得到 64 位指纹；默认汉明距离 ≤6 就进入候选。相同文本使用字符 ngram Jaccard 作为另一条线索。二者都不是同源概率：纯色图、相似构图、相同提示词都可能碰撞。

像素相同也不等于原文件字节相同：PNG 与 BMP 可以存相同像素而字节完全不同。`exact_leaks.csv` 仅列跨集合同字节边，完整来源声明和其他候选需看 `candidate_edges.csv` / `audit.json`。声明错误不能靠给某对填 `distinct` 抹掉，应另存修正清单并新跑。

## 保留失败，才能看清检索边界

[construction_metrics.json](../examples/demo_v2/construction_metrics.json) 按全部构造真值统计：24 个五成员图片家族贡献 24×10=240 个同源 pair，4 个三成员视频家族贡献 4×3=12 个，总计 **252**。检索到 **244** 个，剩余 **8** 个也留在分母里。

|构造关系|已知对数|全部规则命中|仅媒体方法命中|应该怎么读|
|---|---:|---:|---:|---|
|原图 + 改名|24|24|24|字节复制能检出|
|原图 + JPEG|24|24|24|本夹具中的编码变化进入媒体候选|
|原图 + 缩放|24|24|24|本夹具中的缩放进入媒体候选|
|原图 + 裁剪|24|24|**2**|其余 22 对靠文本或声明进入并集，视觉检索仍有明显缺口|
|视频原片 + 再编码|4|4|4|当前有序帧规则检出本夹具的再次编码|
|视频原片 + 反序|4|**0**|**0**|来源相同但顺序反转，全部漏检|
|视频再编码 + 反序|4|**0**|**0**|另外 4 对反序同源关系也漏检|

上表仅选出代表性变换，完整分类保留在 JSON；不能把这些行相加当成全部 252 对。18 个困难反例形成 18×17/2=**153 对独立来源负例，153 对全部进入候选**。这说明特征线索会带来复核负担，而不是 153 对被自动判为重复。这里的已知负例不是全体剩余 10,923 对的逐对人工标签，不能据此计算自然数据准确率。

视频规则保留时间顺序，在固定采样偏移下寻找连续匹配。默认每 0.5 秒采样、至少连续 3 帧、匹配占较短序列至少 0.75、时长比至少 0.8。反序、变速、局部片段检索及可靠源 PTS 取证并未解决；解码超 900 帧会保留失败，不把截断扫描冒充整片判断。完整公式与复杂度见 [README](../README.md)。

## 从复核到划分建议

公开 [fixture_reviews.json](../examples/demo_v2/fixture_reviews.json) 只有两条示范：一条按构造记录标 `related`，一条独立绘制反例标 `distinct`；两者均为 `origin=construction_truth_fixture`。它们演示导入协议，不能视作两名真人、两次独立判断或实测人类 precision。公开 [reviewed_v2](../examples/reviewed_v2/audit_summary.json) 保留了这一区分。

零基础实际操作先用 [三资产完整流程](GETTING_STARTED.zh-CN.md)。需要复算公开复核示例时，可在仓库目录执行下面两条命令；这里导入的仍是构造标签，不会产生真人评测证据：

```bash
.venv/bin/python audit.py import-reviews --audit examples/demo_v2 --reviews examples/demo_v2/fixture_reviews.json --out runs/case_reviewed_01
.venv/bin/python audit.py propose-split --audit runs/case_reviewed_01 --out runs/case_proposal_01 --seed 17 --ratios 0.7,0.15,0.15
```

每次使用新的输出目录，完整 audit 工件与三摘要绑定必须校验通过；不从 validate-only 目录生成建议。公开 [split_proposal.json](../examples/proposal_v2/split_proposal.json) 在该协议下输出 150 行、90 个组，建议 train 105、calibration 23、test 22，104 项与原 split 不同。数字是建议表，不是文件已搬移或泄漏已消除的证明。尚未确认的 JPEG、裁剪或视频来源关系可能继续分开，应先补证据和复核。

并查集（DSU）用于保守隔离连通组。假如只有 A–B 和 B–C 两条已确认边，三项会建议分在同一集合，但并未创造第三条 A–C 重复标签；传递连通不代表组内两两同样。算法按组分配、固定随机种子并减少目标数量偏差，不保证类别平衡，遇到大组时允许比例偏离。

建议程序不会移动或删除文件，不会改写输入清单。先检查每项只出现一次、全部确认/声明边不跨建议 split，再由数据负责人审阅。没有候选、所有建议完成或工件 SHA 一致，都不能证明版权、模型训练成员关系或自然数据的来源独立。

## 能复核到的工程证据

核心本地回归已通过 41 项，包含 validate 下游阻断、1000 节点长链、dHash 碰撞不自动合并、错误摘要与不完整工件清单拒绝等。详见 [输入验证审查](INPUT_VALIDATION_REVIEW.md)；公开四组结果的 487 项已声明输出 SHA 曾全部验过，数据和代码版本变化后仍应重新核验。

浏览器曾验收筛选 304/397、分页、草稿恢复和导出文本经 CLI 回灌；这些是自动化操作证据，真人人数仍为 0。原生下载落盘未被该验收确认，所以操作指南保留导出文本另存方式。当前规模采用穷举 pair，默认上限 1000 项；没有百万级索引、ANN、模型推理或自然数据效能结论。

[统一教材 PDF（发布预留）](https://github.com/ngna5829-debug/edit-regression-lab/releases/download/v2.0.0/aigc-evaluation-textbook-v2.pdf)：由根任务统一发布并下载验真；当前为预留地址。
