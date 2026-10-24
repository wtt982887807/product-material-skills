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


def write_reports(task,out):
    out=Path(out)
    csv_table(out/'合同完整要素底稿.csv', ['事实ID','字段ID','要素名称','状态','提取层级','结构化值','完整原文','来源位置'],
              [(f['fact_id'],f['field_id'],f.get('name'),f.get('status'),f.get('extraction_level'),f.get('value'),f.get('raw_text'),f.get('source_locations')) for f in task['contract']['facts']])
    coverage=task['contract'].get('coverage',{})
    rows=list(coverage.items()) if isinstance(coverage,dict) else [(i,c) for i,c in enumerate(coverage)]
    csv_table(out/'合同覆盖检查.csv',['覆盖项目','状态/数量/完整详情'],rows)
    csv_table(out/'物料产品区域.csv',['物料ID','文件','区域ID','产品名称','状态','判断依据','原文位置'],
              [(m['material_id'],m['source']['path'],r['region_id'],r.get('product_names'),r['status'],r.get('reason'),r.get('locations')) for m in task['materials'] for r in m['regions']])
    csv_table(out/'逐物料需求.csv',['物料ID','槽位ID','区域ID','产品状态','字段ID','原标签','原文','可编辑','部件','XPath'],
              [(m['material_id'],s['slot_id'],s['region_id'],s.get('product_status'),s.get('field_id'),s.get('label'),s['text'],s.get('editable'),s.get('part'),s.get('xpath')) for m in task['materials'] for s in m['slots']])
    csv_table(out/'个例检查与缺项.csv',['检查ID','物料ID','槽位ID','字段ID','类别','等级','状态','问题','原文'],
              [(c['check_id'],c.get('material_id'),c.get('slot_id'),c.get('field_id'),c['type'],c['severity'],c['status'],c['message'],c.get('source_text')) for c in task['checks']])
    name=task['target_product']['name']
    counts=task['summary']
    lines=[f'# {name} 物料审阅任务','',f"任务：`{task['task_id']}`；核对截至：{task['as_of']}。",'',
           f"合同事实/候选片段 {counts['fact_count']} 条（含同条款对应多个字段，不是独立要素数量）；物料 {counts['material_count']} 份。目标区域 {counts['target_regions']} 个，非目标区域 {counts['other_regions']} 个，身份待核区域 {counts['ambiguous_regions']} 个。",'',
           '## 阅读顺序','',
           '1. 合同完整要素底稿：查看完整原文、条件及出处。候选不等于已核验规范化值。',
           '2. 合同覆盖检查：查看章节、表格、附件和引用的处理状态；原文覆盖不等于语义提取全部完成。',
           '3. 物料产品区域：先核对要更新哪个产品；非目标及身份待核区域禁止回写。',
           '4. 逐物料需求：保留各文件原标签、内容、顺序与精确位置。',
           '5. 个例检查与缺项：缺少日期、参数、主体或其他依据均按具体检查项补充。','',
           '## 审阅与更新','',
           '`task.json`保存无损结构化任务；复制`review.template.json`为本次审阅文件，填写supplements、decisions及必要的resolutions。',
           '更新以slot_id和来源哈希定位，不执行全文件字符串替换。文本替换须有合同事实或用户补充依据；批注可引用物料自身证据。',
           'Word生成真实修订和批注；PPT局部保留原对象及样式。未决问题与格式限制保留，不能把源文件副本标成全部更新完成。','',
           '## 检查概览','']
    for c in task['checks'][:60]:
        lines.append(f"- {c['check_id']} / {c['type']}：{c['message']}")
    if len(task['checks'])>60: lines.append('- 其余检查见完整CSV及task.json。')
    (out/'审阅说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
