# 模板需求发现与合同回查

先建立完整合同原文索引，再从所选模板正文发现需要核对的事实。字段字典帮助复用词汇，不是白名单；完整结构化要素表不作为更新物料的前置任务。自动候选是起点，不代表模板已经读全。

## 逐项判断

在交付范围内读原句、标签、括号、脚注、表头、空白值及图片文字。一槽可以含多个需求，例如“管理费年费率……，每日计提、按季支付”分开核对费率、计提及支付。句式、单位、标题/摘要方式记录为 `display_rule`；默认原句保持，只改实际差异。

1. 按合同目标、主体、份额、渠道和期间判断适用性。合同名称优先；模板不同产品不需要另问目标。
2. 匹配已有事实及完整上下文。管理人风险、代销评级和投资者 C 等级分别核对；同主体同口径的 R 值及风险描述保持一致。
3. 缺依据时批量回查合同正文、附件、近义词及相关上下文；有新事实登记 `contract_rechecks`，不写成用户补充。
4. 明确不同的事实作最小替换；明确不适用作有依据的局部修改。只有合同未给出具体参数时才保留旧值、局部标黄、加批注并记 `template_pending`。
5. 原文不可读、来源冲突或尚未找到依据分别保留状态。零命中不是不存在的证明。用户另给有效依据后再核对更新，不要求先补齐才出标注稿。

程序确认的静态标题/标签可使用自动保留记录；检查例外即可。产品名、数字、日期及条款不能归入静态保留。

## 字段与补提

`review.new_fields`：唯一 `field_id`、非空 `name`/`definition`，可选 `aliases` 字符串数组，说明与近似字段的区别。

`review.contract_rechecks`：唯一 `recheck_id`、已定义 `field_id`、有条件和单位的 `value`、`citations` 数组（每项 `block_id` 与准确非空 `quote`）、本合同 `source_sha256`、具体 `reason`、`reviewed: true`。CLI 校验原文件/转换版哈希、块 ID 和引文，AI 判断语义；引用方式为 `{ "type":"recheck", "id":"实际补提ID" }`。

同一任务的新字段可直接补提，无需先修改公共字典。已定义字段可用 `search-contract --pending-field <字段ID> --compact` 回查待确认参数；仅在 `review.new_fields` 新定义的字段先用常规查询或 `--batch` 检索，保存全体相关块，验证时按新增名称及别名补齐要求，见 [缺项保留与标黄](template-retention.md)。

## 原子需求记录

同字段中已知与缺失参数并存时，用可选 `parameter_key` 在需求、合同补提和用户补充中标识同一原子参数（`[a-z][a-z0-9_]{0,63}`）。例如业绩报酬比例与门槛分别核对；省略键表示整个字段。同参数不能既有有效依据又声明缺项；整字段明确不适用会阻止所有下属参数沿用。详见 [缺项保留](template-retention.md#同一字段中部分已知部分缺项)。

| 字段 | 用途 |
| --- | --- |
| `requirement_id`、`material_id`、`slot_id` | 唯一需求及所属槽位 |
| `field_id`、`description` | 要核对的参数/事实及定义 |
| `template_quote` | 本槽精确原文，只说明需求和原始表达 |
| `display_rule` | 保留句式、层次、单位及条件；指出最小改动位置 |
| `candidate_id` | 来自自动候选时记录；拆分可复用同一候选，额外发现的可省略 |
| `field_mapping_reason` | 字段重映射或拆分时说明 |
| `status`、`evidence` | 下表状态及本次依据；待核不冒充已核实 |
| `rendered_text` | 本需求在最终槽文中的实际片段 |
| `retained_text`、`comment_decision_id`、`highlight_decision_ids` | `template_pending` 的原样片段及实际注释绑定，详见标黄参考 |
| `contract_search` | 待确认/外部信息的本合同回查记录 |
| `removed_text` | 不适用子项被移除时的唯一原始片段，关联真实替换 |
| `reason`、`reviewed` | 本次审阅依据和结论 |

| `status` | 含义 |
| --- | --- |
| `verified` | 有依据且最终表达已核实 |
| `not_applicable` | 有 `fact` 或 `recheck` 合同依据，不能仅凭未检出或模板判定 |
| `template_pending` | 合同未给出参数，原值保留、关键参数标黄并加实际批注；不计 `verified` 或 complete |
| `needs_external` | 确需外部信息且已回查；没有可安全保留的原值等情况仍可用此状态，不强制先补齐才能处理其他项 |
| `unresolved` / `unreadable` / `conflict` | 分别表示待定位、原文不可读、来源/条款冲突 |
| `template_retained` | 旧白名单格式兼容；新任务改用 `template_pending` 及实际标黄 |

`needs_external` 的 `contract_search` 至少包含非空 `queries`、本合同 `source_sha256` 和具体 `conclusion`；缺少记录仍按 `unresolved`。`template_pending` 还需 `contract_value_status: "not_specified"` 及已读 `reviewed_block_ids`，覆盖字段名称/别名强制检索命中和相邻原文。检索的 `absence_proven` 始终为 false；AI 阅读后才能判断具体参数是否未提。

## 最终表达绑定

同槽全部需求（含未决项）由 `slot_dispositions.requirement_ids` 精确关联，`discovery_reviewed`/`discovery_reason` 说明实际拆分。`final_text` 为执行局部决定后的完整槽文；不要为了填写它重新措辞。`rendered_text` 必须与实际最终文字一致。

事实替换只引用已核实或有合同依据不适用的需求。待确认项的 `retained_text` 与 `rendered_text` 完全一致，且原跨度不能被替换；批注和标黄绑定相同片段。重复参数用 `start` 逐次定位。移除不适用子项须写 `removed_text` 并绑定真实删除/替换；原本正确的不适用表达可直接保留。

三个判断例：管理费率有合同、具体开放日期未提时，只改费率并标黄旧日期；合同明确“不提取业绩报酬”时须纠正旧收费表达，不能标黄后沿用；风险 R2 改为 R3 时同时核对同主体风险描述，保留无关句式。日期与费率的完整混合记录见 [缺项保留与标黄](template-retention.md#日期待确认与管理费已核实的混合例)。
