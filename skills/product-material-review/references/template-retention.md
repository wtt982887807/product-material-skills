# 合同未提时沿用模板

本 Skill 的用户规则只覆盖现有明确业绩报酬字段及子字段，以及 `distributor_risk_rating`（代销机构销售评级）。它不覆盖管理人评级、未确定评级主体、收益率、产品代码、日期或任意新字段。未知标签先完成语义识别和正常字段映射，不能仅靠自定义字段名扩展例外。

## 处理顺序

1. 阅读合同对应条款、附件及上下文，区分“没有具体参数”“明确不收取”“已有具体值”“相互冲突”。后面三种走正常合同核对；未提到不等于零费用或不适用。
2. 使用 `search-contract --retention-field <字段ID> --compact` 获取强制同义词检索和 `required_review_block_ids`。同一合同和字段的检索可一次读取后复用；每个实际保留片段仍须绑定对应槽位。阅读命中及上下文并记录已审块，不能通过无关查询制造“无命中”。原文不可读时先补读，不能声明未提。CLI 重放来源及查询，但不替代 AI 的语义结论。
3. 保留该子项原文，在同一槽位增加真实 `kind: comment` 决定，批注正文固定为 `合同未提，沿用模板`。Word 为锚定批注，PPT 为该页原生批注。不在正文额外挤入说明，不用 sidecar 冒充批注。
4. 使用独立状态及来源；不要把模板值复制到 `supplements` 并声称用户提供了本次有效参数。

## review.json 记录

在正常原子需求字段之外，保留需求增加：

```json
{
  "requirement_id": "req-fee",
  "material_id": "本任务物料ID",
  "slot_id": "本槽ID",
  "field_id": "performance_fee",
  "status": "template_retained",
  "retained_text": "精确模板原文片段",
  "rendered_text": "精确模板原文片段",
  "evidence": [{"type": "template", "id": "req-fee"}],
  "comment_decision_id": "comment-fee",
  "contract_search": {
    "source_sha256": "本任务合同哈希",
    "queries": ["业绩报酬", "浮动管理费", "超额收益报酬"],
    "reviewed_block_ids": ["已实际阅读的命中和上下文块ID"],
    "contract_value_status": "not_specified",
    "conclusion": "说明已查条款及为什么仍未给出该具体参数"
  }
}
```

仍需填写通常的 `description`、`template_quote`、`display_rule`、`reason`、`reviewed: true` 等字段。`retained_text` 和 `rendered_text` 必须完全相同且精确来自本槽。对应批注决定的 `before` 必须包含该片段，`after` 为固定批注文字，`evidence` 包含同一 `template` 引用。

纯保留槽位用 `action: retain_template_exception`，`decision_ids` 仍只列正文替换决定，因此此时为空；`requirement_ids` 列全该槽需求。混合槽位使用 `rewrite`，只对有正常依据的部分局部替换，保留子项不变。模板依据只能授权保留和加注，不能授权写入新值。

对应缺项检查可用同一需求及模板引用处置；公式冲突、合同不可读、产品范围等检查不能用此例外结案。输出的 `template_retentions`、`沿用模板例外.csv` 与 `template_retained` 计数独立于合同核实数。全部处理完成且含此类例外时，交付状态为 `content_reviewed_with_template_exceptions`；有其他未决项仍为 `pending_draft`。
