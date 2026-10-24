# CLI、安装与迁移

## 安装到另一台电脑

复制整个 `product-material-review` 目录到目标电脑的 `$CODEX_HOME/skills/`；未设置 `CODEX_HOME` 时使用 `~/.codex/skills/`。目录应包含 `SKILL.md`、`agents/`、`references/`、`scripts/`、`runtime/` 和 `requirements.txt`。仅复制 `SKILL.md` 不能运行。面向使用者的简明安装步骤见 [INSTALL.md](../INSTALL.md)。

使用目标机器可用的 Python 3.11 或更新版本，安装依赖后运行环境检查。下面是 PowerShell 示例，`python` 可替换为已验证的解释器路径：

```powershell
$materialSkillHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$materialSkillDir = Join-Path $materialSkillHome 'skills/product-material-review'
$materialCli = Join-Path $materialSkillDir 'scripts/material_agent.py'
python -m pip install -r (Join-Path $materialSkillDir 'requirements.txt')
python -X utf8 $materialCli doctor
python -X utf8 $materialCli --help
```

核心依赖为 `lxml` 和 `pypdf`，不调用在线模型，不要求模型 API 密钥。Skill 文件相对自身定位 `runtime/src` 和 `runtime/config`，不依赖原开发电脑的目录。CLI 可以从其他工作目录启动；命令中的输入/输出路径仍应指向本次实际文件。

不含历史修订的 Word 物料与 PPTX 可直接进行 OOXML 分析和局部修改。含历史修订的 Word 物料需先调用 Windows Microsoft Word 原生接受全部旧修订；目标环境无该能力时受阻。`doctor` 的 Word 检测仅为只读注册信号，实际能否启动和接受仍以准备结果为准。原生打开与视觉验收另需相应工具，`doctor` 通过不等于已完成物料准备或 Office 版面验收。

## 常用命令

每个子命令先用 `--help` 核对当前版本参数。以下使用安装示例中的 `$materialCli`；文件名是调用示例，执行时替换为用户实际文件。

```powershell
python -X utf8 $materialCli analyze `
  --contract '输入/合同.docx' `
  --material '输入/一页通模板.docx' `
  --material '输入/PPT模板.pptx' `
  --material-mode template `
  --out '本次审阅/analysis'

python -X utf8 $materialCli search-contract `
  --task '本次审阅/analysis/task.json' `
  --query '最低保有份额' --query '赎回后份额' `
  --limit 20 --out '本次审阅/合同回查.json'

python -X utf8 $materialCli inspect-review `
  --task '本次审阅/analysis/task.json' `
  --review '本次审阅/review.json' `
  --out '本次审阅/审阅覆盖检查.json'

python -X utf8 $materialCli apply `
  --task '本次审阅/analysis/task.json' `
  --review '本次审阅/review.json' `
  --out '本次审阅/updated_01'

python -X utf8 $materialCli validate `
  --task '本次审阅/analysis/task.json' `
  --result '本次审阅/updated_01/update_result.json'

python -X utf8 $materialCli accept `
  --task '本次审阅/analysis/task.json' `
  --result '本次审阅/updated_01/update_result.json' `
  --qa '本次审阅/updated_01/acceptance.json' `
  --out '本次审阅/updated_01/accepted_result.json'
```

`apply` 自动将完整审阅稿写入 `updated_01/internal/`，把用户文件写入 `updated_01/delivery/`；读取 `update_result.json` 的 `deliveries` 和 `delivery_manifest.json` 获取实际交付路径。命令摘要只列交付结果和阻塞原因，不能把运行成功等同于每份文件已生成或验收通过。执行 `accept` 前，由 AI 按 [验收与交付](qa-and-delivery.md) 实际检查文件并填写 QA；仅检查内部稿不够。

`analyze` 必需一份 `--contract` 和至少一份 `--material`；每份模板重复一次 `--material`。可传 `--as-of YYYY-MM-DD`，省略时使用北京时间当天，日期参数本身不提供最新事实。`--product` 只能确认合同已识别的名称或别名，不能修复未知或冲突身份。

### Word 物料准备

`analyze` 检出 Word 物料的历史修订时，自动准备到 `analysis/prepared_templates/<material_id>/原模板名.docx`，后续槽位、原文定位及回写均基于该工作副本。`source.path`、`source.sha256` 绑定基线，`source.original_path`、`source.original_sha256` 绑定上传原件，`preparation` 保存准备方法、原有/剩余修订数和接受前后批注记录；原件和基线都用于追溯及版本检查。无历史修订时保持原输入路径作为清洁基线。两种情况都不改上传原件，`apply` 生成新文件。`template` 与 `review` 模式均采用此规则，合同不接受或改写修订。

- 检测到历史修订时，在 Windows Microsoft Word 中对工作副本执行原生 `AcceptAllRevisions`，保存后确认旧修订已清理，原批注仍保留。
- 没有历史修订时直接沿用原件作为清洁基线，不要求安装 Office 来完成该步。
- 有历史修订但无可用 Word、原生接受失败或准备校验不通过时明确受阻。不能把手工删除 XML 修订节点当作原生接受，也不能回退到带旧历史的物料继续交付。

准备完成只建立基线，不表示旧物料事实已符合合同。AI 仍逐项核对模板要求；`apply` 从该基线生成本次真实修订与新增批注，保留已有批注。原件、准备副本或引擎改变后重新分析，不手工改哈希维持旧任务。

`search-contract` 在任务绑定的合同原文块中检索，`--query` 可重复，`--limit` 限制返回数量；输出包含命中文本、位置、章节及前后文等来源信息。省略 `--out` 时读取命令输出。AI 仍需阅读上下文并作语义判断；无命中不证明合同没有该项。

`inspect-review` 在回写前检查审阅数据、合同补提绑定、原子需求及槽位覆盖，定位需要继续核对的内容。返回 `semantic_review_complete`、`pending_checks`、`template_coverage`、`requirement_coverage`、`contract_rechecks`；前者只表示记录满足完整核对条件，不证明 AI 判断一定正确，也不代表业务文件已生成或通过原生/视觉验收。它不能替代回写后的 `validate`。完整需求记录见 [需求发现与合同回查](requirements.md)。

这两个只读检查命令的 `--out` 应指定新的结果文件，不能覆盖任务、审阅记录或输入业务文件（包括已登记的合同转换副本和 Word 准备副本）。提供 `--out` 后，完整内容写入文件，标准输出仅返回摘要和文件路径，避免长合同报告占满会话；AI 应继续读取报告中的所需部分。

| 模式 | 输入含义 | 区域选择依据 |
| --- | --- | --- |
| `template`（默认，可省略） | 合同提供目标内容，其他文件提供格式与展示需求 | 先选择模板自身主体及区域，再映射到合同产品；允许跨产品 |
| `review`（显式指定） | 合同与已有同产品物料核对 | 根据合同身份选取对应产品区域；保留同品审阅的身份检查 |

### 多产品模板的选择

AI 读取封面、要素表及上下文，分别确认每份模板的主体。不能因为合同名称出现在模板某页就自动选该页；信托模板中的底层资管页即使与合同同名，也不是信托自身的模板页。清晰的单主体模板可直接分析；需显式选择时由 AI 建立以下映射文件，再在 `analyze` 增加 `--template-map '本次审阅/template-map.json'`：

```json
{
  "templates": [
    {
      "path": "输入/PPT模板.pptx",
      "product_name": "从本次模板实际识别出的主体名称",
      "reason": "封面及主体要素表一致；排除底层产品要素页"
    }
  ]
}
```

路径必须对应本次 `--material` 输入，名称必须来自模板中可观察的主体；映射可仅列需要显式选择的输入。重复映射、非本次输入或凭空的产品名称不能用来绕过选择。业务用户无需写 JSON；只有实际资料无法分辨应采用哪一模板主体时才向用户核实。选择方式、模式及依据随任务绑定，变更后重新分析。

模板模式的 `regions[].status=target` 表示选中的源模板区域，承载的目标内容来自合同；`other` 表示未选模板区域，`ambiguous` 表示选择仍待核。源模板主体与合同目标分别记录，不要求同名，也不能通过修改快照状态把未选区变成可编辑。

### 分类和选页

默认名称含“海报”或 `poster` 的文件归为 `poster`，含“一页通”、`factsheet` 或 `onepager` 的文件归为 `onepager`；其他 PPTX 归为 `ppt`，其余归为 `onepager`。这是初始分类，AI 应按实际用途核对，必要时填写 [review.delivery_plan](review.md#交付计划)。一页通和海报保留模板原文件名；`ppt` 在模板文件名扩展名前追加“产品要素”。重名用物料 ID 子目录区分。

所有 PPTX（含海报、一页通）使用安全的选中主体要素页建议；确需显式指定时在交付计划填写源页码和具体依据。页码升序、去重且为正整数；未选产品页、混页或身份待核要素页仍会受阻，不能以手动页码或更换 `kind` 绕过。受阻时 `deliveries` 记录原因，不回退交付完整稿。

`deliver-ppt` 保留为单独检查或重新导出的工具，新任务的正常交付由 `apply` 自动完成。独立调用时先读 `--help`；它支持 `--pages`、`--review-reason`，但不改变上述主体边界，也不替代本次交付清单及最终文件验收。规则见 [验收与交付](qa-and-delivery.md)。

## 旧 DOC 的转换登记

CLI 不内置 Office 转换。先用可用工具将原 DOC 转为 DOCX，核对转换完整性，再登记缓存：

```powershell
python -X utf8 $materialCli register-conversion `
  --source '输入/原合同.doc' `
  --converted '输入/已转换合同.docx' `
  --cache-dir '本次审阅/conversion-cache'

python -X utf8 $materialCli analyze `
  --contract '输入/原合同.doc' `
  --material '输入/一页通.docx' `
  --project-dir '本次审阅/conversion-cache' `
  --out '本次审阅/analysis'
```

登记保存原件和转换版的双哈希；分析旧 DOC 时用 `--project-dir` 指向登记命令报告的缓存项目目录。不要将转换命令成功等同于内容无损；保留原件供核对。也可以直接把已核验的 DOCX/PDF 作为本次合同输入。

扫描 PDF、图片内条款、嵌入附件的缺失解析须保留待核；不因命令完成而宣称全文已核实。

## 结果入口

| 产物 | 用途 |
| --- | --- |
| `analysis/task.json` | 完整事实候选、合同目标、源模板主体、选中区域、字段槽位、检查项和版本依据 |
| `analysis/prepared_templates/<material_id>/原模板名.docx` | 仅含历史修订的 Word 物料生成此基线：全部旧修订已接受，原批注保留，绑定原件/准备副本双哈希 |
| `analysis/review.template.json` | 本任务的审阅记录模板，由 AI 补充原子需求、合同回查及处置为 `review.json` |
| `analysis/审阅说明.md` 与各 CSV | 可读底稿、逐物料需求和缺项检查 |
| `updated_01/update_result.json` | 内部输出、`deliveries` 最终交付记录、哈希、状态、修改验证和未决检查 |
| `updated_01/review.applied.json` | 实际执行的审阅记录 |
| `updated_01/internal/` | 完整审阅稿，供修改验证和追溯 |
| `updated_01/delivery/` | 海报/一页通保持模板文件名，PPT 为“模板原名产品要素.pptx”；重名时增加物料 ID 子目录 |
| `updated_01/delivery_manifest.json` | `task_id`、`delivery_required` 和各物料 `deliveries`；受阻项没有输出路径 |
| 交付 PPTX 同目录的 `.delivery.json` | 选页映射、来源/输出哈希、结构校验及包含/排除的修改决定 |
| `updated_01/accepted_result.json` | 实际验收结果；最终 `deliveries` 含原生/视觉状态，初始交付清单仍保留生成时状态 |

`apply` 不覆盖原业务文件，同名目的文件已存在时应使用新的输出目录。模板模式须同时完成 `review.requirements` 原子需求核对和 `review.slot_dispositions` 槽位处置；只改名称、只补 `supplements` 或只接收自动需求种子不代表内容已核完。未处置或待补内容会保留待核，规则见 [审阅文件与依据](review.md)。修改来源、模式、模板选择、配置或分析代码后重新 `analyze` 并复核决定，不能把旧任务/审阅文件中的哈希改成新值。

## 字典与需求变更

公共字段字典位于 `runtime/config/field_dictionary.json` 和 `runtime/config/contract_field_extensions.json`。字典不是白名单：模板新增字段先在 `review.new_fields` 中定义，由 AI 回查合同并登记 `contract_rechecks`，即可在本任务核对；不必先修改公共配置。经复核具有复用价值的字段或别名再维护到公共字典，并保留稳定 `field_id`。每轮重新识别实际需求，历史频次不是所有产品或渠道的必填规则。公共配置变更后重新分析和审阅，配置哈希会使旧任务失效。当前没有自动学习、全库需求矩阵合并或自动更新命令。

## 迁移边界

可迁移的是完整 Skill 和本次用户提供的输入资料。历史任务快照保存原件、转换或准备副本的实际路径，单纯复制旧 `task.json` 不代表路径和版本已迁移。应在新环境对同一批源文件重新分析；含历史修订的 Word 物料仍需可用的 Windows Microsoft Word 完成准备，再由 AI 按新任务 ID、槽位和事实 ID 复核旧审阅决定。

当前 CLI 不自动迁移旧任务路径。输入搬移后重新分析，不手改快照或借用原电脑缓存。Skill 包不应包含用户合同、物料、历史任务、凭证、原电脑路径或私人数据。
