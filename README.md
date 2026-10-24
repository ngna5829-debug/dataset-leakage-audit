# 数据来源泄漏审计 · dataset-leakage-audit

<!-- publication-status:start -->
**发布与学习入口：** [项目静态首页（本地文件）](index.html) · [在线示例](https://ngna5829-debug.github.io/dataset-leakage-audit/) · [中文教材专章](docs/TUTORIAL.zh-CN.md) · [统一教材 PDF（v2.0.0）](https://github.com/ngna5829-debug/edit-regression-lab/releases/download/v2.0.0/aigc-evaluation-textbook-v2.pdf) · [CI 运行](https://github.com/ngna5829-debug/dataset-leakage-audit/actions/workflows/test.yml)。

Pages 展示已生成的静态示例；分析、模型推理和正式导入仍在本机 Python 中执行。CI 配置安装仓库包、检查依赖并运行离线测试与 CLI；实际运行结果请打开上方 CI 链接，按对应 commit 查看。
<!-- publication-status:end -->

同一张图改名、压缩或缩放后，是否同时出现在训练集、校准集与测试集？这个离线 Python 项目把字节相同、来源声明与相似候选分别保存，让你看证据、填写复核，再提出不拆散来源组的划分建议。

**已实现：** SHA-256、图像 dHash、中文字符 ngram、按时间顺序的视频帧匹配、SQLite 查询、中文离线复核页、草稿导出/CLI 导入、并查集分组建议。默认没有网络请求、模型下载或账号。候选不等于确认重复，程序执行完成不等于素材来源独立。

先看 [零基础开始](START_HERE.md)、[Mac 完整操作](docs/GETTING_STARTED.zh-CN.md)、[150 项实际案例](docs/CASE_STUDY.zh-CN.md) 和 [自己的三个增量挑战](MY_WORK.md)。已保存的 [中文复核报告](examples/demo_v2/review.html)、[原始结果](examples/demo_v2/result.json) 与 [构造诊断](examples/demo_v2/construction_metrics.json) 可逐项核对；GitHub 文件页不能执行 HTML，下载后本地打开。


## 三步运行

在 macOS 终端进入本仓库目录后执行。先用 `python3.12 --version` 确认已有 Python 3.12；零基础读者优先按完整操作文档的三资产小例完成 validate → audit → 复核 → propose-split。下面另列可选的完整构造 demo：

如果已按教材安装 uv，但 `python3.12` 不在 PATH，可用 `uv venv --seed --python 3.12 .venv` 替代下方建环境命令，随后仍用 `.venv/bin/python`；环境创建后可用 `.venv/bin/python --version` 检查版本。这是备用操作路线，不表示作者已实跑 uv 的系统安装过程。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python audit.py demo --out runs/first_run
```

打开 `runs/first_run/review.html`（macOS 可执行 `open runs/first_run/review.html`）。再次运行请换新目录；已有结果不覆盖。Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。

固定夹具包含 **150 项：138 张自绘图片、12 段 3 秒视频**。24 个图片来源各含原图、改名副本、JPEG、缩放、裁切；18 个独立绘制的同风格困难反例；4 个视频来源各含原片、真实再次编码与反序版本。全部是构造材料，不代表模型输出或真人评分。

当前附带运行：150/150 媒体可读；397 对候选、304 对跨集合；108 条精确字节或输入声明的跨集合边。252 个已知同源 pair 中检索到 244 个，8 个反序视频来源关系未检索到；153 对困难负例全进入候选。这说明候选必须复核。原图与裁切仅 2/24 对被**媒体方法**命中，其余通过文本或来源声明进入候选，不能把联合命中包装成视觉算法成功。具体分母见 [construction_metrics.json](examples/demo_v2/construction_metrics.json)。这些是夹具诊断，不是自然数据的召回率或准确率。

## 换成自己的输入

先把有权使用的素材复制到 `my_data/media/`，确认文件存在，再创建 `my_data/manifest.csv`。**媒体 `path` 必须是清单所在目录内的相对路径**，例如 `media/原图.png`；不能写绝对路径、`../原图.png` 或任何含 `..` 的路径，也不能用指向目录外的符号链接。中文路径可用。完整操作文档先复制仓库的 CC0 自绘样例再写清单，避免指向不存在的占位文件。下表是自己的素材格式示意，需替换为已复制文件：

```csv
asset_id,path,split,source_group,media_type,text,license_note
scene_a,media/原图.png,train,photo_session_01,image,红杯放在桌子左边,本人拍摄
scene_a_small,media/缩小图.jpg,test,photo_session_01,image,红杯放在桌子左边,本人拍摄的缩放版本
scene_b,media/另一张图.png,calibration,unknown,image,蓝杯放在桌子右边,待补充来源
video_a,media/短片.mp4,train,shoot_02,video,,本人拍摄
```

`asset_id,path,split,source_group` 必填且为字符串，ID 唯一。`media_type` 可省略（按扩展名推断 image/video）；`text` 可用别名 `prompt`，为空时不做文本匹配；`license_note,parent_asset_id,provenance` 可选。允许 CSV 和每行一个对象的 JSONL。`unknown`、空来源、`未知` 不会合并为一个来源组。`parent_asset_id` 仅保留来源说明，不自动确认关系。

```bash
.venv/bin/python audit.py validate --manifest my_data/manifest.csv --out runs/my_validate
.venv/bin/python audit.py audit --manifest my_data/manifest.csv --out runs/my_audit
```

`validate` 解码与检查格式，**不扫描 pair**。`audit` 才计算候选。不要将 `runs/my_validate` 传给 `import-reviews` 或 `propose-split`，否则返回 `PAIR_SCAN_REQUIRED`（退出码 2）。每步输出都用新的、互不重叠的目录；失败后也换新目录，不能把下游目录放到旧 audit 内。自定义阈值时复制示例的 `config.json` 到新文件，修改后使用 `--config my_config.json`；不支持的配置字段会拒绝。默认 split 为 `train,calibration,test`，可以显式修改 `declared_splits`。这里的 calibration 是**被审计数据的划分**；本项目没有训练/调参近重复分类器，也没有声称对真实数据做过阈值校准。

坏路径、无法解码、超视频帧上限都保留在失败 ledger；不会被当作 clean。ID 重复、空清单或非法 schema 会返回退出码 2，新的输出目录含 `status=blocked` 的结果。可读媒体的扫描正常结束返回退出码 0，即使发现跨集合风险或部分媒体失败；请同时检查 `quality.verdict` 和覆盖率。一个或零个可读资产的质量状态为 incomplete。

## 复核与修复建议

1. 在报告选择“跨集合”，检查双图、原始尺寸、提示词、命中原因与阈值。视频显示首帧缩略图和逐对匹配时间；最终需核对原片，浏览器页不冒充全片播放器。
2. 选 `duplicate`（同内容复制）、`related`（确认同源派生）、`distinct`（不同来源）、`unsure`（证据不足），填写证据和复核者代号。草稿按输入/规则摘要存于本浏览器。存储受限会显示提示，此时及时导出。
3. 导出已填部分 JSON。只导出当前本机草稿；导入时和该版本已有正式记录按 pair 合并。重复 pair 不增加人数；修改同一 pair 在新输出版本中生效，旧版本保留。

```bash
.venv/bin/python audit.py import-reviews --audit runs/my_audit --reviews ~/Downloads/reviews-摘要.json --out runs/my_reviewed
.venv/bin/python audit.py propose-split --audit runs/my_reviewed --out runs/my_proposal --seed 17 --ratios 0.7,0.15,0.15
```

`import-reviews` 先核验已保存的原清单、规范化快照、有效配置、必要工件与 SHA 清单，再校验复核文件的三个绑定摘要；任何条目非法时不导入该批次。`input_digest` 包含审计时记录的媒体 SHA，但下游命令不会重新读取此刻的原始媒体；源文件变化后必须重新 audit，不能把旧快照视为对当前文件的证明。文件需 `schema_version=2.0`、三个 digest、`reviews` 数组和可选 `manual_pairs`。每条 review 含 `pair_id,decision,evidence,reviewer,origin`；真实本机复核为 `origin=human_local`。范例 `fixture_reviews.json` 明确为 `construction_truth_fixture`，不自动导入，不伪装成人。

漏检时可在导出 JSON 的 `manual_pairs` 增加：

```json
{"asset_a":"scene_a","asset_b":"scene_b","reason":"原始工程文件记录这两张来自同一张底图"}
```

导入后报告出现人工候选，再正常复核导出。手工候选本身不形成确认边。不能通过 distinct 清除已记录的 SHA 相同或 source_group 声明；这类冲突有独立计数。错误来源声明需要另存新清单重跑。

`split_proposal.csv` 是**待执行建议**，列出旧 split、新 split、组 ID、证据、是否变动。不会移动/删图，也不重写输入。只将相同字节、已声明 source_group、已确认 duplicate/related 边加入并查集；像素 hash、dHash、文本和视频候选不自动加入。整组按固定 seed、组大小降序与目标样本数平方偏差贪心分配。报告目标和实际资产/组计数；小组数量或大组会导致比例偏离，不能保证类别平衡。

## 输入输出与可追溯性

|文件|用途|
|---|---|
|`result.json`|执行 status、独立 quality、输入/配置摘要、输出 SHA 清单、分母与警告|
|`audit.json` / `audit_summary.json`|资产、特征、原始候选、正式复核、确认/待查关联组及汇总|
|`audit.sqlite`|assets / features / candidate_edges / reviews / failures / metadata|
|`manifest.original.*` / `manifest.snapshot.jsonl`|原清单字节副本和规范化字段快照|
|`config.json`|包含默认值的有效配置；预处理和所有门槛均绑定摘要|
|`candidate_edges.csv` / `exact_leaks.csv`|全部候选、跨集合同字节证据|
|`failure_ledger.csv`|计划资产中的失败项、阶段、错误与原因|
|`evidence/*.jpg` / `review.html`|离线报告证据与复核界面|
|`review_template.json`|绑定当前版本的空复核文件|

`input_digest` 由规范化身份、路径、划分、来源、文本、媒体 SHA 与解码状态生成；`manifest_digest` 绑定原始清单的完整内容，二者用途不同。文本还保留规范化摘要。原始媒体不复制到普通审计输出，demo 才生成 `input/`；输出缩略图不能替代源媒体归档。

修改清单、媒体或规则须新跑；输出每一份正式复核或建议都有新目录。SHA 用于完整性和绑定，不能证明版权、作者身份、训练成员或外部声明真实。

## 核心算法与公式

**字节与像素。** 流式 SHA-256 判断原始文件相同。图片按 EXIF 转正、转 RGBA，连同尺寸对原像素计算另一摘要；不缩放后再冒称原像素相同。完全相同像素不同编码仍只是来源候选，需人工或声明确认。

**dHash。** 将透明图复合到白底、转灰度、Lanczos 缩为 9×8。64 位为 `b(y,x)=1[I(y,x+1)>I(y,x)]`。距离 `H(a,b)=popcount(a XOR b)`，默认 `H≤6` 为候选。它不输出“重复概率”；纯色图、同布局图会碰撞，裁切和翻转可能漏检。

**中文文本。** NFKC、casefold，删除 Unicode 标点/分隔/控制字符，保留中文字。长度至少 8 字时取连续 3 字集合 `G`，`J(A,B)=|G_A∩G_B|/|G_A∪G_B|`，默认 `J≥0.8`。短文本不比较；同提示词不能证明媒体同源。文本处理无分词器，不理解语义、否定或同义词。

**有序视频。** 完整顺序解码，默认每 0.5 秒记录一个 dHash，记录取帧时间与 frame_index；超过 900 帧直接保留失败，不把截断片当整片。使用 OpenCV POS_MSEC；不单调时记录 `frame_index/declared_fps` 回退。固定相对采样偏移下寻找连续匹配，要求距离≤6、连续≥3、连续匹配数/较短序列长度≥0.75、时长比≥0.8。没有打乱帧排序或用单帧决定整片重复。这是候选机制；不支持可靠 VFR/源 PTS 取证、速度变化或局部片段检索，时间也不是原始容器 PTS 保证。

**图与分母。** 确认图与候选图分别输出。A–B、B–C 连通，只说明这组需整体保守隔离，不生成 A–C 的确认标签。所有计划 N 项对应 `N(N−1)/2` pair；跨 split s,t 的全集为 `N_s×N_t`；另列成功解码分母。污染下界是“至少与一条 train 有确认/声明关系的可读 test 资产数 / 可读 test 资产数”，每个 test ID 只数一次。明确标签候选中的正例比例只代表已复核候选；缺标签不是负例，全 unsure 时比例为 null，不推估全库泄漏率。

**复杂度。** 当前是可复算穷举：图片/文本 pair `O(N²)`，单个视频对的所有固定偏移扫描 `O(F_a F_b)`，视频解码 `O(总帧数)`；候选全部落盘，最坏空间 `O(N² + 总采样帧数)`。默认限制 1000 项以避免无意大规模计算；显式提高上限不会自动变成可扩展检索。没有 ANN、CLIP、pHash DCT、自动删除或百万级承诺。

## 用 SQL 学一次真实查询

可用系统 `sqlite3`，或 Python 自带 sqlite3，无需数据库服务器：

```sql
-- 跨集合候选及媒体失败状态；不是所有行都确认同源。
SELECT e.pair_id, a.asset_id, a.split, b.asset_id, b.split,
       e.evidence_status, COALESCE(r.decision, 'pending') AS review
FROM candidate_edges e
JOIN assets a ON a.asset_id=e.asset_a
JOIN assets b ON b.asset_id=e.asset_b
LEFT JOIN reviews r ON r.pair_id=e.pair_id
WHERE e.cross_split=1;

-- 原始字节 hash 发现同文件改名；没有把 dHash 当同字节。
SELECT sha256, COUNT(*) AS asset_count, COUNT(DISTINCT split) AS splits
FROM assets WHERE sha256 IS NOT NULL
GROUP BY sha256 HAVING COUNT(*)>1;

-- 检查完整计划的解码覆盖。
SELECT split, decode_status, COUNT(*) FROM assets GROUP BY split, decode_status;
```

## 测试与实跑边界

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python audit.py import-reviews --audit examples/demo_v2 --reviews examples/demo_v2/fixture_reviews.json --out runs/fixture_reviewed
.venv/bin/python audit.py propose-split --audit runs/fixture_reviewed --out runs/fixture_proposal
```

测试覆盖 dHash 碰撞不自动合并、中文归一化、失败分母、错误绑定整批拒绝、源媒体修改摘要变化、工件损坏、链式边不补造 pair、20 条 train 边只算一个 test 资产、视频顺序、人工追加漏检候选和分组约束。当前核心本地回归为 41/41 通过，输入边界说明见 [输入验证审查](docs/INPUT_VALIDATION_REVIEW.md)。已完成浏览器筛选、分页、草稿刷新与导出文本回灌检查；原生下载是否实际落盘未确认，操作文档保留复制导出文本的路径。自动化浏览器测试不算真人复核；公开复核示例只有 2 条 `construction_truth_fixture`。CI 配置已提供，远端执行与发布状态由统一发布验收记录确认。未做真人评分、真实自然数据基准、ANN 或模型推理。

代码 MIT；自绘构造媒体按 CC0-1.0 提供，见 [DATA_CARD.md](DATA_CARD.md)。
