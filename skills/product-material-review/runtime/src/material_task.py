"""Local, resumable material-update core. No model/API, skill installation or website.

analyze produces evidence, product-scoped slots and a review template. apply consumes
explicit reviewed decisions, never treating an old material as contract truth.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from pathlib import Path
import shutil

CORE_VERSION = '2.1.0'
PROJECT = Path(__file__).resolve().parents[1]


class ReviewError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def source_version_matches(source):
    pairs=[(source['path'],source['sha256'])]
    if source.get('converted_path'):
        pairs.append((source['converted_path'],source.get('converted_sha256')))
    return all(digest and Path(path).is_file() and file_hash(path)==digest for path,digest in pairs)


def engine_hashes():
    modules=['contract_facts.py','material_regions.py','case_checks.py','material_task.py','material_writer.py',
             'corpus.py','qdii_pipeline.py','task_validation.py','task_report.py','ppt_delivery.py','material_cli.py']
    values={n:file_hash(PROJECT/'src'/n) for n in modules if (PROJECT/'src'/n).exists()}
    values.update({'config/'+p.name:file_hash(p) for p in sorted((PROJECT/'config').glob('*.json'))
                   if p.name in {'field_dictionary.json','contract_field_extensions.json','qdii25_profile.json'}})
    return values


def dictionary():
    fields = read_json(PROJECT / 'config/field_dictionary.json')
    extra = PROJECT / 'config/contract_field_extensions.json'
    if extra.exists():
        additions = read_json(extra)
        if isinstance(additions, dict): additions = additions.get('fields', [])
        known = {f['field_id'] for f in fields}
        fields += [f for f in additions if f['field_id'] not in known]
    return fields


def _product_name(value):
    return value if isinstance(value, str) else value.get('name', '')


def _check_ids(values, key):
    ids = [x.get(key) for x in values]
    if any(not x for x in ids) or len(ids) != len(set(ids)):
        raise ReviewError(f'{key}为空或重复')


def validate_review(task, review):
    """Bind every change to analyzed source/product/slot, with evidence and review."""
    if task.get('engine_hashes') != engine_hashes():
        raise ReviewError('分析引擎已更新或任务缺少版本依据；请重新分析后审阅')
    if review.get('task_id') != task['task_id']:
        raise ReviewError('审阅文件不属于本任务；请重新分析来源版本')
    materials = {m['material_id']: m for m in task['materials']}
    hashes = review.get('source_hashes', {})
    for mid, material in materials.items():
        if hashes.get(mid) != material['source']['sha256']:
            raise ReviewError(f'{mid}来源版本与审阅文件不一致')
    if task.get('contract', {}).get('source'):
        if hashes.get('contract') != task['contract']['source']['sha256']:
            raise ReviewError('合同来源版本与审阅文件不一致')
    facts = {f['fact_id']: f for f in task['contract']['facts']}
    supplements = deepcopy(review.get('supplements', []))
    if supplements: _check_ids(supplements, 'supplement_id')
    target = _product_name(task['target_product'])
    for item in supplements:
        if item.get('product_name') != target:
            raise ReviewError('补充信息的产品与目标产品不一致')
        if not item.get('field_id') or item.get('value') in (None, '') or not item.get('source_note'):
            raise ReviewError('补充信息须有字段、值和来源说明')
        item['provenance'] = 'user_supplied'
    supplement_map = {s['supplement_id']: s for s in supplements}

    def evidence_check(evidence, field_id=None, replacing=False):
        if not evidence:
            raise ReviewError('修改或结案必须附依据')
        authoritative = False
        for e in evidence:
            typ, eid = e.get('type'), e.get('id')
            if typ == 'fact' and eid in facts:
                if replacing and facts[eid]['field_id'] != field_id:
                    raise ReviewError('合同依据字段与目标字段不一致；须先核实字段映射')
                authoritative = True
            elif typ == 'supplement' and eid in supplement_map:
                if field_id and supplement_map[eid]['field_id'] != field_id:
                    raise ReviewError('补充信息字段与目标字段不一致')
                authoritative = True
            elif typ == 'material' and eid in materials:
                pass  # May explain an unresolved comment; cannot alone replace facts.
            else:
                raise ReviewError(f'未知或无效依据：{typ}/{eid}')
        if replacing and not authoritative:
            raise ReviewError('文本更新须有合同或用户补充依据，旧物料不能单独证明当前事实')

    decisions = review.get('decisions', [])
    if decisions: _check_ids(decisions, 'decision_id')
    operations = {mid: [] for mid in materials}
    used = set()
    for d in decisions:
        mid = d.get('material_id')
        if mid not in materials: raise ReviewError('未知物料')
        m = materials[mid]
        slot = next((s for s in m['slots'] if s['slot_id'] == d.get('slot_id')), None)
        if slot is None: raise ReviewError('未知目标槽位')
        region = next((r for r in m['regions'] if r['region_id'] == slot['region_id']), None)
        if slot.get('product_status') != 'target' or not region or region.get('status') != 'target':
            raise ReviewError('仅允许修改已确定属于目标产品的区域')
        kind = d.get('kind')
        if kind not in ('replace_text', 'comment'): raise ReviewError('不支持的修改类型')
        if kind == 'replace_text' and not slot.get('editable'): raise ReviewError('目标槽位不可安全编辑')
        if not d.get('reviewed') or not d.get('reason'): raise ReviewError('修改必须经过审阅并填写理由')
        mapped_field = slot.get('field_id')
        if kind == 'replace_text' and not mapped_field:
            mapped_field = d.get('target_field_id')
            if not mapped_field or not d.get('field_mapping_reason'):
                raise ReviewError('未分类槽位须明确目标字段及映射理由')
        evidence_check(d.get('evidence'), mapped_field, kind == 'replace_text')
        before = d.get('before', '')
        if not before or slot['text'].count(before) != 1:
            raise ReviewError('修改前原文须在所选槽位中精确且唯一出现')
        if kind == 'replace_text' and (not isinstance(d.get('after'), str) or d['after'] == before):
            raise ReviewError('替换文本为空类型或未发生变化')
        key = (mid, slot['part'], slot['xpath'], kind, before)
        if key in used: raise ReviewError('同一原文范围重复修改；请合并为一次审阅决定')
        used.add(key)
        operations[mid].append({'op_id': d['decision_id'], 'kind': kind,
                                'part': slot['part'], 'xpath': slot['xpath'], 'before': before,
                                'after': d.get('after', ''), 'reason': d['reason'],
                                'evidence': d['evidence'], 'region_id': slot['region_id']})
    checks = {c['check_id']: c for c in task.get('checks', [])}
    resolutions = review.get('resolutions', [])
    resolved = set()
    for r in resolutions:
        cid = r.get('check_id')
        if cid not in checks or cid in resolved: raise ReviewError('结案检查项不存在或重复')
        if not r.get('reason') or not r.get('reviewed'): raise ReviewError('检查项结案需要审阅理由')
        check=checks[cid]
        required_field=check.get('required_evidence_field') or check.get('field_id')
        evidence_check(r.get('evidence'), required_field)
        matches=[]
        for e in r['evidence']:
            if e['type']=='supplement':
                item=supplement_map[e['id']]
                if not required_field or item['field_id']==required_field: matches.append('supplement')
            elif e['type']=='fact' and (not required_field or facts[e['id']]['field_id']==required_field):
                matches.append('fact')
        if check.get('needs_external_evidence') and 'supplement' not in matches:
            raise ReviewError('该检查须补充对应字段的本次有效依据，合同一般规则及旧物料不能单独结案')
        if required_field and not matches:
            raise ReviewError('结案依据须对应检查所缺字段')
        resolved.add(cid)
    return {'operations': operations, 'supplements': supplements, 'resolved_checks': sorted(resolved)}


def analyze(contract_path, material_paths, *, product=None, as_of=None, project_dir=None):
    from contract_facts import build_contract_facts
    from material_regions import analyze_material
    from case_checks import inspect_cases
    contract_path = Path(contract_path).resolve()
    if not material_paths: raise ReviewError('合同之外至少需要一份PPT、一页通或海报')
    paths = [Path(p).resolve() for p in material_paths]
    if len(set(paths)) != len(paths) or contract_path in paths:
        raise ReviewError('输入文件重复，或把合同作为目标物料')
    contract = build_contract_facts(contract_path, project_dir=project_dir or PROJECT)
    identity = contract['product']
    if not _product_name(identity) or identity.get('status') != 'identified_from_contract':
        raise ReviewError('合同产品身份未知或冲突；不能用--product覆盖，需核实合同并重新分析')
    if product:
        # A supplied identity disambiguates the contract; never silently changes it.
        known = [_product_name(identity)] + identity.get('aliases', [])
        import re
        norm = lambda s: re.sub(r'[\s·•\-—（）()【】]', '', s)
        if _product_name(identity) and norm(product) not in {norm(x) for x in known}:
            raise ReviewError('指定产品与合同识别结果不同，请核对合同身份')
        identity = {**identity, 'name': product}
    if not _product_name(identity): raise ReviewError('合同未能确定产品名称，需核实身份后再继续')
    fields = dictionary()
    materials = []
    for path in paths:
        m = analyze_material(path, identity, fields)
        m['material_id'] = 'm-' + sha256(str(path).encode()).hexdigest()[:12]
        materials.append(m)
    as_of = as_of or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    datetime.strptime(as_of, '%Y-%m-%d')
    payload = {'version': CORE_VERSION, 'contract': contract['source']['sha256'],
               'materials': [(m['material_id'], m['source']['sha256']) for m in materials],
               'product': identity, 'as_of': as_of, 'dictionary': fields,
               'engine_hashes': engine_hashes()}
    task_id = 'task-' + sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    task = {'schema_version': 1, 'core_version': CORE_VERSION, 'task_id': task_id,
            'as_of': as_of, 'target_product': identity, 'contract': contract, 'materials': materials,
            'engine_hashes': payload['engine_hashes']}
    task['checks'] = inspect_cases(task)
    task['summary'] = {'material_count': len(materials), 'fact_count': len(contract['facts']),
                       'check_count': len(task['checks']),
                       'target_regions': sum(r['status'] == 'target' for m in materials for r in m['regions']),
                       'other_regions': sum(r['status'] == 'other' for m in materials for r in m['regions']),
                       'ambiguous_regions': sum(r['status'] == 'ambiguous' for m in materials for r in m['regions'])}
    return task


def review_template(task):
    return {'schema_version': 1, 'task_id': task['task_id'],
            'source_hashes': {'contract': task['contract']['source']['sha256'],
                              **{m['material_id']: m['source']['sha256'] for m in task['materials']}},
            'supplements': [], 'decisions': [], 'resolutions': [],
            'instructions': '补充信息独立于合同事实；按slot_id局部审阅，不能修改other/ambiguous区域。日期只是个例之一。'}


def save_analysis(task, output_dir):
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'task.json', task)
    template = out / 'review.template.json'
    write_json(template, review_template(task))
    from task_report import write_reports
    write_reports(task, out)
    return out / 'task.json'


def apply_task(task, review, output_dir):
    from material_writer import apply_patch_plan
    checked = validate_review(task, review)
    # Validate every input before mutating any output; never partly run stale review.
    for source in [task['contract']['source']] + [m['source'] for m in task['materials']]:
        if not source_version_matches(source):
            raise ReviewError(f"源文件已变化，必须重新分析：{source['path']}")
    out = Path(output_dir).resolve()
    source_dirs = {Path(m['source']['path']).resolve() for m in task['materials']}
    if out in source_dirs: raise ReviewError('输出目录不能是输入文件')
    out.mkdir(parents=True, exist_ok=True)
    destinations={}
    for m in task['materials']:
        source=Path(m['source']['path'])
        target=out/(m['material_id']+'_'+source.stem+('_审阅稿' if source.suffix.lower()=='.docx' else '_更新稿')+source.suffix)
        if target.resolve()==source.resolve(): raise ReviewError('禁止覆盖原文件')
        if target.exists(): raise ReviewError(f'输出已存在，请使用新输出目录：{target.name}')
        destinations[m['material_id']]=target
    results = []
    for m in task['materials']:
        mid, source = m['material_id'], Path(m['source']['path'])
        operations = checked['operations'][mid]
        target=destinations[mid]
        pending = [c for c in task['checks'] if c.get('material_id') in (None, mid)
                   and c['check_id'] not in checked['resolved_checks'] and c.get('severity') != 'info']
        if operations:
            try:
                result = apply_patch_plan(source, target, operations, m['source']['sha256'])
                status = 'updated_with_pending_checks' if pending else 'updated_pending_visual_qa'
            except (ValueError, NotImplementedError) as exc:
                # Preserve the original byte-for-byte in the review package; other
                # materials can still finish, with failure prominently retained.
                if target.exists(): target.unlink()
                shutil.copy2(source, target)
                result = {'passed': False, 'issues': [str(exc)], 'changed_parts': []}
                status = 'edit_blocked_source_copy'
        else:
            shutil.copy2(source, target)
            result = {'passed': True, 'changed_parts': [], 'operation_results': []}
            status = 'pending_review_source_copy' if pending else 'unchanged_source_copy'
        results.append({'material_id': mid, 'source': str(source), 'output': str(target),
                        'status': status, 'pending_check_ids': [c['check_id'] for c in pending],
                        'operation_count': len(operations), 'verification': result,
                        'visual_status': 'not_checked', 'output_sha256': file_hash(target)})
    result = {'schema_version': 1, 'task_id': task['task_id'], 'results': results, 'applied_review': review,
              'supplements': checked['supplements'], 'resolved_checks': checked['resolved_checks'],
              'source_hashes_unchanged': all(source_version_matches(s) for s in
                                           [task['contract']['source']] + [m['source'] for m in task['materials']]),
              'all_updates_complete': False,
              'note': '结构验证不代替原生打开和视觉验收；含待核项的文件不会标记全部完成。'}
    write_json(out / 'update_result.json', result)
    write_json(out / 'review.applied.json', review)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    a = sub.add_parser('analyze')
    a.add_argument('--contract', required=True)
    a.add_argument('--material', action='append', required=True)
    a.add_argument('--out', required=True)
    a.add_argument('--product')
    a.add_argument('--as-of')
    a.add_argument('--project-dir')
    b = sub.add_parser('apply')
    b.add_argument('--task', required=True)
    b.add_argument('--review', required=True)
    b.add_argument('--out', required=True)
    c = sub.add_parser('validate')
    c.add_argument('--task', required=True)
    c.add_argument('--result', required=True)
    d = sub.add_parser('accept',help='登记已完成的原生打开及视觉验收，重新检查输出版本')
    d.add_argument('--task',required=True)
    d.add_argument('--result',required=True)
    d.add_argument('--qa',required=True)
    d.add_argument('--out',required=True)
    args = parser.parse_args()
    if args.command == 'analyze':
        task = analyze(args.contract, args.material, product=args.product, as_of=args.as_of, project_dir=args.project_dir)
        print(json.dumps({'task': str(save_analysis(task, args.out)), **task['summary']}, ensure_ascii=False))
    elif args.command == 'apply':
        result = apply_task(read_json(args.task), read_json(args.review), args.out)
        print(json.dumps({k: result[k] for k in ['task_id','source_hashes_unchanged','all_updates_complete']}, ensure_ascii=False))
    elif args.command == 'accept':
        from task_validation import record_acceptance
        accepted=record_acceptance(read_json(args.task),read_json(args.result),read_json(args.qa))
        write_json(args.out,accepted)
        print(json.dumps({'all_updates_complete':accepted['all_updates_complete'],'result':args.out},ensure_ascii=False))
    else:
        from task_validation import validate_task_outputs
        result = validate_task_outputs(read_json(args.task), read_json(args.result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
