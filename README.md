# 产品要素审阅与物料更新

可安装的 Codex Skill 与本地 CLI。输入一份合同和至少一份物料，建立可追溯的合同候选底稿，识别每份物料的展示要求，再按审阅决定更新 Word/PPT。

## 一句话安装

把下面这句话发给另一台电脑上的 Codex：

> 从 https://github.com/wtt982887807/product-material-skills 的 skills/product-material-review 目录安装这个 Skill，一并安装 requirements.txt 中的依赖，然后运行 scripts/material_agent.py doctor 验证。

也可以直接提供 [Skill 目录链接](https://github.com/wtt982887807/product-material-skills/tree/main/skills/product-material-review) 并要求安装和环境检查。安装后使用 `$product-material-review`；不需要发送ZIP或编写JSON。

## 使用

> 使用 $product-material-review，按这份合同更新我提供的物料。Word保留修订和批注，PPT只输出目标产品自身的要素页；缺项和冲突请列出来。

- 输入：合同必需；PPT、一页通、海报至少一份。合同支持DOCX、可提取文字的PDF，旧DOC需可信转换副本和双哈希登记；自动回写支持DOCX/PPTX。
- 合同：保留原文、定位、适用条件、候选值及覆盖检查。通用候选不代表全部内容已结构化或核验。
- 物料：逐份识别需求和产品区域；信托合同只更新信托自身，底层资管产品保持独立。混页或身份待核页不会强行整页输出。
- Word：局部真实修订及锚定批注，保留既有审阅痕迹。PPT：保留页内对象和格式，最终仅交付目标产品要素页。
- 个例：检查日期、费率单位、份额/渠道、阶段、经理履历、主体及条款条件等。缺证信息保留待核，AI负责审阅文件。

## 运行要求

Python 3.11+，依赖为`lxml`和`pypdf`。CLI核心本地运行，不需要模型API密钥；语义审阅由当前AI会话执行。最后的原生打开与视觉验收需要Office或等效渲染器。扫描件、图片海报、复杂对象和未识别条款可能需要额外处理。

详情：[安装与迁移](skills/product-material-review/INSTALL.md) · [CLI](skills/product-material-review/references/cli.md) · [审阅依据](skills/product-material-review/references/review.md)。

## 版本与更新

当前公开包1.0.1，核心2.1.0。保留了QDII25的专用解析范围保护；它仅在产品身份与全部选择器适配时补充结构化候选，不代表对所有产品的通用自动核验。公开包的配置不含开发时的合同/物料文件名或历史审阅结论，也不含业务原始文件、任务和缓存。

字段字典位于技能`runtime/config`，逐物料需求每次分析重新生成。更新代码或配置后旧审阅会失效；迁移文件到另一台电脑后重新分析，不能改旧task的路径或哈希冒充迁移。更新已安装版本时保留并复核自己的配置。

## 验证范围

本地核心通过193项测试；已在同一Windows电脑的独立目录、隔离Python环境中验证5种产品/10份物料，以及Word修订批注和PPT选页。该结果不代表所有合同已自动核验或跨操作系统视觉验收通过。发布后另验证从本仓库下载、校验文件完整性及运行doctor。

此仓库提供本地Skill和CLI；基金排名项目的网页仍位于独立仓库。
