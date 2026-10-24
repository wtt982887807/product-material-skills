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

Word/PowerPoint 不参与核心 OOXML 分析和局部修改；原生打开与视觉验收需要目标环境具备相应工具。`doctor` 通过不等于已完成 Office 版面验收。

## 常用命令

每个子命令先用 `--help` 核对当前版本参数。以下使用安装示例中的 `$materialCli`；文件名是调用示例，执行时替换为用户实际文件。

```powershell
python -X utf8 $materialCli analyze `
  --contract '输入/合同.docx' `
  --material '输入/一页通.docx' `
  --material '输入/路演PPT.pptx' `
  --out '本次审阅/analysis'

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

python -X utf8 $materialCli deliver-ppt `
  --task '本次审阅/analysis/task.json' `
  --result '本次审阅/updated_01/update_result.json' `
  --material-id '本任务的实际PPT物料ID' `
  --out '本次审阅/delivery/产品要素页.pptx'
```

`analyze` 必需一份 `--contract` 和至少一份 `--material`；每份物料重复一次 `--material`。可传 `--as-of YYYY-MM-DD`，省略时使用北京时间当天，日期参数本身不提供最新事实。`--product` 只能确认合同已识别的名称或别名，不能修复未知或冲突身份。

`deliver-ppt` 默认使用无歧义的目标要素页建议。需要显式指定经审核的原始页码时，追加 `--pages '22,23' --review-reason '本次逐页核对后的具体依据'`；示例页码不代表任何产品的固定页码。没有明确目标区域、含其他产品区域或含身份待核要素的页面都会被拒绝，手动页码与理由不能绕过；要保留混页中的目标内容，需要另做区域隔离，当前 CLI 不执行对象级隔离。详见 [验收与 PPT 交付](qa-and-delivery.md)。

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
| `analysis/task.json` | 完整事实候选、来源、产品区域、字段槽位、检查项和版本依据 |
| `analysis/review.template.json` | 本任务的审阅记录模板，由 AI 维护为 `review.json` |
| `analysis/审阅说明.md` 与各 CSV | 可读底稿、逐物料需求和缺项检查 |
| `updated_01/update_result.json` | 每份输出路径、哈希、状态、修改验证和未决检查 |
| `updated_01/review.applied.json` | 实际执行的审阅记录 |
| `accepted_result.json` | 绑定实际文件哈希的验收结果 |
| `delivery/产品要素页.pptx` | 仅含确认目标产品要素页的 PPT 副本 |
| `delivery/产品要素页.delivery.json` | 选页映射、来源与输出哈希、结构校验、包含/排除的修改决定和待验收状态 |

`apply` 不覆盖原业务文件，同名目的文件已存在时应使用新的输出目录。修改来源或分析代码后重新 `analyze` 并复核决定，不能把旧任务/审阅文件中的哈希改成新值。

## 字典与需求变更

实际执行的字段字典位于 `runtime/config/field_dictionary.json` 和 `runtime/config/contract_field_extensions.json`。出现有依据的新字段或别名时，由 AI 审阅后维护，并保留已有字段的稳定 `field_id`。每轮 `analyze` 重新识别本次逐物料需求；历史样例的出现频次不自动成为所有产品或渠道的必填规则。配置变更后必须重新分析和审阅，配置哈希会使旧任务对应的审阅失效。当前没有自动学习或全库需求矩阵合并命令。

## 迁移边界

可迁移的是完整 Skill 和本次用户提供的输入资料。历史任务快照保存实际来源路径，单纯把旧 `task.json` 复制到另一台电脑不代表路径和版本已迁移。最稳妥的迁移流程是在新环境对同一批源文件重新分析，再由 AI 按新任务 ID、槽位和事实 ID 复核旧审阅决定。

当前 CLI 不自动迁移旧任务路径。输入搬移后重新分析，不手改快照或借用原电脑缓存。Skill 包不应包含用户合同、物料、历史任务、凭证、原电脑路径或私人数据。
