# 审阅文件与依据

本文件供 AI 生成审阅记录时使用。用户只需提供资料、回答实质业务问题；不要把填写 JSON 作为业务用户的前置任务。

## 读取顺序

1. `task.target_product` 与 `contract.source`：核对目标、主合同版本和来源位置。
2. `contract.facts`、原文块和 `contract.coverage`：读取候选的完整上下文、条件、例外、份额、单位及引用条款。`candidate` 不代表已核实；未检出不等于“无”或“0”。
3. 任务模式、`materials[].template_source_product`、`destination_product`、`template_selection`、`regions` 与 `slots`：模板模式分别确认源模板主体和合同目标，读取选择依据、展示需求与可编辑范围；二者不同名不构成错误。`target` 是选中的模板区，`other` 保留为未选区，`ambiguous` 需重新核对选择。文件封面或宽泛简称不能授权全文件替换。
4. `requirement_candidates`、`checks`：按 [需求发现与合同回查](requirements.md) 检查每个模板槽位，补充或拆分原子需求，先判适用性，再回查缺失依据。自动检查及种子不是完整需求或事实核验清单。

`task.json` 保存完整信息。CSV 是便于查看的导出；为避免 Excel 公式执行而增加的文本保护前缀不能复制进回写文字。

## 维护 review.json

从本任务的 `review.template.json` 建立审阅文件，原样保留 `schema_version`、`task_id` 和全部 `source_hashes`。后续继续编辑同一个审阅文件，不覆盖已完成的决定。AI 负责以下记录：

| 数组 | 用途 | 必需信息 |
| --- | --- | --- |
| `supplements` | 保存合同外的本次有效补充依据 | 唯一 `supplement_id`；与目标完全一致的 `product_name`；`field_id`；非空 `value`；可追溯 `source_note` |
| `new_fields`、`contract_rechecks` | 定义任务内新字段并登记合同补提 | 精确定义；本合同块与引文、来源哈希、审阅理由，见 [需求参考](requirements.md) |
| `requirements` | 逐项核对模板原子需求 | 槽位、要求、适用状态、依据和最终表达，见 [需求参考](requirements.md) |
| `decisions` | 指定局部替换或 Word 批注 | 唯一 `decision_id`；`material_id`；`slot_id`；`kind`；精确原文 `before`；新文/批注 `after`；`reason`；`reviewed: true`；`evidence`；模板事实替换还需 `requirement_ids` |
| `resolutions` | 结案真正解决的具体检查 | `check_id`；说明如何解决的 `reason`；`reviewed: true`；`evidence` |
| `slot_dispositions` | 模板模式逐个选中内容槽位的最终处置 | `material_id`；`slot_id`；`action`；完整 `final_text`；`decision_ids`；`requirement_ids`；`discovery_reviewed: true`；`discovery_reason`；`evidence`；`reason`；`reviewed: true` |

`reviewed: true` 表示该条已完成实质审阅，不表示用户逐条点击批准。已有任务授权足以执行其中有依据的操作；未决业务事实继续保持待核。

### 补充依据

`supplements` 与合同事实分开保存，运行时标记为 `user_supplied`。只用于回查后仍需的本次外部信息；合同中补提的内容必须登记为 `contract_rechecks`。将需求列为 `needs_external` 前，填写其 `contract_search.queries`、`source_sha256` 和具体 `conclusion`，由 CLI 回放检索；未记录回查仍按 `unresolved` 处理，不能直接变成给用户的缺项。检索记录不证明语义上的不存在，仍须 AI 阅读命中及上下文，详见 [需求参考](requirements.md)。来源说明应包含资料名称、日期或版本、具体位置、适用渠道/份额及用户提供或确认的信息。仅写“已确认”不能解释事实依据；CLI 不能证明所填内容真实、完整或有效。

对缺项向用户展示：

| 物料及位置 | 缺项或冲突 | 已有依据 | 需要补充的信息 |
| --- | --- | --- | --- |
| 当前物料的实际页码/标签 | 例如本次渠道销售费率 | 列明已查合同位置及其一般规则；模板旧值仅说明需求 | 本次适用的费率、条件和带日期的依据 |

模板模式中，用户未补齐某项时，应执行待补占位以清理旧值，并保留未决检查；不能把旧产品的日期、费用或渠道继续留作新产品正文。展示句式需求保留在审阅底稿，待事实补齐后恢复完整表达。只有已有同品物料的 `review` 模式可以把未改原文作为待核状态保留。两种模式均不能把缺失值规范化成“无费用”、把一般开放周期算出的日期当成公告日期，或将旧渠道文字当成新渠道依据。

### 模板槽位的完整处置

为交付范围内每个选中的必需内容槽位填写一项 `slot_dispositions`，包括标题、空白值、未映射字段以及需要保留的静态标签。`requirement_ids` 关联本槽全部原子需求；`discovery_reviewed` 和 `discovery_reason` 说明已实际逐句检查并完整拆分，不能把自动种子当作全部需求。`final_text` 是该槽位执行全部决定后的完整文字，不是单条替换片段；`decision_ids` 精确包含本槽位的全部文字替换决定，不含 `comment`。最终文字必须与输出一致，不能只填写处置记录而省略实际改文。程序无法安全填入的空白或复杂结构保留受阻/未完成，不能因原本为空而跳过审阅。

| `action` | 使用条件及处理 |
| --- | --- |
| `rewrite` | 按合同、合同补提或有效补充重写；引用真实替换决定，并逐需求绑定事实依据及最终表达 |
| `retain_verified` | 最终值与模板恰好相同，每项内容经本合同或有效补充核实后保留；相同数值不是证据 |
| `pending_placeholder` | 暂无目标事实；以 `placeholder` 决定把整槽换成 `【待补充】`，仍不能算完成 |
| `not_applicable` | 整槽均有合同依据证明不适用；最终文字为 `不适用` 或空串，并绑定实际替换；仅子需求不适用时在 `requirements` 记录，并按剩余需求重写整槽 |
| `retain_static` | 仅保留程序识别为标签的文字或受支持的通用标题；仍须显式审阅，不能用于具体产品名称、数值、日期或条款 |

事实处置须有匹配需求的 `fact`、`recheck` 或 `supplement`；不适用必须包含合同事实或合同补提依据。`material` 只说明模板要求或占位来源，不能证明目标内容。未知槽位先识别含义，映射已有字段或在 `new_fields` 定义新字段，再在需要映射的处置记录及对应替换决定中填写 `target_field_id` 和 `field_mapping_reason`。不能靠 `retain_static` 跳过；未检出或不知道不等于不适用。

按“下一开放期首个开放日为某日”的模板表达时，先判断目标产品是否有该类开放安排。适用但合同仅给一般周期时，回查后仍缺本次日期才请用户补充；合同明确不适用时有依据地改写该要求。收到用户日期后，还需真正改正文、绑定对应决定与最终槽文；只新增补充记录或写“已解决”不能完成该槽位。

### 局部决定

`kind` 为 `replace_text`、`comment`，或模板模式清理缺项的 `placeholder`。`before` 必须在本次选定槽位内精确且唯一出现。替换的 `after` 必须是字符串且确有变化；批注的 `after` 是批注正文。`placeholder` 的 `before` 必须为整槽文字，`after` 必须精确为 `【待补充】`，并绑定 `pending_placeholder` 处置；它不能写任意新事实，也不能凭占位结案。定位使用任务绑定的 `part/xpath`，不在审阅文件中自造位置。

`evidence` 中每项使用 `{ "type": "fact|recheck|supplement|material", "id": "本任务实际ID" }` 的结构；`type` 要填其中一个值：

- `fact`：本合同的候选事实 ID；使用前由 AI 核对完整原文与条件。
- `recheck`：本次已验证来源绑定的合同补提 ID；原文的语义适用性由 AI 核对。
- `supplement`：本审阅文件的补充依据 ID。
- `material`：本任务物料 ID，可解释格式要求、待补占位或待核批注；不能单独证明目标事实或原值适用。

每条模板事实替换的 `requirement_ids` 须关联本槽已核实或有依据不适用的需求，引用的依据须来自这些需求。槽位 `field_id` 为空时，还须填写 `target_field_id` 和具体的 `field_mapping_reason`。专门的 `placeholder` 决定允许使用 `material` 依据清理未知值，但仍保留待补状态。不要只因文本相似就合并不同费率、首次/追加金额、认购/参与、自然日/工作日或不同份额。

`other`、`ambiguous` 区域不会因为 `reviewed: true` 变为可编辑。身份补证或规则修正后重新分析。一个段落有多次替换时，确保范围不重叠；不要跨段拼出一个 `before`。

### 检查结案

模板的 `template_content_review` 检查依赖有效槽位处置及其需求核对；其他业务检查仍需逐条审阅。只有证据实际解决对应 `check_id`，并且要求的正文改动或有依据保留实际完成时，才写入 `resolutions`。新增批注或待补占位没有解决实质缺项；只补日期依据而正文仍是旧日期，也不能结案。原子需求、槽位处置和一般检查分别验证，不能用一条结案记录替代全部核对。

检查有 `required_evidence_field` 时按该字段提供依据。当前开放日期等外部事实适用时，需要对应字段的本次有效补充，一般开放周期不足以证明具体日期；若该要求被合同明确排除，应通过有依据的不适用需求处理，不能虚构补充资料。不要为得到完成状态而批量结案。

## 编辑限制

Word 使用真实删除/插入修订和锚定批注，不自动接受已有修订。PPT 当前支持局部文字替换，不支持批注。跨段、复杂域、书签/批注边界、已有修订内部、超链接或含换行/制表符等结构可能受阻；查看 `verification.issues`，不要在程序失败后直接重建整篇/整页绕过局部范围。

需要更复杂编辑时保留已授权的内容与区域边界，采用合适的 Office 编辑方法并重新验证；无法安全处理的部分明确交付为待处理项。
