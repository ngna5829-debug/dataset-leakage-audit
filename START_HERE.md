# 从三份素材开始

你要回答的是：同一份或同源素材是否混进不同集合，哪些关系已有证据，哪些还需要复核。完成练习后，你会得到一份审计报告、一份正式复核版本和一张划分建议表。

1. 打开 [Mac / Python 3.12 完整操作](docs/GETTING_STARTED.zh-CN.md)，进入仓库、创建 `.venv` 并安装依赖。`.venv` 是这个项目单独使用的 Python 环境。
2. 按文档先复制三份仓库自绘素材到 `my_data/media/`，再写 `my_data/manifest.csv`。清单中的 `path` 写 `media/original.png` 这样的目录内相对路径；不能用绝对路径、`../` 或越界符号链接。
3. 顺序运行 `validate` 和 `audit`，分别生成 `runs/my_validate_01`、`runs/my_audit_01`。前者检查格式和能否解码，后者才扫描素材对。每次使用新输出目录。
4. 打开 audit 的 `review.html`，为一对素材填写判断、可复核证据和代号，导出 JSON。保存为 `review_inputs/my_reviews.json`；浏览器草稿还不是正式复核。
5. 用 `import-reviews` 生成 `runs/my_reviewed_01`，再用 `propose-split` 生成 `runs/my_proposal_01`。核对 `split_proposal.csv` 中同字节两项的组 ID 和建议 split 是否一致。

不要把 validate 的目录交给后两步：程序会以 `PAIR_SCAN_REQUIRED` 阻断。不要编辑已完成目录里的 JSON/CSV 来“修好”结果；导入会检查原清单、配置、审计快照、复核绑定和工件 SHA。修改输入后另跑新版本。

四层证据要分开读：SHA 相同是文件字节相同；dHash 相近只是画面候选；`source_group` 是提供者的同源声明；`duplicate` / `related` 是有依据的复核判断。同提示词和同风格都不自动确认同源。A–B、B–C 连通时可整体隔离，但不能因此写成 A–C 已确认重复。

建议表不会移动或删除文件，也不会修改原清单。保留素材和审计版本，先审阅建议再决定自己的数据管理流程。`status=completed` 表示命令执行完成，风险要看 `quality.verdict`、失败记录和未复核候选。

跑通小例后，阅读 [150 项实际案例与失败分析](docs/CASE_STUDY.zh-CN.md)，再选 [一个独立增量挑战](MY_WORK.md)。公开样例全为构造素材，真人复核人数为 **0**。

[统一教材 PDF（发布预留）](https://github.com/ngna5829-debug/edit-regression-lab/releases/download/v2.0.0/aigc-evaluation-textbook-v2.pdf)：待统一发布与下载验真，当前链接仅为发布预留。
