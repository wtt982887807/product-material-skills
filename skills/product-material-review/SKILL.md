---
name: product-material-review
description: Use when a user needs to review or update Chinese financial product elements in Word one-pagers, brochures, posters, or PowerPoint decks against a supplied contract, especially when materials contain several products, missing parameters, tracked changes, or product-element-only slide delivery.
---

# 产品要素审阅与物料更新

以本次合同确定目标产品，逐份物料建立展示需求，在有依据的产品区域内局部更新。AI 负责阅读原文、判断适用条件、形成审阅决定并调用 CLI；业务用户提供材料和必要补充信息，无须编写 JSON。

## 开始前

需要一份主合同和至少一份目标物料。合同支持 DOCX、可提取文字的 PDF；旧 DOC 需要先取得转换版并用 `register-conversion` 登记匹配来源哈希的缓存。回写支持 DOCX 和 PPTX，图片海报或 PDF 一页通不属于自动回写格式。文件缺失或产品身份冲突时先解决对应输入；不以文件名、旧物料或 `--product` 强行覆盖合同身份。

入口固定为 `python <skill_dir>/scripts/material_agent.py`，其中 `<skill_dir>` 是本 Skill 实际所在目录。首次运行、换电脑或依赖报错时读取 [CLI 与迁移](references/cli.md)，运行 `doctor`。核心随 Skill 携带，不需要原项目目录或模型 API 密钥；AI 的语义审阅由当前 Codex 会话完成。

## 执行

1. **分析。** 调用 `analyze`，读取生成的 `task.json` 和审阅表。核对合同正文、附件、跨条款引用、期号、份额、渠道、日期与单位。合同候选、全文解析覆盖、结构化抽取和事实核验分别判断。
2. **确定局部范围。** 逐物料检查 `material_id → region_id → slot_id`。只更新 `target` 区域；`other` 保留，`ambiguous` 核实后重新分析。同一 PPT 中信托与底层资管产品各有身份：提供信托合同时，只更新和输出信托自身要素，不输出底层产品要素页。用户另有目标但表述与合同冲突或范围不明时，问清该处含义，同时继续不受影响的工作。
3. **审阅并补缺。** 阅读 [审阅文件与依据](references/review.md)，由 AI 从生成模板维护 `review.json`。将待补项列成“物料及位置｜缺项/冲突｜已有依据｜需要补充的信息”表。缺费率、当前开放档期、渠道主体等不能用旧物料补成合同事实；保留未决状态，Word 可添加待核批注。
4. **执行已授权更新。** 用户已授权按合同更新指定物料时，直接执行证据充分的决定；只暂停受缺失依据或歧义影响的字段，无需重复要求逐文件批准。`apply` 写入新的输出目录，Word 使用真实修订和锚定批注，PPT 保留对象并局部改文。逐份检查结果，受阻文件的源副本不算修改成功。
5. **校验与交付。** 运行 `validate`，再按 [验收与 PPT 交付](references/qa-and-delivery.md) 检查原生打开和版面、登记 `accept`。PPT 最终仅交付确认属于目标产品的产品要素页；完整更新稿作为内部审阅中间件。含其他产品要素的混页禁止整页输出，手动页码与理由也不能越过；需该页内容时另做目标区域隔离并单独验证。选页不等于字段核验，选页副本仍需打开与视觉检查。

## 完成口径

交付 Word 审阅稿、PPT 产品要素页副本及简短的修改/待补清单。清楚区分已执行、未修改、受阻、待核和未验收；结构验证通过不能单独宣称全部完成。若环境没有 Office 或实际渲染条件，说明尚未完成的原生/视觉验收，不填写虚假的验收记录。

样例配置和历史出现频次只解释相应样例，不是所有产品、渠道的统一必填规则。保留来源与版本；资料或分析引擎改变时重新分析，不能改任务哈希、身份状态或验收布尔值绕过检查。
