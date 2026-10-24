# Mac / Python 3.12：从清单到划分建议

这条路线用仓库已有的三份小素材练习，不需要运行完整 150 项 demo，也不需要账号或模型。安装依赖需要网络，安装完成后的审计在本机运行。素材许可见 [DATA_CARD.md](../DATA_CARD.md)。

## 1. 进入项目并准备 Python

在 Finder 找到解压或克隆后的 `dataset-leakage-audit` 文件夹。打开“终端”，输入 `cd `（末尾有空格），把该文件夹拖进终端后按回车。以下命令都在这个目录执行，不要逐行复制命令块外面的说明。

```bash
pwd
ls audit.py requirements.txt
python3.12 --version
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python audit.py --help
```

`ls` 应能找到两个文件，Python 应显示 `3.12.x`；如果出现 `command not found`，先安装 Python 3.12 并重新打开终端。不要把系统里另一个 Python 版本误当成本练习环境。安装命令成功后，`pip check` 应报告 `No broken requirements found`。后续一直使用 `.venv/bin/python`，无需执行 `activate`。

## 2. 先复制素材，再写清单

下面从仓库的 CC0 自绘图片取一张原图、它的逐字节副本和一张独立绘制图片。第一次练习使用尚不存在的 `my_data` 文件夹；已有同名个人素材时请换目录名并同步修改后续命令。

```bash
mkdir -p my_data/media
cp examples/demo_v2/input/media/f00_original.png my_data/media/original.png
cp my_data/media/original.png my_data/media/renamed.png
cp examples/demo_v2/input/media/hard_00.png my_data/media/independent.png
ls -lh my_data/media
```

先看到三个非空 PNG 文件，再执行下面的整个命令块。`cat` 会写出 UTF-8 CSV；末行 `CSV` 也要复制。

```bash
cat > my_data/manifest.csv <<'CSV'
asset_id,path,split,source_group,media_type,text,license_note
original,media/original.png,train,unknown,image,,仓库CC0自绘素材
renamed,media/renamed.png,test,unknown,image,,original的字节副本
independent,media/independent.png,calibration,unknown,image,,仓库CC0独立绘制素材
CSV
```

**这里的素材根目录是清单所在的 `my_data/`，不是终端当前目录。** `media/original.png` 表示 `my_data/media/original.png`。清单中的媒体路径只能是此目录内的相对路径：不允许 `/Users/...` 这样的绝对路径，不允许 `../` 或任何 `..` 分量，也不允许符号链接绕到外部。换自己的数据时，先把有权使用的文件复制到 `my_data/media/`，再写清单；不要把桌面文件的绝对路径直接粘进 CSV。

`asset_id,path,split,source_group` 为必需字符串字段，ID 必须唯一；`source_group` 可为空。`unknown`、空值和 `未知` 不会把素材合成一个组。只有掌握来源依据才填共同来源组，不能把“风格相同”写成来源声明。可选的 `text` / `prompt`、`license_note`、`parent_asset_id`、`provenance` 等字段出现时也必须是字符串；`parent_asset_id` 只记录说明，不自动建立关系。支持 CSV 和 JSONL，JSONL 为每行一个对象，禁止重复 JSON 键和非有限数。

## 3. validate：检查清单与媒体

```bash
.venv/bin/python audit.py validate --manifest my_data/manifest.csv --out runs/my_validate_01
cat runs/my_validate_01/result.json
```

本例应为 `status=completed`、`quality.verdict=validation_only_no_pair_scan`、`planned_assets=3`、`audited_assets=3`、`decode_failures=0`。此步生成 `result.json`、`audit.json`、清单快照、`failure_ledger.csv` 和预览页，但候选数为 0，因为它尚未比较素材对。候选数为 0 不能解释成没有泄漏。

每个 `--out` 都应是新的、互不重叠的目录。程序允许空目录，但推荐让命令自己创建；已有内容会报 `OUTPUT_EXISTS`。重跑用 `_02` 等新名称，失败目录也不要复用。输出不能包含清单或媒体，不能位于旧 audit 目录里。

## 4. audit：扫描关系并查看证据

```bash
.venv/bin/python audit.py audit --manifest my_data/manifest.csv --out runs/my_audit_01
open runs/my_audit_01/review.html
```

本例在当前默认配置下有 3 个可读资产、1 对候选，即 `original ↔ renamed` 的跨集合 SHA 相同关系；质量状态为 `confirmed_or_declared_cross_split`。核对 `audit_summary.json` 中的数量、理由和质量状态。`exact_leaks.csv` 只列跨集合同字节关系，`candidate_edges.csv` 列全部候选，`audit.sqlite` 可供 SQL 查询。HTML 页中的图是证据缩略图；视频需要另外打开原片核对。

不要将 `runs/my_validate_01` 作为后续 `--audit`：导入复核和划分建议都要求完成 pair 扫描，否则返回 `PAIR_SCAN_REQUIRED`、退出码 2。正确输入是这里的 `runs/my_audit_01`。

需要改规则时，复制 `runs/my_audit_01/config.json` 到独立文件，修改合法字段，给 validate 和 audit 同样加 `--config my_config.json` 并使用新输出目录。默认划分顺序为 `train,calibration,test`；这里的 calibration 是被审计数据的一个集合，不表示 dHash 阈值已在自然数据上校准。

## 5. 复核：把判断保存为正式版本

在报告选择“跨集合”，展开 `original ↔ renamed` 的理由。核对文件复制过程与 SHA 后，可选择 `duplicate`，写明“本练习将 original.png 逐字节复制为 renamed.png，SHA 相同”，填写你自己的复核者代号。其他关系证据不足就用 `unsure`，不为凑数编造判断。

|选择|含义|是否额外建立分组约束|
|---|---|---|
|`duplicate`|确认同内容复制|是|
|`related`|确认同源派生|是|
|`distinct`|确认不同来源|否；不会撤销已有 SHA / 来源声明|
|`unsure`|证据不足|否|

点击“导出已填写复核”。浏览器草稿只保存在本浏览器，刷新后能看到也不等于已正式导入。将实际导出的文件在“下载”中重命名为 `my_reviews.json`，然后执行：

```bash
mkdir -p review_inputs
cp ~/Downloads/my_reviews.json review_inputs/my_reviews.json
.venv/bin/python -m json.tool review_inputs/my_reviews.json
.venv/bin/python audit.py import-reviews --audit runs/my_audit_01 --reviews review_inputs/my_reviews.json --out runs/my_reviewed_01
open runs/my_reviewed_01/review.html
```

如果未出现下载文件，页面会显示完整只读导出文本和再次下载链接。可把完整文本保存到 `review_inputs/my_reviews.json`，使用 UTF-8 纯文本，不含 Markdown 围栏，然后从 `json.tool` 开始执行。不要假设点击按钮就已落盘。

导入成功后，新目录的 `audit.json`、`audit_summary.json` 与页面会出现正式复核记录；只填一对时 `reviewed_pairs=1`。这表示记录被接受，程序不会认证复核者身份。你实际填写的意见使用 `human_local`；仓库 `fixture_reviews.json` 使用 `construction_truth_fixture`，两者不能混称为真人独立评价。

复核 JSON 必须包含 `schema_version="2.0"`、`manifest_digest`、`input_digest`、`config_digest` 和 `reviews` 数组。每条意见需要真实候选 `pair_id`、合法 `decision`、非空 `evidence,reviewer,origin`。不要改摘要、手写猜测 pair ID，或用另一轮导出文件拼接。错误摘要、重复 pair 或任意非法意见会整批拒绝，旧版本保留。

漏检时，可在当前绑定的 JSON 的 `manual_pairs` 中写入两个现有资产 ID 和非空理由，例如 `{"asset_a":"original","asset_b":"independent","reason":"此处应替换为真实的外部来源线索"}`。这只是格式示意，不是本例同源事实。手工线索导入只新增候选，仍需在新报告里有依据地复核；同批不能重复或反向重复同一对。

## 6. propose-split：生成建议并验收

```bash
.venv/bin/python audit.py propose-split --audit runs/my_reviewed_01 --out runs/my_proposal_01 --seed 17 --ratios 0.7,0.15,0.15
open runs/my_proposal_01/split_proposal.csv
cat runs/my_proposal_01/split_proposal.json
```

输出包含 `split_proposal.csv`、`split_proposal.json` 和 `result.json`。CSV 每个原资产一行，包含旧 split、建议 split、group_id、证据和 `proposal_only` 状态。本例应有 3 行，`original` 与 `renamed` 的 group_id 和 proposed_split 一致。只有 3 个资产和 2 个不可拆分组，不保证凑齐三个集合或达到 70%/15%/15%；当前 seed 17 实跑为 train 2、calibration 1、test 0。JSON 同时列目标与实际数量；未获分配的 test 可能不出现在 `actual_asset_counts` 中，应按 0 解读。

比例按 `declared_splits` 的顺序输入，每个值须大于 0 且总和为 1。划分使用固定 seed、整组分配与目标数量偏差，不能拆开已确认关系以凑比例，也不保证类别平衡。并查集（DSU）把有确认或声明关系的连通资产整体隔离：A–B、B–C 关联可以形成同组，但没有据此新增 A–C 的两两重复标签。

建议不会自动移动/删除文件，也不会重写原清单。先审查来源关系、缺失素材、类别与分组约束，再决定是否另存下一版清单。`propose-split` 的结果目录不是 audit 目录，不能作为下一次复核的 `--audit`。

## 7. 每一步到底绑定什么

|记录|绑定内容|使用方式|
|---|---|---|
|`manifest_digest`|原始清单完整字节|仅换行或空白变化也可能改变它|
|`input_digest`|规范化身份、路径、split、来源、文本，以及审计时的媒体 SHA 和解码状态|对应当时审计快照|
|`config_digest`|包含默认值的有效配置|防止把别的阈值下的意见串入本轮|
|`result.json → artifacts`|必要输出文件与 SHA|下游逐项核验文件完整性、目录内路径和清单完整性|
|proposal 的 `audit_sha256`|用于建议的上游 audit 字节|追溯建议究竟依据哪份复核版本|

导入和建议还会校验原清单、快照、配置、资产、关系、复核、失败记录、汇总和组件的一致性。**下游校验读取已保存的审计工件，不重新读取当前原始媒体。** 原媒体、清单或规则改动后必须重新 audit；普通 audit 目录只保存缩略图与快照，不会完整备份源素材。保留自己的 `my_data/` 才能重新扫描。

SHA 是完整性工具，不是作者身份、版权或声明真实性证明。能重写所有文件与摘要的人仍可构造自洽材料；本项目没有数字签名。

|看到的结果|下一步|
|---|---|
|退出 0，`completed`，但 `incomplete`|看 `failure_ledger.csv`；缺失、空文件、坏媒体、越界路径仍在计划分母里，修正后新跑|
|退出 0，`confirmed_or_declared_cross_split`|有确认/声明跨集合关系；核对来源，审阅划分建议|
|退出 0，`needs_review` / `no_candidates_found`|检查候选、失败与覆盖；不据此宣布来源独立|
|退出 2，`blocked` 或 `failed`|看终端原因与可写新目录内的 `result.json`；修正后换新目录|
|`REVIEW_BINDING_MISMATCH` / `ARTIFACT_MISMATCH`|使用对应版本的完整导出或从原输入重跑，不手改摘要绕过|
|`OUTPUT_EXISTS` / `INPUT_OUTPUT_OVERLAP`|更换输出位置；保护旧目录时可能不写错误封套|

更多实测异常见 [输入验证审查](INPUT_VALIDATION_REVIEW.md)。完整 150 项数据的候选和漏检解释见 [实际案例](CASE_STUDY.zh-CN.md)。
