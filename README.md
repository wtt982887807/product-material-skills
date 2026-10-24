# 产品要素审阅与物料更新

可安装的 Codex Skill 与本地 CLI。合同确定目标产品，所选近似模板作为正文底稿；保留原句、顺序和格式，只按合同最小修改事实。先建立完整合同原文索引，再按模板需求核对、补提和回写，无需先制作全量要素表。

## 一句话安装

把下面这句话发给另一台电脑上的 Codex：

> 从 https://github.com/wtt982887807/product-material-skills 的 skills/product-material-review 目录安装这个 Skill，一并安装 requirements.txt 中的依赖，然后运行 scripts/material_agent.py doctor 验证。

也可提供 [Skill 目录链接](https://github.com/wtt982887807/product-material-skills/tree/main/skills/product-material-review) 并要求安装与环境检查。安装后使用 `$product-material-review`，不需要发送 ZIP 或手写 JSON。

## 使用

> 使用 $product-material-review。合同是目标产品，其他文件是近似模板：保留模板原句，只按合同局部更新事实。合同未提的日期、代码、业绩报酬等参数保留原值，只标黄关键参数并加简短待确认批注。Word 先接受旧修订，再生成本次修订；PPT 只输出自身全部要素页及续页。请分开列修改清单与待确认清单，并验收实际文件。

- 输入：合同必需，PPT、一页通、海报至少一份。合同支持 DOCX、文字 PDF；旧 DOC 需可信转换及双哈希登记。自动回写支持 DOCX/PPTX。
- 目标与范围：合同决定唯一目标。模板6号、合同7号时直接按合同制作；先选择模板自身主体，再更新其中内容。信托底层及其他产品要素不因与合同同名而被选中。
- 原句与事实：新任务默认 `editing_policy=template_minimal`。只改名称、数值、代码及合同要求改变的条件；同一评级主体、同一口径的 R 值与风险描述一起核对。中文措辞或条件确需变动时说明 `wording_change_reason`；CLI 裁掉替换首尾相同文字，减少无必要的 Word 修订。
- 缺项：完成合同回查后仍无具体日期、代码、费用等参数时，保留模板原值，局部标黄并实际批注“合同未提，保留模板值待确认”，记录为 `template_pending`。它不算已核实或完成，不默认整格换成待补充，也不要求用户先补齐才能出稿。合同明确约定、不收取或不适用的内容仍按合同纠正。
- 定位：重复数值使用原始槽文的零基 `start`，不扩大为整段修改。批注、标黄和保留片段相互绑定；同槽已知费率可以修改，未知日期继续保留待确认。
- Word：`analyze` 在工作副本中原生接受模板历史修订，再以清洁基线生成本轮真实修订、标黄和批注；上传原件及原批注保留，本轮修订供用户审阅。合同不执行该准备操作。
- PPT：保留对象和格式，只导出选中主体全部要素页及续页，排除信托底层；范围待核时先解决范围，不改交完整 PPT。
- 文件：用户文件在 `delivery/`，内部完整稿在 `internal/`。所有文件名产品按合同更新，保留可靠模板渠道、日期和类型；普通 PPT 追加一次“产品要素”，一页通和海报不额外追加。重名按物料 ID 子目录区分。
- 核对：一格的费率、计提、支付等分别核实。未知字段可在本任务定义并回查合同，字典不是白名单。合同补提与用户真实补充分开保存；已确认静态标签可自动保留以减少重复。
- 验收与交付：核对真实修改跨度、原值、黄色范围、原生批注、Word 修订及版面。文件 `ready` 可以同时带 `pending_draft` 并交付；待确认参数单列，不把“已出稿”当作“事实全部核实”。

## 运行要求

Python 3.11+，依赖为 `lxml` 和 `pypdf`。CLI 核心本地运行，无需模型 API 密钥；语义审阅由当前 AI 会话执行。含历史修订的 Word 模板需要 Windows Microsoft Word 来接受旧修订；普通无修订文件无需该预处理。原生打开与视觉验收需要 Office 或等效渲染器。扫描件、图片海报、复杂对象和未识别条款可能需要额外处理。

详情：[安装与迁移](skills/product-material-review/INSTALL.md) · [CLI](skills/product-material-review/references/cli.md) · [审阅依据](skills/product-material-review/references/review.md) · [缺项标黄与混合示例](skills/product-material-review/references/template-retention.md)。

## 版本与更新

当前公开包 1.3.0，核心 2.4.0。本版改用模板正文驱动的最小修改：缺项原值保留、参数局部标黄与简短批注；重复值精确定位；已知与未知参数可在同槽分别处理；事实状态与文件生成状态分开。旧 `template_retained` 白名单只兼容历史格式，新模板任务采用 `template_pending`，不计入合同核实或完成数。

继续保留原文定位及来源校验、批量回查、PPT 自身全部要素续页、合同产品命名和 Word 历史修订准备。优先用 `review-packet` 和 `--compact` 读取所需部分；字段回查用 `--batch`，已定义的待确认字段用 `search-contract --pending-field`。完整索引留在磁盘，减少重复读取，不减少实际核对及最终验收。

公共字段字典位于技能 `runtime/config`，逐物料需求由实际模板生成。更新引擎或配置后重新分析；迁移输入到另一台电脑后也重新分析，不能修改旧任务的路径或哈希冒充迁移。公开包不含业务原件、本机路径或历史审阅结论。

已安装电脑不会自动同步 GitHub。可发送：

> 请将我已安装的 product-material-review 更新为 wtt982887807/product-material-skills 中的最新版本；先备份现有版本、保留并检查自定义配置，更新完整 Skill 目录和依赖，运行 doctor，并报告安装版本。

## 验证范围

回归覆盖跨产品模板选择、信托主体隔离、续页完整性、合同命名、复合需求及新字段补提、精确文字跨度、缺项标黄批注和状态区分。发布时验证独立目录安装、文件完整性、doctor 和实际 Office 样本；具体合同仍需语义审阅及实际成品验收，不能用测试通过代替。

此仓库提供本地 Skill 和 CLI；基金排名网页位于独立仓库。
