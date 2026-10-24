# 缺项原值保留与局部标黄

新模板任务的合同未提参数采用 `template_pending`：保留模板原句和原值，只标黄需确认的参数，附一条简短真实批注。日期、代码、业绩报酬参数、代销评级及其他已定义的应适用参数均按此流程核对；产品名称仍必须按合同更新，不能将旧产品身份保留待确认。

## 回查与绑定

1. 确认参数适用，并读合同完整相关条款、附件和上下文。已定义字段用 `search-contract --pending-field <字段ID> --compact` 获得完整字段别名回查及 `required_review_block_ids`；同一合同的同类查询可复用。新字段先登记 `new_fields`，用常规查询或 `--batch` 搜索其名称、别名及相关业务词，验证时补齐强制命中及上下文。不能通过无关检索制造“无命中”。
2. 合同未给出具体参数时，`contract_search` 记录 `source_sha256`、`queries`、`reviewed_block_ids`、`contract_value_status: "not_specified"` 和具体 `conclusion`。检索只能定位，不能证明语义上的不存在；原文不可读、冲突或尚未查清不能冒充合同未提。已有适用合同补提或用户补充时先核对该依据。
3. `retained_text` 是原槽内精确片段，`rendered_text` 与它完全一致；`evidence` 为本需求的 `[{"type":"template","id":"本需求ID"}]`，不放进 `supplements`。保留的是原值，不是目标产品已核实事实。
4. 同槽增加真实 `comment`，例如“合同未提，保留模板值待确认”，并用 `comment_decision_id` 关联。增加 `kind: "highlight"`、`color: "yellow"` 的决定，`before` 仅覆盖关键参数，用 `highlight_decision_ids` 关联。批注和标黄引用同一需求的 `template` 依据，绑定同一个保留片段。Word 使用锚定批注，PPT 使用原生页面批注，不用旁置说明文件代替。
5. 同一数值多次出现时，每个需要提示的实际出现位置都绑定精确跨度；使用零基 `start` 消除歧义，不扩大到整段。多个原值可以各建原子需求；较长保留片段内的多个关键参数可分别标黄，但句式、解释、单位外的无关文字不整段染黄。保留片段不得与正文替换重叠。

合同明确“不提取业绩报酬”属于明确约定，不能沿用旧收费公式；已有日期与明确合同安排冲突时先解决冲突，不能只标黄绕过。合同只给一般规则但未给具体日期/门槛时，已知规则按合同核对，缺失参数保留待确认；不默认整槽 `【待补充】`。

## 同一字段中部分已知、部分缺项

用可选 `parameter_key` 区分同字段内不同语义参数。例如 `performance_fee` 的提取比例用 `participation_rate`，门槛用 `annual_threshold`。关联的 `requirements`、`contract_rechecks` 和真实用户 `supplements` 使用相同键，格式为小写字母开头的蛇形标识，最多64字符。合同明确提取比例而未给具体门槛时，两类需求分别核对，不能整段标黄。

任一记录未细分键时按整个字段保守检查；同参数已有依据不能仍称缺项。整个业绩报酬明确不收取时，使用无参数键的 `not_applicable`，阻止沿用任何旧收费公式。参数键只标识语义，不能通过换键规避合同已给出的同一参数。

## 日期待确认与管理费已核实的混合例

模板原句为“下次开放日为2027年11月3日；管理费0.50%/年。”。假定合同明确管理费为0.30%/年，只给一般开放规则而未提供该日期。输出应为“下次开放日为2027年11月3日；管理费0.30%/年。”：只替换费率，只将日期标黄，并对日期加简短批注。

下面是审阅文件片段；保留完整 review 的任务头及其他记录。示例 ID、来源哈希及已读块须换成本任务真实内容，不能直接当作业务依据。`start: 6` 针对这句原始槽文，不是替换后偏移。

```json
{
  "requirements": [
    {
      "requirement_id": "r-date", "material_id": "m-1", "slot_id": "s-1",
      "field_id": "current_open_date", "description": "本次具体开放日",
      "template_quote": "下次开放日为2027年11月3日",
      "display_rule": "保留原句，仅日期标黄待确认",
      "status": "template_pending", "retained_text": "2027年11月3日",
      "rendered_text": "2027年11月3日",
      "evidence": [{"type": "template", "id": "r-date"}],
      "contract_search": {
        "source_sha256": "本任务合同SHA256",
        "queries": ["本次实际开放日", "本次开放日", "预期开放日", "开放日期", "开放期"],
        "reviewed_block_ids": ["b-opening-prev", "b-opening", "b-opening-next"],
        "contract_value_status": "not_specified",
        "conclusion": "已读相关一般规则、公告约定及相邻条款，未给出本次具体开放日期"
      },
      "comment_decision_id": "d-date-comment",
      "highlight_decision_ids": ["d-date-yellow"],
      "reason": "原日期不作为合同已核实事实，保留待确认", "reviewed": true
    },
    {
      "requirement_id": "r-fee", "material_id": "m-1", "slot_id": "s-1",
      "field_id": "management_fee", "description": "年管理费率",
      "template_quote": "管理费0.50%/年", "display_rule": "保持原句及年费率单位，仅更新费率",
      "status": "verified", "rendered_text": "0.30%/年",
      "evidence": [{"type": "fact", "id": "f-management-fee"}],
      "reason": "合同该主体及份额的年费率为0.30%", "reviewed": true
    }
  ],
  "decisions": [
    {
      "decision_id": "d-fee", "material_id": "m-1", "slot_id": "s-1",
      "kind": "replace_text", "before": "0.50%", "after": "0.30%",
      "requirement_ids": ["r-fee"], "evidence": [{"type": "fact", "id": "f-management-fee"}],
      "reason": "仅替换已核实的费率差异", "reviewed": true
    },
    {
      "decision_id": "d-date-yellow", "material_id": "m-1", "slot_id": "s-1",
      "kind": "highlight", "color": "yellow", "before": "2027年11月3日", "start": 6,
      "evidence": [{"type": "template", "id": "r-date"}],
      "reason": "仅提示待确认日期", "reviewed": true
    },
    {
      "decision_id": "d-date-comment", "material_id": "m-1", "slot_id": "s-1",
      "kind": "comment", "before": "2027年11月3日", "start": 6,
      "after": "合同未提，保留模板值待确认",
      "evidence": [{"type": "template", "id": "r-date"}],
      "reason": "说明日期来源和待确认状态", "reviewed": true
    }
  ],
  "slot_dispositions": [
    {
      "material_id": "m-1", "slot_id": "s-1", "action": "rewrite",
      "final_text": "下次开放日为2027年11月3日；管理费0.30%/年。",
      "decision_ids": ["d-fee"], "requirement_ids": ["r-date", "r-fee"],
      "discovery_reviewed": true, "discovery_reason": "已拆分具体日期和年管理费率，原句无其他事实参数",
      "evidence": [{"type": "fact", "id": "f-management-fee"}, {"type": "template", "id": "r-date"}],
      "reason": "局部更新已知费率，日期保留并标黄待确认", "reviewed": true
    }
  ]
}
```

无正文变化、只有待确认标记时用 `retain_marked`，`decision_ids` 为空；批注和标黄始终通过需求的两个注释字段关联。混合修改用 `rewrite`，不将整槽记为已核实。

## 状态与兼容

`template_pending` 不属于 `verified` 或 complete；它使内容保持 `pending_draft`。文件成功生成可为 `ready`，允许交付并单列待确认清单，无需先向用户补问全部参数。真实标黄/批注已完成不代表事实缺项已解决。

旧 `template_retained` 及业绩报酬/代销评级白名单仅兼容旧格式；新 `template_minimal` 任务使用本页流程，不能用旧状态把待确认参数计成完成。收到有效补充后按新依据核对并更新具体跨度，不通过改状态或哈希跳过回写及验收。
