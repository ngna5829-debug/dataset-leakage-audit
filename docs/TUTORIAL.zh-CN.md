# 项目四：数据来源与划分审计，找到同一份素材的不同版本

你做了一个图片生成评测集：训练材料有一张海报，测试材料中又放入它的缩小版。文件名不同、像素不同，题目看似增加了一道，来源却没有增加。类似问题还包括同一底图的裁切、同一视频的转码，以及同一次拍摄的连续画面。本章用 `dataset-leakage-audit` 建立清单、寻找候选、记录复核、提出按来源组划分的建议。交付物是可追溯的 JSON、CSV、SQLite 数据库与中文 HTML 报告。

这对应评测岗位中的素材入库检查、来源记录、训练与测试划分检查、重复候选复核和数据问题反馈。完成后，你应能当场演示“复制一张图进入 test，程序怎样发现它；发现之后怎样留下证据”。本章是完整 50 周路线的**第 43—46 周，每周约 15 小时，共 60 小时**。先修路径、列表与字典、CSV/JSON、集合交并集和比例；不会 SQL 也能完成第一轮。

|周次|先理解什么|主要实践|当周验收|
|---|---|---|---|
|43|文件、资产、来源与集合|环境、三资产清单、validate/audit|解释每个字段与输出|
|44|字节、视觉、文本、时间顺序|手算指纹与混淆矩阵，读真实报告|保留漏检和误召回|
|45|复核证据、连通关系与分组|导入记录，生成并检查建议|解释链式传播和比例偏离|
|46|独立增量与证据边界|完成一个挑战，整理展示材料|另一人可按说明复算|

每周可分为概念与手算 4 小时、操作与观察 7 小时、故障解释与记录 4 小时。这是学习安排，不是求职保证。项目当前公开材料全部来自构造过程，独立真人复核人数为 **0**；开发与审查角色也不是人类评测者。

## 1. 先把“泄漏”说具体

资产是清单中的一项，例如 `original`。文件是磁盘中的 `original.png`。来源是它怎样产生，例如同一张原始照片及其派生版本。集合 `split` 表示计划用途：train 通常用于训练，calibration 用于确定规则，test 用于检查冻结规则后的表现。但本项目只读取这些标签，既不训练模型，也没有拿这份数据训练近重复分类器。公开案例中的 calibration 不能证明 dHash 门槛经过了自然数据校准。

同源变体分到训练和测试两边，可能让评测看起来比独立来源测试容易。这里能检查的是**你提供的素材集合之间的关系**。如果不知道某个商业模型的训练清单，找到一张相似图不能证明它被用于训练；本程序也不执行训练成员推断。源码、媒体 SHA、来源声明和人工意见各自有作用，不能互相替代。

`source_group` 是输入者的来源声明，应该对应底图、拍摄批次或原始视频等明确对象。把所有“蓝色海报”写成一组，是用风格替代来源；把不确定项都写成 `unknown` 才符合未知状态。`known_group()` 不会将 `unknown`、空字符串、`未知`、`none`、`null` 合并成一个大组。文件有授权和文件同源也是两个问题，许可证字段只是记录，不是自动认证。

## 2. 代码在哪个窗口运行

在 Mac 按 Command＋空格，搜索“终端”。终端接收命令；Python 解释器执行 `.py` 文件；浏览器显示生成的报告。下载独立仓库后，输入 `cd `，把 Finder 中的项目文件夹拖进终端再回车。若你现在位于完整教材包根目录，先执行下面第一行；若在 `book/`，改用 `cd ../projects/dataset-leakage-audit`。进入独立项目根后不要再重复切换。

```sh
cd projects/dataset-leakage-audit
pwd
ls audit.py leakage_audit.py requirements.txt
python3.12 --version
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python audit.py --help
```

如果已安装uv，但终端找不到`python3.12`，可用下面这一条替代建环境命令，再继续后面的`.venv/bin/python`命令；无需为此重装评测代码。未装uv时先读教材环境导学。作者实测使用已有Python 3.12.14解释器，没有执行uv系统安装。

```sh
uv venv --seed --python 3.12 .venv
```

`pwd` 应以 `dataset-leakage-audit` 结尾；`ls` 应找到三个文件；版本应为 3.12.x。找不到解释器时先安装 Python 3.12，再打开终端。`venv` 创建 `.venv/`，让本项目的解释器与依赖有明确位置；安装会列出 Pillow、NumPy、OpenCV 等包，`pip check` 应显示没有损坏依赖，`--help` 会列出五个子命令。后续都写 `.venv/bin/python`，避免在一个解释器里安装、用另一个运行。虚拟环境不是虚拟机；搬动目录后宜重建环境。[Python venv 官方说明](https://docs.python.org/3.12/library/venv.html)

默认路线使用本机 CPU，不下载模型，不需要 GPU。MPS 是 Apple GPU 的计算后端，CUDA 是 NVIDIA GPU 路线，本项目没有对应的模型设备开关。虚拟机运行另一套操作系统，需要在里面另装 Python 并处理路径；它不是运行本例的前置条件。本章实际验证的是 macOS，未验证 Windows、CUDA、MPS 或虚拟机路线。首次安装依赖需要网络，安装后的审计离线运行。

报告是本地 HTML，`open` 负责交给浏览器，默认无需 HTTP 服务。HTTP 服务的职责是通过网址响应浏览器请求，并不会替你执行 Python 审计；GitHub 文件页显示源码也不等于报告运行。`audit.py` 只是入口，调用 `leakage_audit.py:main()` 解析命令，再转交实际函数。

## 3. 先做三个资产，理解 manifest

manifest 是媒体清单，一行描述一项资产。先用已有 CC0 自绘素材，避免把不存在的示例路径误当真实输入。以下都从项目根执行；已有同名个人目录时换一个名称，并同步修改后续命令。

```sh
mkdir -p my_data/media
cp examples/demo_v2/input/media/f00_original.png my_data/media/original.png
cp my_data/media/original.png my_data/media/renamed.png
cp examples/demo_v2/input/media/hard_00.png my_data/media/independent.png
ls -lh my_data/media
cat > my_data/manifest.csv <<'CSV'
asset_id,path,split,source_group,media_type,text,license_note
original,media/original.png,train,unknown,image,,仓库CC0自绘素材
renamed,media/renamed.png,test,unknown,image,,original的字节副本
independent,media/independent.png,calibration,unknown,image,,仓库CC0独立绘制素材
CSV
```

`mkdir` 建目录，三条 `cp` 复制文件，`ls -lh` 应显示三个非空 PNG。最后的完整命令块写出 CSV，末行 `CSV` 也是命令的一部分。`path` 相对**清单所在的 `my_data/`**，所以 `media/original.png` 指向 `my_data/media/original.png`，不随终端当前目录变化。

|字段|本例值|程序怎样使用|
|---|---|---|
|`asset_id`|original|唯一字符串身份，重复或空 ID 拒绝|
|`path`|media/original.png|清单目录内的相对媒体路径|
|`split`|train|必须属于配置声明的集合|
|`source_group`|unknown|必有字符串字段；未知不自动合并|
|`media_type`|image|可省略并按扩展名推断，支持 image/video|
|`text` / `prompt`|本例为空|文本检索线索；空文本不做文本匹配|
|`license_note`|仓库CC0自绘素材|来源许可说明，程序不判断授权有效性|
|`parent_asset_id` / `provenance`|本例省略|保留派生与来源说明，不自动确认关系|

提供的可选字段也必须是字符串；JSON 的 `null` 不是空字符串。除 CSV 外支持 JSONL，即每行一个完整对象，例如 `{"asset_id":"a","path":"media/a.png","split":"train","source_group":"unknown"}`。不能把普通 JSON 数组改个扩展名就当 JSONL。`load_manifest()` 读取清单、补默认值；重复 CSV 表头、错列数、重复 JSON 键、NaN 数值都会拒绝。`confined_path()` 拒绝绝对路径、任何 `..` 分量和指向目录外的符号链接。自己的素材应先合法复制到清单目录下，再写相对路径。

## 4. validate 与 audit，零候选可能表示尚未比较

```sh
.venv/bin/python audit.py validate --manifest my_data/manifest.csv --out runs/w43_validate
.venv/bin/python -m json.tool runs/w43_validate/result.json
.venv/bin/python audit.py audit --manifest my_data/manifest.csv --out runs/w43_audit
open runs/w43_audit/review.html
```

第一条检查字段和媒体解码，会生成 `audit.json`、清单快照、失败表、HTML 和 `result.json`。本例预期 3 项计划、3 项可读、0 个候选，质量状态 `validation_only_no_pair_scan`；`json.tool` 将封套排版到终端。这不是“没有重复”，而是 `run_audit()` 在 `operation="validate"` 时设置 `pair_scan_completed=false`，跳过素材对扫描。

第三条才调用 `candidate_pairs()`。本例预期候选 1 对，即 `original–renamed`；它们原本分属 train/test，所以跨集合确认边为 1，质量状态为 `confirmed_or_declared_cross_split`。浏览器应看到这两张相同的图。检查路径、尺寸、SHA 和命中理由，比单看总数更有用。普通命令退出 0 只表示报告生成完成；即使发现风险或个别媒体失败，也可能退出 0。

每次使用新的输出目录，重跑用 `_02` 等后缀。`fresh_out()` 会拒绝已有内容；失败后也换新目录。输出不能包含原清单或素材，也不能嵌入旧审计目录。这样可保留旧证据，防止新旧结果混在一起。

|输出|打开后看什么|不能据此声称什么|
|---|---|---|
|`result.json`|status、quality、counts、SHA 清单|退出成功不等于来源独立|
|`audit.json` / `audit_summary.json`|完整资产、关系、复核与数量|候选数量不等于重复数量|
|`candidate_edges.csv` / `exact_leaks.csv`|全部候选／跨集合同字节边|后者没有列出全部同源风险|
|`failure_ledger.csv`|失败资产、阶段、代码、原因|读不到的文件不能算 clean|
|`manifest.original.*` / `manifest.snapshot.jsonl`|原清单字节／规范化字段|快照不是原始媒体备份|
|`audit.sqlite` / `review.html`|查询表／证据与复核页面|缩略图不能替代视频全片|

代码中 `run_audit()` 给每项添加 `sha256`、`decode_status`、图像或视频特征；单项解码异常进入 ledger 后继续。`save_audit()` 保存正式工件，`summarize()` 计算分母，`write_result()` 区分执行和质量。整体字段错误在 `main()` 被捕获，通常退出 2，并在可写的新目录保存 blocked/failed 封套；保护已有目录时不强行写错误文件。

## 5. 四种线索，各自在问什么

**SHA 问的是文件字节是否相同。** `sha256()` 每次读 1 MiB，通过 `hashlib` 累计摘要。改文件名不改字节，逐字节复制的两图摘要相同；JPEG 重编码通常改变字节。项目把同 SHA 当精确字节关系，并纳入保守分组。摘要不是作者签名，不能认证版权或训练使用史。Python 的 `update()` 与 `hexdigest()` 分别负责累计字节和输出十六进制摘要。[hashlib 官方说明](https://docs.python.org/3.12/library/hashlib.html)

还有一个 `canonical_pixel_sha256`：图片按 EXIF 方向转正，转为 RGBA，把原尺寸与原像素一起计算摘要。它没有先缩放。PNG 与 BMP 可以像素完全相同而文件字节不同；项目将这种像素一致留作候选，避免把编码层与来源层混写。

**dHash 问的是粗略亮暗排列是否接近。** `dhash()` 先把透明图复合到白底，转灰度，缩为 9×8；每行比较相邻 9 个点，得到 8 个“右边是否更亮”的真假值，8 行共 64 位。以灰度 `[10,20,15,15]` 为玩具例子，比较结果是 `[1,0,0]`；相等时不是 1。`hamming()` 对两指纹逐位异或，再数不同位数，例如 `0011` 与 `0101` 距离为 2。默认距离不超过 6 就进入图像候选。

这 64 位没有保存完整颜色、形状或语义。纯红和纯蓝图虽颜色不同，每行内部亮度恒定，dHash 都可能全为 0。它们距离为 0 仍不能自动确认重复；裁切则改变布局，可能让真实派生图距离变大。阈值是检索规则，不是“重复概率 94%”。

**文本 n-gram 问的是连续字符片段重合多少。** `normalize_text()` 用 NFKC 统一兼容字符、casefold 处理大小写，去掉 Unicode 标点、分隔和控制字符；中文仍保留。默认规范化文本至少 8 字才比较。`text_ngrams(text,3)` 把“红杯放在桌子左边”变成“红杯放、杯放在、放在桌、在桌子、桌子左、子左边”六个片段组成的集合。重复片段只算一次，没有中文分词或语义模型。

Jaccard 值是 `交集大小 / 并集大小`。两份片段集合各有 6 个，共有 5 个，则并集为 `6+6-5=7`，相似度 `5/7≈0.714`，低于默认 0.8；完全相同则为 1。同提示词可能生成不同图片，近义改写也可能没有足够字符交集，所以文字只能提示复核方向。

**有序视频指纹问的是一段顺序能否对齐。** `video_features()` 顺序解码整段，默认每 0.5 秒记录一项 `frame_index,time_s,dhash`；`ordered_video_match()` 枚举固定采样偏移，找连续匹配。默认同时要求单帧距离≤6、连续≥3、匹配数占较短序列≥0.75、较短与较长时长比≥0.8。它没有把帧排序后忽略时间。

把互不相同的指纹简写为 A、B、C、D：`[A,B,C,D]` 与 `[X,A,B,C]` 能以一个采样间隔偏移连续匹配三项，比例 `3/4=0.75`。对应时间可为 `(0,0.5)、(0.5,1)、(1,1.5)` 秒。反序 `[D,C,B,A]` 保留同一批内容，却没有足够的有序连续匹配；局部片段还可能被时长比挡住。

视频超过默认 900 帧会记 `VIDEO_LIMIT`，不会只扫前半段却声称整片已检查。OpenCV 时间不单调时，代码尝试 `frame_index/declared_fps` 回退，并记录 `timestamp_method`；这不等于已验证容器真实 PTS 或可变帧率精度。文件无法打开、声明帧数无效、解码不完整分别走明确异常路径。四类线索在 `candidate_pairs()` 中取并集，一对可有多个理由，但只占一行候选。

## 6. 手算阈值，先分清误召回与漏检

下面是人为指定距离的八对练习，**不是新增图像实测**。真值正例表示已知同源，负例表示已知独立；“预测正”只表示距离≤门槛、进入候选，不是最终重复判决。先遮住末列，按默认门槛 6 自己填写。

|pair|来源真值|距离|门槛 6 的结果|
|---|---|---:|---|
|P1、P2、P3|同源|0、2、5|TP：正确召回三对|
|P4|同源|9|FN：漏掉一对|
|N1、N2|独立|1、6|FP：多送两对复核|
|N3、N4|独立|10、12|TN：正确排除两对|

混淆矩阵按真值与预测分格：TP=3、FN=1、FP=2、TN=2。候选 precision 为 `TP/(TP+FP)=3/5=0.6`，意思是五个召回候选中三个确为同源；recall 为 `TP/(TP+FN)=3/4=0.75`，意思是四个已知同源关系找回三个。命中规则本身不能同时充当真值，否则会循环证明自己正确。混淆矩阵的真值／预测方向可对照 [scikit-learn 官方定义](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.confusion_matrix.html)，本练习不需要安装该包。

门槛改成 2 时，TP=2、FP=1、FN=2、TN=3，precision=2/3，recall=1/2；改成 10 时，TP=4、FP=3、FN=0、TN=1，precision=4/7，recall=1。宽松门槛减少这组漏检，却增加复核负担。选择应依据任务成本和独立校准集，冻结后再看测试集；不能不断看 test 调到满意再称为泛化性能。

还有两个分母陷阱。只复核已召回候选，找不到 FN，不能计算完整 recall。把“不是已知正例”的所有对都当负例，也没有依据。`precision_on_adjudicated_candidates` 只使用正式复核中非 unsure 的关系，若一条标签也没有，结果应为 `null`，不能填 0 或 1。正式复核只有方便样本时，这个比例仍不能外推整个素材库。

## 7. 逐项读懂 150 资产报告

公开材料在 `examples/demo_v2/`。`build_demo()` 自绘 24 个图片来源，每个有原图、改名副本、JPEG、缩放、裁切，得到 120 张；加 18 张独立同风格困难反例，共 138 图。4 个视频来源各有原片、再次编码、反序版，共 12 段，合计 150 项。真实构造标签单独存在 `input/construction_truth.json`；候选函数不读取它来决定答案。

```sh
.venv/bin/python -m json.tool examples/demo_v2/audit_summary.json
.venv/bin/python -m json.tool examples/demo_v2/construction_metrics.json
open examples/demo_v2/review.html
```

这三条读取已有结果，不会重新生成媒体。汇总应为计划／可读 150/150，原划分 train 50、calibration 46、test 54。所有可能 pair 数为 `150×149/2=11,175`；候选 **397** 对，其中跨集合 **304** 对。132 条边具有同字节或来源声明依据，其中跨集合 108 条。初始正式复核 0，precision 分母 0、值 null。跨集合候选不是已确认泄漏，108 条也不是 108 名真人的意见。

同源真值如何得到 252？五成员图片家族内有 `5×4/2=10` 对，24 家族贡献 240；三成员视频家族有 3 对，4 家族贡献 12，共 **252**。候选检索找到 **244**，漏掉 **8**，这个分母包括漏检。244/252 是此固定夹具上全部规则联合检索的比例，不能换成自然数据召回率。

|代表性关系|已知对数|全部规则找回|仅媒体方法找回|
|---|---:|---:|---:|
|原图—改名|24|24|24|
|原图—JPEG|24|24|24|
|原图—缩放|24|24|24|
|原图—裁切|24|24|**2**|
|原片—再编码|4|4|4|
|原片—反序|4|0|0|
|再编码—反序|4|0|0|

表中只是代表性分类，不能相加代替 252。**裁切 2/24 很差**：其余 22 对靠文本或来源声明进入候选，并没有被视觉方法救回。若只写“裁切全部找回”，会掩盖算法缺口。反序视频的两种配对各漏 4，对应全部 8 个漏检；“同源”与“相同播放顺序”显然不是同一问题。

18 个困难负例彼此形成 `18×17/2=153` 对，**153 对全部进入候选**。它们由构造过程记录为独立来源，提示词和风格相近导致复核负担；程序没有自动把它们判重复。未对所有其余 pair 做逐对人工标注，因此不能补出完整自然数据 TN 或准确率。

`summarize()` 还计算与 train 有直接确认／声明关系的可读 test 资产：本例为 24/54≈0.4444，按 test 资产去重。一个 test 同时连到三个 train，分子仍只加一；单纯通过中间节点传递连通也不增加这项直接关系计数。这个数是当前输入及证据政策下的下界；错误来源声明会影响它，不能拿 304/11,175 代替，也不能据此证明某模型训练污染。

公开 `fixture_reviews.json` 只有一条 related、一条 distinct，两条均为 `construction_truth_fixture`。导入后的 0.5 是 1/2 的构造标签比例，真人仍为 0。需要生成自己的完整构造 demo 时，可另跑 `.venv/bin/python audit.py demo --out runs/w44_demo`；它会创建 `input/` 和整套报告，字节以本机版本和 SHA 为准。本章作者未重跑完整 demo，核对的是实际保存的公开工件。

## 8. 人工复核如何成为正式记录

在三资产报告选择“跨集合”，看两图、尺寸、原 split、全部理由与阈值。图片候选看完整原图；视频首帧相似还不够，要打开原片核对顺序、来源记录和匹配时间。证据可以是原始工程文件、授权素材编号或自己保存的复制／变换记录。只有相似感而没有来源依据时，选择 unsure。

`duplicate` 表示确认同内容复制，`related` 表示确认同源派生，二者新增分组约束；`distinct` 表示不同来源，`unsure` 表示证据不足，后二者不新增约束。给本例字节副本填写 duplicate 时，写明复制过程与 SHA，填实际复核者代号。浏览器草稿存在本机；保存草稿或刷新恢复不等于正式导入。导出后确认文件真正落盘，必要时把页面导出文本另存为 UTF-8 纯文本 JSON。

```sh
mkdir -p review_inputs
cp ~/Downloads/my_reviews.json review_inputs/my_reviews.json
.venv/bin/python -m json.tool review_inputs/my_reviews.json
.venv/bin/python audit.py import-reviews --audit runs/w43_audit --reviews review_inputs/my_reviews.json --out runs/w45_reviewed
.venv/bin/python -m json.tool runs/w45_reviewed/audit_summary.json
```

先把实际导出文件命名为 `my_reviews.json`。复制成功后，`json.tool` 应能解析；导入成功产生新版本，若只填一对，`reviewed_pairs=1`。真实本机意见标记 `human_local`，自动化演示必须保留 `construction_truth_fixture`，程序不认证填写者身份。最后一条读回正式数量，才能确认记录进入结果。

`import_reviews()` 先调用 `load_audit()` 校验完整旧工件，再检查导出文件的 `manifest_digest`、`input_digest`、`config_digest`。它们分别绑定原清单字节、审计时的规范化资产与媒体 SHA、有效配置。任何一项不匹配都整批拒绝。`validated_reviews()` 还检查 pair 是否存在、decision 合法、证据／复核者／origin 非空，同一 pair 不能在一批出现两次；新版按 pair 合并旧记录，修改意见不增加“人数”。

漏检也可以记录。在绑定当前版本的 JSON 中添加 `manual_pairs`，条目包含 `asset_a`、`asset_b`、非空 `reason`；例如依据工程记录补入一对反序视频。手工条目先成为候选，不自动确认同源，可在新报告继续复核。不要猜 pair ID 或手改摘要。distinct 也不能撤销同字节或输入来源声明；来源填错应修正新清单再 audit。

下游校验读取的是**保存的审计快照**，不会重新读取此刻磁盘上的原始媒体。原文件变了，旧报告的 SHA 仍只说明旧记录，应重新 audit。普通输出没有完整备份素材；同时保留输入、配置、复核文件和结果才方便追溯。SHA 检查可以发现不一致，不构成身份认证或来源真实性证明。

## 9. DSU 连通、错误链传播与来源组划分

把资产画成点，把已经接受的关系画成线。`accepted_edges()` 只接受同字节、非未知来源声明、正式 duplicate/related；像素、dHash、文本和视频候选本身不进入这个集合。`components()` 使用 `UnionFind`，也叫并查集或 DSU，把由这些线连通的点放进一个组。`possible_components` 则使用全部候选，目的是显示待查范围，不能直接冒充确认组。

如果确认 A–B、B–C，A、B、C 会同组，但程序并未生成 A–C 重复标签。再加 A–D，共三条边形成四节点组，仍然只有三条关系证据。假设 A=train、B=calibration、C/D=test，直接 train 关联 test 的只有 D，所以相应下界为 1/2；保守划分却要把四项放在一起。这解释了“隔离整个连通组”和“统计直接证据”为什么不能混算。

连通也会放大错误。若本来有两个正确家族 A–B 和 C–D，只需误把 B–C 填为 related，就会把两家族全连起来。扩大 dHash 阈值还只会扩大候选；把候选误当确认边才会让错误进入分组。应优先复核连接大组的桥接关系，记录理由，发现误判后导入修正意见生成新版本；已有同字节／声明约束仍要从对应证据源纠正。不能靠把组内每对都补成“重复”来修饰链条。

```sh
.venv/bin/python audit.py propose-split --audit runs/w45_reviewed --out runs/w45_proposal --seed 17 --ratios 0.7,0.15,0.15
.venv/bin/python -m json.tool runs/w45_proposal/split_proposal.json
open runs/w45_proposal/split_proposal.csv
```

`propose_split()` 打乱组的同大小顺序后按组大小降序，逐组尝试三个去处，选择使“分配后资产数与目标数之差的平方和”最小的位置。seed 17 固定这个顺序；比例按 `declared_splits` 顺序提供，必须全为正且总和为 1。它优先保持整组，不能保证类别均衡或精确比例。

三资产例只有大小为 2 和 1 的两个组，实跑建议 train 2、calibration 1、test 0。目标 2.1、0.45、0.45 不可能用三个不可拆分资产精确实现；JSON 未出现的 test 计数按 0 读。CSV 应有三行，两份字节副本 `group_id` 和 `proposed_split` 一致，每行状态 `proposal_only`。公开 150 项建议为 90 组、train 105、calibration 23、test 22，104 项建议改变原 split；未确认的同源关系仍可能分开。

proposal 只是建议表，**不会移动或删除文件，不重写原清单**。验收应独立检查每个计划 ID 恰好一次，全部接受边两端同组同 split，再比较目标与实际数。`result.json` 的 counts 仍描述上游审计，建议后的数量在 `split_proposal.json`；不能拿前者宣称风险已消除。建议目录也不是下一次 `import-reviews` 可用的 audit 源。

## 10. 三类真实边界，把错误变成教学证据

**边界一：validate-only 不能生成可靠分组。** 旧版本把六个同字节、同来源项 validate 后直接分配，因没扫描边而拆组。当前 `require_pair_scan()` 同时挡住建议与复核导入。可运行以下故意错误命令，预期退出 2、新目录封套 blocked、警告 `PAIR_SCAN_REQUIRED`，且没有建议 CSV；正确修复是先 audit。

```sh
.venv/bin/python audit.py propose-split --audit runs/w43_validate --out runs/w45_expected_block
cat runs/w45_expected_block/result.json
```

**边界二：失败媒体不能变成成功证据。** 两个零字节文件的摘要天然相同，旧实现曾把它们形成同字节边；当前 `run_audit()` 先检查大小，留下两条 `EMPTY_MEDIA`，SHA 为 null，不产生该精确关系。越界路径则是 `MEDIA_PATH_ESCAPE`。在三资产清单副本中把 independent 路径改为 `../outside.png`，再 audit 到新目录，计划仍为 3、可读变 2、总 pair 仍为 3、可读 pair 为 1，quality=incomplete。用 `cat 新目录/failure_ledger.csv` 定位原因，修路径后新跑，不能删失败行美化覆盖率。

**边界三：长链与坏复核不能悄悄通过。** 旧递归查根在 1000 项合法长链上出现 `RecursionError`；当前 `UnionFind.find()` 用循环找根并压缩路径，既有回归已验证全部归入同根。另一个已修复问题是复核顶层 `[]` 或 `reviews=[null]` 曾触发无封套崩溃，当前退出 2、阻断整批；手工理由 null 也不再被转成文字接受。先用 `json.tool` 检查语法，再按模板核对结构；语法合法不代表字段合同合法。

这些修复的现有核心记录为 41 项测试通过，见项目 `docs/INPUT_VALIDATION_REVIEW.md`。本章没有重跑全套后声称新增 41 项证据；本章另做了三资产、阻断、越界与空媒体轻量检查。dHash 纯色碰撞、裁切差、反序漏检仍是保留的能力边界，不应写成已经修好。

## 11. 先预测再修改：练习与参考答案

**练习 A：只改一个来源字段。** 把三资产清单另存，给 original 和 independent 同填 `exercise_group`，renamed 保持 unknown，其他字段不变。先画图预测，再用新输出 audit 和 propose。参考答案：original–renamed 的字节边仍存在，新声明连起 original–independent，三项成一个组；候选预期增加到 2，对应来源声明与精确字节。这个声明是练习故意填写，不能当真实同源结论；结果说明错误元数据可以越过视觉检索直接影响划分。

**练习 B：只改门槛并拆开理由。** 复制已保存 `config.json` 到 `my_config_02.json`，只将 `image_hamming_max` 改为 0，再使用 `--config my_config_02.json` 跑新 audit。预测三资产精确副本会不会消失。参考答案：不会，同 SHA 已足够入候选；不能由总候选不变推断门槛无效。要检查每条 `reasons` 中的 `image_dhash`，再用本章八对表计算 t=2、6、10 的混淆矩阵。若复核导入报配置摘要不一致，应导出新轮意见，不复制旧摘要绕过。

**练习 C：写一个只读小扩展。** 新建自己的 `count_methods.py`，读取 `audit.json`，先遍历 `candidate_edges`，再把每对 reasons 中的 method 放入集合，累计每种方法命中了多少对。参考做法是两层循环加 `Counter`；同一对最多给每种方法计一次，同时另报候选并集数量。验收时方法计数之和可以大于 397，但并集仍应等于 397。若把方法计数相加当总 pair，回到“一对可有多个理由”的定义。

独立挑战选一个即可：新增裁切检索方法，或写不调用 `components()` 的建议验证器。裁切路线至少增加 30 个合法独立来源，每个做原图、80% 与 50% 保留面积的裁切；按来源分 calibration 10、test 20，另外列至少 30 对独立困难负例。提前冻结门槛，报告 test 的 40 对原图—裁切完整分母、媒体方法检出数、负例候选数、耗时及失败；不要混入文本救回的结果。没有提高召回也可以成为有效分析，只要对照和局限完整。

验证器路线可覆盖单点、同字节两项、A–B–C 链、大组、失败资产、1000 项长链，并主动改坏一份建议：删除 ID、重复 ID、把一条确认边拆到两边、改上游摘要。独立遍历边与建议行，应该逐类报告失败，不能调用被测分组函数再称为独立核验。这些是你的待完成增量，不是当前项目已替你实现的经历。

## 12. 展示、面试与本章验证范围

GitHub 展示应包含输入来源与许可、冻结清单／配置、执行命令、可读报告、逐对失败和个人新增文件。AI 辅助起点与自己完成的增量分别写清楚。只运行已有 demo，可以写“完成复现并解释边界”；只有真实新增、运行并保留证据后，才能写“改进了裁切候选”或“实现了独立验证器”。真人为 0 就写 0，不能把自动化脚本导入写成用户调研。

面试可以围绕四问展开。为什么同一张图换成 JPEG 后 SHA 不同？回答字节编码与像素／来源层次。为什么候选多不一定好？用阈值表说明漏检、误召回和复核成本。为什么 A–B、B–C 同组但没有 A–C 标签？解释隔离约束与直接证据。为什么报告完成仍是 incomplete？指出计划分母、解码失败和质量状态。能打开相应文件定位字段，比背诵名称更能证明理解。

本章实际在已有 Python 3.12.14、Pillow 12.3.0、NumPy 2.3.5、OpenCV 4.13.0 的 macOS 环境，运行 `runtime/bin/python reviews/data_chapter34/verify_chapter.py`。脚本在自己的 `reviews/data_chapter34/run_01/` 执行 8 条 CLI：help、三资产 validate/audit/import-reviews/propose-split、validate-only 阻断、越界路径与零字节审计；另核对纯色 dHash、阈值手算、DSU 小链及公开真值漏检。构造复核 origin 明确，新增真人数据为 0。

证据保存在 `reviews/data_chapter34/verification.json`、逐条 stdout/stderr、`threshold_math.json` 和 `known_misses.json`。本章只读加载公开 audit，并逐文件检查 643 个 examples 文件及核心 SHA 未变；没有重跑模型、完整 demo、全套回归、浏览器交互、全新 venv 安装或远端发布。终端教程中的安装和手工导出是读者操作路线，其执行范围不能与本章已有环境的验证混写。
