"""Human-readable review tables alongside lossless task JSON."""
import csv
import json
from pathlib import Path


def _value(v):
    return json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else '' if v is None else str(v)


def csv_table(path, headers, rows):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            values=[_value(v) for v in row]
            # Viewing in Excel must not turn untrusted source text into formulas.
            writer.writerow(["'"+v if v.startswith(('=','+','-','@')) else v for v in values])


def write_review_reports(task,review,checked,out):
    """Separate extraction candidates from reviewed demands and new source facts."""
    if not task.get('requirement_schema_version'):return
    out=Path(out)
    csv_table(out/'需求核对结果.csv',['需求ID','物料ID','槽位ID','字段','需求说明','显示口径','状态','已审阅','依据','最终显示片段','核对理由'],
        [(r['requirement_id'],r['material_id'],r['slot_id'],r['field_id'],r['description'],r['display_rule'],r['status'],
          r.get('reviewed'),r.get('evidence'),r['rendered_text'],r['reason']) for r in review.get('requirements',[])])
    csv_table(out/'合同回查补提底稿.csv',['回查ID','字段','值','原文引用','来源位置','来源类型','来源哈希','核对理由'],
        [(fid,f['field_id'],f['value'],f['raw_text'],f['source_locations'],f['source_kind'],f['source_sha256'],f['reason'])
         for fid,f in checked.get('contract_rechecks',{}).items()])
    coverage=checked.get('requirement_coverage',{})
    stats={'original_contract_candidates':len(task['contract']['facts']),
           'new_contract_recheck_facts':len(checked.get('contract_rechecks',{})),
           'requirement_review':coverage,'slot_review':checked['template_coverage'],
           'meaning':'原始候选、新增合同证据及已核对需求分别统计；证据绑定与显式核对记录不是自动语义正确性证明。'}
    (out/'需求核对统计.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')


def write_reports(task,out):
    out=Path(out)
    template_mode=task.get('material_mode')=='template'
    csv_table(out/'合同完整要素底稿.csv', ['事实ID','字段ID','要素名称','状态','提取层级','结构化值','完整原文','来源位置'],
              [(f['fact_id'],f['field_id'],f.get('name'),f.get('status'),f.get('extraction_level'),f.get('value'),f.get('raw_text'),f.get('source_locations')) for f in task['contract']['facts']])
    coverage=task['contract'].get('coverage',{})
    rows=list(coverage.items()) if isinstance(coverage,dict) else [(i,c) for i,c in enumerate(coverage)]
    csv_table(out/'合同覆盖检查.csv',['覆盖项目','状态/数量/完整详情'],rows)
    csv_table(out/'物料产品区域.csv',['物料ID','文件','区域ID','产品名称','状态','判断依据','原文位置'],
              [(m['material_id'],m['source']['path'],r['region_id'],r.get('product_names'),r['status'],r.get('reason'),r.get('locations')) for m in task['materials'] for r in m['regions']])
    csv_table(out/'逐物料需求.csv',['物料ID','槽位ID','区域ID','产品状态','字段ID','原标签','原文','可编辑','部件','XPath','交付范围需处置'],
              [(m['material_id'],s['slot_id'],s['region_id'],s.get('product_status'),s.get('field_id'),s.get('label'),s['text'],s.get('editable'),s.get('part'),s.get('xpath'),s.get('delivery_required')) for m in task['materials'] for s in m['slots']])
    if template_mode:
        csv_table(out/'模板范围与目标产品.csv',['物料ID','模板文件','模板主体','合同目标产品','选择状态','选择依据','选定区域','要素页建议'],
                  [(m['material_id'],m['source']['path'],m.get('template_source_product'),task['target_product']['name'],
                    m.get('template_selection',{}).get('status'),m.get('template_selection',{}).get('reason'),
                    m.get('template_selection',{}).get('selected_region_ids'),m.get('template_selection',{}).get('element_pages'))
                   for m in task['materials']])
        csv_table(out/'模板原子需求候选.csv',['需求种子ID','物料ID','槽位ID','候选字段','组成部分','需求说明','模板引文','显示口径候选','状态'],
                  [(c['candidate_id'],c['material_id'],c['slot_id'],c.get('field_id'),c.get('component'),c['description'],
                    c['template_quote'],c['display_rule'],c['status']) for c in task.get('requirement_candidates',[])])
    csv_table(out/'个例检查与缺项.csv',['检查ID','物料ID','槽位ID','字段ID','类别','等级','状态','问题','原文'],
              [(c['check_id'],c.get('material_id'),c.get('slot_id'),c.get('field_id'),c['type'],c['severity'],c['status'],c['message'],c.get('source_text')) for c in task['checks']])
    name=task['target_product']['name']
    counts=task['summary']
    lines=[f'# {name} 物料审阅任务','',f"任务：`{task['task_id']}`；核对截至：{task['as_of']}。",'',
           f"合同事实/候选片段 {counts['fact_count']} 条（含同条款对应多个字段，不是独立要素数量）；物料 {counts['material_count']} 份。目标区域 {counts['target_regions']} 个，非目标区域 {counts['other_regions']} 个，身份待核区域 {counts['ambiguous_regions']} 个。",'',
           '## 阅读顺序','',
           '1. 合同完整要素底稿：查看完整原文、条件及出处。候选不等于已核验规范化值。',
           '2. 合同覆盖检查：查看章节、表格、附件和引用的处理状态；原文覆盖不等于语义提取全部完成。',
           ('3. 模板范围与目标产品：合同确定成品产品；其余文件只提供格式。先选定模板自身要素区域，模板原产品不同不构成错误。'
            if template_mode else '3. 物料产品区域：先核对要更新哪个产品；非目标及身份待核区域禁止回写。'),
           '4. 逐物料需求：保留各文件原标签、内容、顺序与精确位置。',
           '5. 个例检查与缺项：缺少日期、参数、主体或其他依据均按具体检查项补充。','',
           '## 审阅与更新','',
           '`task.json`保存无损结构化任务；复制`review.template.json`为本次审阅文件，填写supplements、decisions及必要的resolutions。'+
           ('模板模式还须逐一填写交付范围内slot_dispositions，未处置槽位不能完成。' if template_mode else ''),
           '更新以slot_id和来源哈希定位，不执行全文件字符串替换。文本替换须有合同事实或用户补充依据；批注可引用物料自身证据。',
           'Word生成真实修订和批注；PPT局部保留原对象及样式。未决问题与格式限制保留，不能把源文件副本标成全部更新完成。','',
           '## 检查概览','']
    for c in task['checks'][:60]:
        lines.append(f"- {c['check_id']} / {c['type']}：{c['message']}")
    if len(task['checks'])>60: lines.append('- 其余检查见完整CSV及task.json。')
    if template_mode:
        lines += ['','## 模板内容覆盖','',
                  '模板旧名称、产品码、费率、日期、管理人和历史业绩均不构成新产品事实。识别出的同名字段也必须逐槽审阅。',
                  'slot_dispositions使用rewrite、retain_verified、pending_placeholder、not_applicable或retain_static；final_text须为所有决定执行后的完整槽位正文。',
                  '缺项用kind=placeholder、整槽before与after=【待补充】清除旧值，并保留待补状态；不能仅补一条来源或加批注后把旧正文结案。',
                  '空白值槽和无法安全写入的对象仍须人工处理；源副本和未覆盖正文仅为内部审阅稿，不能标记完整成品。']
        if task.get('requirement_schema_version'):
            lines += ['','## 需求回查与核对','',
                f"合同初始候选 {len(task['contract']['facts'])} 条；规则发现需求种子 {len(task.get('requirement_candidates',[]))} 条。此时已核实需求为0，不能将原文覆盖或种子数量当作语义完成率。",
                'AI须逐槽读取完整模板并补充、拆分原子需求；已有字段字典不是需求白名单。种子仅提醒可能的费用、支付、日期和公告信息，不自动证明已发现所有含义。',
                '先判断目标合同是否适用，再匹配已有底稿、回查完整合同及关联条款。新的合同摘录记录到contract_rechecks，并绑定原文块与精确引文；不得伪装成user_supplied。',
                'requirements逐项记录verified、not_applicable、needs_external、unresolved、unreadable或conflict；slot_dispositions须列全requirement_ids并确认discovery_reviewed/discovery_reason。',
                '最终原子需求rendered_text必须出现在实际槽位正文。封闭合同可用合同依据将模板开放日期判不适用，无需虚构日期或强制用户补充。']
    (out/'审阅说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
