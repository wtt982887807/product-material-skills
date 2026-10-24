# 审阅文件与依据

本文件供 AI 生成审阅记录时使用。用户只需提供资料、回答实质业务问题；不要把填写 JSON 作为业务用户的前置任务。

## 读取顺序

1. `task.target_product` 与 `contract.source`：核对目标、主合同版本和来源位置。
2. `contract.facts`、原文块和 `contract.coverage`：读取候选的完整上下文、条件、例外、份额、单位及引用条款。`candidate` 不代表已核实；未检出不等于“无”或“0”。
3. `materials[].regions` 与 `slots`：确认逐物料的身份、字段需求和可编辑范围。文件封面或宽泛简称不能授权全文件替换。
4. `checks`：逐条判断缺项、占位符、动态参数、公式或身份问题。自动检查不是完整事实核验清单，仍须阅读实际合同及物料。

`task.json` 保存完整信息。CSV 是便于查看的导出；为避免 Excel 公式执行而增加的文本保护前缀不能复制进回写文字。

## 维护 review.json

从本任务的 `review.template.json` 建立审阅文件，原样保留 `schema_version`、`task_id` 和全部 `source_hashes`。后续继续编辑同一个审阅文件，不覆盖已完成的决定。AI 负责以下三组记录：

| 数组 | 用途 | 必需信息 |
| --- | --- | --- |
| `supplements` | 保存合同外的本次有效补充依据 | 唯一 `supplement_id`；与目标完全一致的 `product_name`；`field_id`；非空 `value`；可追溯 `source_note` |
| `decisions` | 指定局部替换或 Word 批注 | 唯一 `decision_id`；`material_id`；`slot_id`；`kind`；精确原文 `before`；新文/批注 `after`；`reason`；`reviewed: true`；`evidence` |
| `resolutions` | 结案真正解决的具体检查 | `check_id`；说明如何解决的 `reason`；`reviewed: true`；`evidence` |

`reviewed: true` 表示该条已完成实质审阅，不表示用户逐条点击批准。已有任务授权足以执行其中有依据的操作；未决业务事实继续保持待核。

### 补充依据

`supplements` 与合同事实分开保存，运行时标记为 `user_supplied`。来源说明应包含资料名称、日期或版本、具体位置、适用渠道/份额及用户提供或确认的信息。仅写“已确认”不能解释事实依据；CLI 不能证明所填内容真实、完整或有效。

对缺项向用户展示：

| 物料及位置 | 缺项或冲突 | 已有依据 | 需要补充的信息 |
| --- | --- | --- | --- |
| 当前物料的实际页码/标签 | 例如本次渠道销售费率 | 合同一般规则与旧物料值分别说明 | 本次适用的费率、条件和带日期的依据 |

用户没有补齐某项时，保留原文和未决检查；继续处理其他有依据的字段。不要把缺失值规范化成“无费用”、把一般开放周期算出的日期当成公告日期，或将旧渠道文字当成新渠道依据。

### 局部决定

`kind` 为 `replace_text` 或 `comment`。`before` 必须在本次选定槽位内精确且唯一出现。替换的 `after` 必须是字符串且确有变化；批注的 `after` 是批注正文。定位使用任务绑定的 `part/xpath`，不在审阅文件中自造位置。

`evidence` 中每项使用 `{ "type": "fact|supplement|material", "id": "本任务实际ID" }` 的结构；`type` 要填其中一个值：

- `fact`：本合同的候选事实 ID；使用前由 AI 核对完整原文与条件。
- `supplement`：本审阅文件的补充依据 ID。
- `material`：本任务物料 ID，可解释保留原文或待核批注；不能单独证明替换后的事实。

每条替换至少引用一个匹配目标字段的 `fact` 或 `supplement`。槽位 `field_id` 为空时，还须填写 `target_field_id` 和具体的 `field_mapping_reason`。不要只因文本相似就合并不同费率、首次/追加金额、认购/参与、自然日/工作日或不同份额。

`other`、`ambiguous` 区域不会因为 `reviewed: true` 变为可编辑。身份补证或规则修正后重新分析。一个段落有多次替换时，确保范围不重叠；不要跨段拼出一个 `before`。

### 检查结案

替换成功不会自动结案。只有证据实际解决对应 `check_id` 时才写入 `resolutions`，说明证据与问题的关系。新增待核批注一般不解决实质缺项，不应据此结案。

检查有 `required_evidence_field` 时按该字段提供依据；`needs_external_evidence` 为真时需要对应字段的本次有效 `supplement`。例如当前开放日期需要 `current_open_date` 的有效资料，合同中的一般开放周期不足以结案。不要为得到完成状态而批量结案。

## 编辑限制

Word 使用真实删除/插入修订和锚定批注，不自动接受已有修订。PPT 当前支持局部文字替换，不支持批注。跨段、复杂域、书签/批注边界、已有修订内部、超链接或含换行/制表符等结构可能受阻；查看 `verification.issues`，不要在程序失败后直接重建整篇/整页绕过局部范围。

需要更复杂编辑时保留已授权的内容与区域边界，采用合适的 Office 编辑方法并重新验证；无法安全处理的部分明确交付为待处理项。
