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
import re
import shutil

CORE_VERSION = '2.4.0'
PROJECT = Path(__file__).resolve().parents[1]
PENDING_PLACEHOLDER = '【待补充】'
STATIC_TITLES = {'产品要素','产品要素表','产品概要','产品基本信息','基本信息','产品信息',
                 '风险提示','重要提示','产品概况','投资策略','投资范围','收益分配','费用说明'}


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
    if source.get('original_path'):
        pairs.append((source['original_path'],source.get('original_sha256')))
    return all(digest and Path(path).is_file() and file_hash(path)==digest for path,digest in pairs)


def engine_hashes():
    modules=['contract_facts.py','contract_recheck.py','requirement_review.py','material_regions.py','case_checks.py','material_task.py','material_writer.py',
              'corpus.py','qdii_pipeline.py','task_validation.py','task_report.py','ppt_delivery.py','material_cli.py','material_delivery.py',
              'word_preparation.py','template_retention.py','review_packet.py','text_spans.py']
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


def is_template_task(task):
    return task.get('material_mode') == 'template'


def template_required_slots(material):
    """Only selected template content that can appear in the delivery is required."""
    return [s for s in material['slots'] if s.get('product_status') == 'target'
            and s.get('delivery_required', True)]


def _static_slot(slot):
    text = re.sub(r'[\s：:]+', '', slot['text'])
    if re.search(r'\d|[%％]', text): return False
    return (text in STATIC_TITLES or (slot.get('content_role') == 'label_only'
            and text == re.sub(r'[\s：:]+', '', slot.get('label', '')) and bool(text)))


def _mapped_field(slot, record, *, required=True):
    field = slot.get('field_id')
    supplied = record.get('target_field_id')
    if supplied and supplied != field:
        if field and not (field in {'opening_schedule','redemption_window'} and supplied == 'current_open_date'):
            raise ReviewError('已识别字段不能改映射为不相关字段')
        if not record.get('field_mapping_reason'):
            raise ReviewError('字段重映射须填写映射理由')
        field = supplied
    if required and not field:
        raise ReviewError('未分类槽位须明确目标字段及映射理由')
    return field


def _final_slot_text(slot, decisions):
    from text_spans import locate_span
    text = slot['text']
    ranges = []
    for decision in decisions:
        if decision['kind'] not in {'replace_text','placeholder'}: continue
        start,end = locate_span(text,decision['before'],decision.get('start'))
        ranges.append((start,end,decision['after']))
    ranges.sort()
    if any(right[0] < left[1] for left, right in zip(ranges, ranges[1:])):
        raise ReviewError('同一槽位替换范围重叠')
    for start, end, after in reversed(ranges): text = text[:start] + after + text[end:]
    return text


def _number_tokens(text):
    # Normalize harmless display differences, e.g. 0.50% versus 0.5%.
    from decimal import Decimal
    return {str(Decimal(x).normalize()) for x in re.findall(r'\d+(?:\.\d+)?', text)}


def minimal_template_task(task):
    version=tuple(int(x) for x in re.findall(r'\d+',task.get('core_version','0.0'))[:2])
    return is_template_task(task) and (task.get('editing_policy')=='template_minimal' or version >= (2,4))


def _minimal_replacement(before,after,start):
    """Do not put identical template copy into Word deletion/insertion runs."""
    left=0
    while left<min(len(before),len(after)) and before[left]==after[left]: left+=1
    right=0
    while right<min(len(before)-left,len(after)-left) and before[-right-1]==after[-right-1]: right+=1
    if len(before)-left-right==0:
        if left: left-=1
        elif right: right-=1
    return before[left:len(before)-right],after[left:len(after)-right],start+left


def validate_review(task, review):
    """Bind every change to analyzed source/product/slot, with evidence and review."""
    if task.get('engine_hashes') != engine_hashes():
        raise ReviewError('分析引擎已更新或任务缺少版本依据；请重新分析后审阅')
    if review.get('task_id') != task['task_id']:
        raise ReviewError('审阅文件不属于本任务；请重新分析来源版本')
    materials = {m['material_id']: m for m in task['materials']}
    template_mode = is_template_task(task)
    minimal_mode = minimal_template_task(task)
    from requirement_review import enabled, validate_requirements, validate_operation_requirements, bind_slot_requirements
    atomic_mode = enabled(task)
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
    rechecks = {}
    requirement_state = None
    if atomic_mode or review.get('contract_rechecks') or review.get('new_fields'):
        from contract_recheck import validate_rechecks
        try:
            rechecks = validate_rechecks(task, review)
            if atomic_mode:requirement_state = validate_requirements(task, review, rechecks)
        except ValueError as exc:raise ReviewError(str(exc)) from exc

    def requirement_operation(record, mid, sid, *, allow_template=False):
        try:validate_operation_requirements(requirement_state,record,mid,sid,allow_template=allow_template)
        except ValueError as exc:raise ReviewError(str(exc)) from exc

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
            elif typ == 'recheck' and eid in rechecks:
                if replacing and rechecks[eid]['field_id'] != field_id:
                    raise ReviewError('合同回查依据字段与目标字段不一致')
                authoritative = True
            elif typ == 'material' and eid in materials:
                pass  # May explain an unresolved comment; cannot alone replace facts.
            elif typ == 'template' and requirement_state and eid in requirement_state.get('template_retentions',{}):
                if replacing: raise ReviewError('沿用模板的依据只能保留原值及加批注，不能授权改写新值')
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
        if kind not in ('replace_text', 'comment', 'placeholder', 'highlight'): raise ReviewError('不支持的修改类型')
        if minimal_mode and kind=='placeholder':
            raise ReviewError('默认保留模板原文并局部标黄，不能用整槽占位清空缺项')
        if kind == 'placeholder' and not template_mode: raise ReviewError('占位决定仅用于模板模式')
        if kind in {'replace_text','placeholder','highlight'} and not slot.get('editable'): raise ReviewError('目标槽位不可安全编辑')
        if not d.get('reviewed') or not d.get('reason'): raise ReviewError('修改必须经过审阅并填写理由')
        if atomic_mode and kind == 'replace_text':
            requirement_operation(d,mid,slot['slot_id'])
        else:
            mapped_field = _mapped_field(slot, d, required=kind == 'replace_text')
            evidence_check(d.get('evidence'), mapped_field, kind == 'replace_text')
        before = d.get('before', '')
        from text_spans import locate_span
        try:start,end=locate_span(slot['text'],before,d.get('start'))
        except ValueError as exc:raise ReviewError('修改前原文须精确定位：'+str(exc)) from exc
        if kind == 'placeholder' and (before != slot['text'] or d.get('after') != PENDING_PLACEHOLDER):
            raise ReviewError('缺项占位必须整槽替换为【待补充】，不可保留旧模板数值')
        if kind in {'replace_text','placeholder'} and (not isinstance(d.get('after'), str) or d['after'] == before):
            raise ReviewError('替换文本为空类型或未发生变化')
        if kind=='highlight':
            if d.get('color')!='yellow':raise ReviewError('待核参数仅使用yellow局部标黄')
            pending_sources=requirement_state.get('template_retentions',{}) if requirement_state else {}
            if not any(source.get('status')=='template_pending' and d['decision_id'] in source.get('highlight_decision_ids',[])
                       for source in pending_sources.values()):
                raise ReviewError('局部标黄必须关联已回查的待核模板参数')
        key = (mid, slot['part'], slot['xpath'], kind, start,end)
        if key in used: raise ReviewError('同一原文范围重复修改；请合并为一次审阅决定')
        used.add(key)
        op={'op_id': d['decision_id'], 'kind': 'replace_text' if kind == 'placeholder' else kind,
                                'part': slot['part'], 'xpath': slot['xpath'], 'before': before,
                                'after': d.get('after', ''), 'reason': d['reason'],
                                'evidence': d['evidence'], 'region_id': slot['region_id']}
        if 'start' in d:op['start']=start
        if kind=='highlight':
            op['color']='yellow'
            op.pop('after',None)
        if minimal_mode and kind=='replace_text':
            old,new,position=_minimal_replacement(before,d['after'],start)
            linked=[requirement_state['records'][rid] for rid in d.get('requirement_ids',[])] if requirement_state else []
            name_change=bool(linked) and all(q['field_id']=='product_name' for q in linked)
            parameter=re.fullmatch(r'[0-9A-Za-z\s%％.,，/\\+\-—年月日号期()（）:：、]*',old+new)
            if not parameter and not name_change and not str(d.get('wording_change_reason','')).strip():
                raise ReviewError('模板话术默认保持；必要文字或条件变化须填写wording_change_reason说明最小措辞调整')
            op.update(before=old,after=new,start=position)
        operations[mid].append(op)
    coverage = {}
    dispositions = {}
    if not template_mode and review.get('slot_dispositions'):
        raise ReviewError('槽位内容处置仅用于模板模式')
    for mid, material in materials.items():
        if not template_mode: continue
        required = template_required_slots(material)
        coverage[mid] = {'required_slot_ids':[s['slot_id'] for s in required],
                         'unreviewed_slot_ids':[s['slot_id'] for s in required],
                         'pending_slot_ids':[], 'completed_slot_ids':[], 'final_texts':{}}
    for record in review.get('slot_dispositions', []):
        mid, sid = record.get('material_id'), record.get('slot_id')
        key = (mid, sid)
        if mid not in coverage or sid not in coverage[mid]['required_slot_ids'] or key in dispositions:
            raise ReviewError('内容处置槽位不在交付范围内或重复')
        slot = next(s for s in materials[mid]['slots'] if s['slot_id'] == sid)
        if not record.get('reviewed') or not record.get('reason'):
            raise ReviewError('槽位内容处置必须审阅并填写理由')
        action = record.get('action')
        if action not in {'rewrite','retain_verified','retain_template_exception','retain_marked','pending_placeholder','not_applicable','retain_static'}:
            raise ReviewError('未知槽位内容处置动作')
        peers = [d for d in decisions if d['material_id'] == mid and d['slot_id'] == sid]
        replacements = [d for d in peers if d['kind'] in {'replace_text','placeholder'}]
        ids = record.get('decision_ids')
        if (not isinstance(ids, list) or len(ids) != len(set(ids))
                or set(ids) != {d['decision_id'] for d in replacements}):
            raise ReviewError('内容处置须准确关联本槽位全部文本修改决定')
        final = _final_slot_text(slot, peers)
        if not isinstance(record.get('final_text'), str) or record['final_text'] != final:
            raise ReviewError('内容处置最终正文与实际修改结果不一致')
        source_product=materials[mid].get('template_source_product') or {}
        normalized=lambda value: re.sub(r'[\s·•\-—（）()【】]', '', value)
        target_identity=task['target_product'] if isinstance(task['target_product'],dict) else {'name':target}
        destination_names=[target]+target_identity.get('aliases',[])
        old_names=[source_product.get('name')]+source_product.get('aliases',[])
        for old_name in (name for name in old_names if isinstance(name,str) and name.strip()):
            if (normalized(old_name) not in {normalized(name) for name in destination_names}
                    and normalized(old_name) in normalized(final)):
                raise ReviewError('最终正文仍含旧模板主体名称；须按合同换填或保留待补状态')
        if action == 'retain_static':
            if replacements or not _static_slot(slot):
                raise ReviewError('静态保留仅适用于明确字段标签或通用标题，不能保留产品事实或数值')
            evidence_check(record.get('evidence'))
        elif action == 'pending_placeholder':
            if final != PENDING_PLACEHOLDER or any(d['kind'] != 'placeholder' for d in replacements):
                raise ReviewError('待补处置必须用整槽占位决定清除旧内容')
            if not replacements and slot['text'] != PENDING_PLACEHOLDER:
                raise ReviewError('待补处置缺少实际占位修改')
            evidence_check(record.get('evidence'))
        else:
            if atomic_mode:
                requirement_operation(record,mid,sid,allow_template=True)
            else:
                field = _mapped_field(slot, record)
                evidence_check(record.get('evidence'), field, replacing=True)
            if any(d['kind'] == 'placeholder' for d in replacements):
                raise ReviewError('缺项占位不能标记内容完成')
            if action == 'rewrite' and not replacements:
                raise ReviewError('重写处置必须关联实际正文修改')
            retained = [requirement_state['records'][rid] for rid in record.get('requirement_ids',[])
                        if atomic_mode and requirement_state['records'][rid]['status'] in {'template_retained','template_pending'}]
            marked=[r for r in retained if r['status']=='template_pending']
            if minimal_mode and any(r['status']=='template_retained' for r in retained):
                raise ReviewError('新版缺项须保留原值并局部标黄，使用template_pending而非旧模板例外')
            if action=='retain_marked' and (not marked or replacements):
                raise ReviewError('retain_marked须有关联标黄的待核参数且无正文替换；混合修改用rewrite')
            if action == 'retain_template_exception':
                if not retained or replacements:
                    raise ReviewError('模板例外保留须有已校验保留需求且不能有正文修改；混合修改使用rewrite')
            if action in {'retain_verified','retain_template_exception','retain_marked'}:
                if action == 'retain_verified' and retained:
                    raise ReviewError('沿用模板不能标为已核实保留，待核参数须使用retain_marked')
                if replacements: raise ReviewError('有正文修改应使用rewrite处置')
            if action in {'retain_verified','retain_template_exception','retain_marked'} or (minimal_mode and action=='rewrite'):
                source_map={**{('fact',fid):f for fid,f in facts.items()},**{('recheck',fid):f for fid,f in rechecks.items()},
                            **{('supplement',fid):f for fid,f in supplement_map.items()}}
                if atomic_mode:
                    source_map.update({('template',rid):source for rid,source in requirement_state.get('template_retentions',{}).items()})
                evidence_text='\n'.join(str(source_map[(e['type'],e['id'])].get('raw_text',''))+' '+
                    str(source_map[(e['type'],e['id'])].get('value','')) for e in record['evidence']
                    if (e['type'],e['id']) in source_map)
                if not _number_tokens(final).issubset(_number_tokens(evidence_text)):
                    raise ReviewError('保留正文中的数值未获所引合同或补充依据支持')
            if action == 'not_applicable' and final not in {'','不适用'}:
                raise ReviewError('不适用处置必须删除旧内容或明确写入不适用')
        dispositions[key] = record
        item = coverage[mid]
        item['unreviewed_slot_ids'].remove(sid)
        item['final_texts'][sid] = final
        item['pending_slot_ids' if action == 'pending_placeholder' else 'completed_slot_ids'].append(sid)
    requirement_coverage={}
    if atomic_mode:
        try:requirement_coverage=bind_slot_requirements(requirement_state,coverage,dispositions)
        except ValueError as exc:raise ReviewError(str(exc)) from exc
    checks = {c['check_id']: c for c in task.get('checks', [])}
    resolutions = review.get('resolutions', [])
    resolved = {cid for cid, c in checks.items() if c.get('type') == 'template_content_review'
                and (c.get('material_id'),c.get('slot_id')) in dispositions
                and c['slot_id'] in coverage[c['material_id']]['completed_slot_ids']}
    resolution_ids = set()
    for r in resolutions:
        cid = r.get('check_id')
        if cid not in checks or cid in resolution_ids:
            raise ReviewError('结案检查项不存在或重复')
        resolution_ids.add(cid)
        if not r.get('reason') or not r.get('reviewed'): raise ReviewError('检查项结案需要审阅理由')
        check=checks[cid]
        if check.get('requires_reanalysis'):
            raise ReviewError('模板范围或结构检查不能仅凭结案声明跳过；须处理来源模板后重新分析')
        if template_mode and check.get('slot_id'):
            disposition = dispositions.get((check.get('material_id'),check['slot_id']))
            if not disposition or disposition['action'] == 'pending_placeholder':
                raise ReviewError('检查结案须先完成对应槽位的正文处置，补充来源不等于已更新正文')
            if disposition['action'] == 'retain_static' and check.get('type') != 'template_content_review':
                raise ReviewError('静态标签处置不能结案产品内容检查')
        required_field=check.get('required_evidence_field') or check.get('field_id')
        if atomic_mode and check.get('slot_id'):
            ids=r.get('requirement_ids',[])
            candidates=[requirement_state['records'].get(rid) for rid in ids]
            if (not ids or any(not req or (req['material_id'],req['slot_id'])!=(check['material_id'],check['slot_id'])
                              or req.get('reviewed') is not True or req['status'] not in {'verified','not_applicable','template_retained'} for req in candidates)):
                raise ReviewError('检查结案须关联同槽位已核实或有依据不适用的原子需求')
            relevant=[req for req in candidates if not required_field or req['field_id']==required_field]
            if check.get('type')=='missing_evidence' and not check.get('required_evidence_field'):
                # A generic field seed may be refined after rereading the
                # template. Only that check's original, same-slot candidates
                # can authorize this narrow remapping, never an arbitrary field.
                bound=set(check.get('candidate_ids',[]))
                for req in candidates:
                    seed=requirement_state['candidates'].get(req.get('candidate_id'))
                    if (seed and seed['candidate_id'] in bound and seed.get('field_id')==check.get('field_id')
                            and (seed['material_id'],seed['slot_id'])==(check['material_id'],check['slot_id'])
                            and isinstance(req.get('field_mapping_reason'),str) and req['field_mapping_reason'].strip()
                            and req not in relevant):
                        relevant.append(req)
            if not relevant:raise ReviewError('结案需求字段未覆盖该检查项')
            if any(req['status']=='template_retained' for req in relevant):
                if check.get('type') not in {'missing_evidence','external_information','external_parameter'}:
                    raise ReviewError('沿用模板只能处置对应字段缺项，不能跳过冲突、结构或其他核对检查')
            allowed={(e['type'],e['id']) for req in relevant for e in req['evidence']}
            if not r.get('evidence') or not {(e['type'],e['id']) for e in r['evidence']}.issubset(allowed):
                raise ReviewError('结案依据须来自所关联的原子需求')
            # Applicability is assessed first. Contract-backed inapplicability
            # never forces a fictional current date or a user-supplied source.
            if all(req['status']=='not_applicable' for req in relevant):
                resolved.add(cid);continue
            if check.get('needs_external_evidence'):
                # A complete source-bound recheck may itself supply this value.
                # verified status records explicit applicability/source review.
                for req in relevant:
                    if req['rendered_text'] not in disposition['final_text']:
                        raise ReviewError('结案原子需求尚未写入最终正文')
            resolved.add(cid);continue
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
        if template_mode and check.get('slot_id') and check.get('needs_external_evidence'):
            final = disposition['final_text']
            actual_evidence = {(e['type'],e['id']) for e in disposition['evidence']}
            selected = [supplement_map[e['id']] for e in r['evidence'] if e['type']=='supplement'
                        and (not required_field or supplement_map[e['id']]['field_id']==required_field)]
            if not any(('supplement', s['supplement_id']) in actual_evidence for s in selected):
                raise ReviewError('外部信息结案依据必须同时用于最终正文处置')
            if not any(_number_tokens(str(s['value'])).issubset(_number_tokens(final)) for s in selected):
                raise ReviewError('外部信息的新数值尚未出现在最终正文')
        resolved.add(cid)
    return {'operations': operations, 'supplements': supplements, 'resolved_checks': sorted(resolved),
            'template_coverage':coverage,'requirement_coverage':requirement_coverage,'contract_rechecks':rechecks,
            'template_retentions':requirement_state.get('template_retentions',{}) if requirement_state else {}}


def analyze(contract_path, material_paths, *, product=None, as_of=None, project_dir=None,
            material_mode='template', template_map=None, preparation_dir=None):
    from contract_facts import build_contract_facts
    from material_regions import analyze_material
    from case_checks import inspect_cases
    contract_path = Path(contract_path).resolve()
    if material_mode not in {'template','review'}: raise ReviewError('物料模式须为template或review')
    if not material_paths: raise ReviewError('合同之外至少需要一份PPT、一页通或海报')
    paths = [Path(p).resolve() for p in material_paths]
    if len(set(paths)) != len(paths) or contract_path in paths:
        raise ReviewError('输入文件重复，或把合同作为目标物料')
    if isinstance(template_map, (str, Path)): template_map = read_json(template_map)
    template_map = template_map if template_map is not None else {'templates':[]}
    if (not isinstance(template_map, dict) or not isinstance(template_map.get('templates'), list)
            or (material_mode != 'template' and template_map['templates'])):
        raise ReviewError('template_map须为模板模式下的templates数组')
    selections = {}
    for item in template_map['templates']:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise ReviewError('模板选择须有本次输入路径')
        path = Path(item['path']).resolve()
        if path not in paths or path in selections: raise ReviewError('模板选择路径不属于本次上传输入或重复')
        if not isinstance(item.get('product_name'), str) or not item['product_name'].strip() or not item.get('reason'):
            raise ReviewError('模板选择须有产品主体名称及范围选择理由')
        selections[path] = item
    contract = build_contract_facts(contract_path, project_dir=project_dir or PROJECT)
    identity = contract['product']
    if not _product_name(identity) or identity.get('status') != 'identified_from_contract':
        raise ReviewError('合同产品身份未知或冲突；不能用--product覆盖，需核实合同并重新分析')
    if product:
        # A supplied identity disambiguates the contract; never silently changes it.
        known = [_product_name(identity)] + identity.get('aliases', [])
        norm = lambda s: re.sub(r'[\s·•\-—（）()【】]', '', s)
        if _product_name(identity) and norm(product) not in {norm(x) for x in known}:
            raise ReviewError('指定产品与合同识别结果不同，请核对合同身份')
        identity = {**identity, 'name': product}
    if not _product_name(identity): raise ReviewError('合同未能确定产品名称，需核实身份后再继续')
    fields = dictionary()
    materials = []
    bound_templates = []
    for path in paths:
        selection = selections.get(path)
        template_product = {'name':selection['product_name'],'reason':selection['reason']} if selection else None
        material_id = 'm-' + sha256(str(path).encode()).hexdigest()[:12]
        analysis_path, preparation = path, None
        if path.suffix.lower() == '.docx' and path.is_file():
            from word_preparation import has_tracked_revisions, prepare_word
            if has_tracked_revisions(path):
                if preparation_dir is None:
                    raise ReviewError('Word模板含历史修订，请提供preparation_dir基线目录，或使用CLI analyze自动准备')
                original_sha256 = file_hash(path)
                if selection and selection.get('source_sha256') and selection['source_sha256'] != original_sha256:
                    raise ReviewError('模板选择来源哈希已变化，必须按本次实际文件重新选择')
                analysis_path = Path(preparation_dir).resolve() / material_id / path.name
                try:
                    preparation = prepare_word(path, analysis_path, expected_source_sha256=original_sha256)
                except (ValueError, RuntimeError, OSError) as exc:
                    raise ReviewError('Word历史修订预处理失败：' + str(exc)) from exc
                if (not isinstance(preparation, dict)
                        or preparation.get('policy') != 'accept_all_existing_revisions'
                        or Path(preparation.get('original_path', '')).resolve() != path
                        or preparation.get('original_sha256') != original_sha256
                        or Path(preparation.get('baseline_path', '')).resolve() != analysis_path
                        or preparation.get('remaining_revision_count') != 0
                        or not source_version_matches({'path':str(analysis_path),
                            'sha256':preparation.get('baseline_sha256'),
                            'original_path':str(path),'original_sha256':original_sha256})):
                    raise ReviewError('Word预处理基线记录与实际来源版本不一致')
        m = analyze_material(analysis_path, identity, fields, material_mode=material_mode, template_product=template_product)
        m['material_id'] = material_id
        if preparation is not None:
            m['source'].update(original_path=str(path), original_sha256=preparation['original_sha256'],
                               preparation=preparation)
        original_hash = m['source'].get('original_sha256', m['source']['sha256'])
        if selection:
            if selection.get('source_sha256') and selection['source_sha256'] != original_hash:
                raise ReviewError('模板选择来源哈希已变化，必须按本次实际文件重新选择')
            bound_templates.append({'path':str(path),'product_name':selection['product_name'],
                                     'reason':selection['reason'],'source_sha256':original_hash})
        materials.append(m)
    as_of = as_of or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    datetime.strptime(as_of, '%Y-%m-%d')
    payload = {'version': CORE_VERSION, 'contract': contract['source']['sha256'],
               'materials': [(m['material_id'], m['source']['sha256'],
                              m['source'].get('original_sha256', m['source']['sha256'])) for m in materials],
               'product': identity, 'as_of': as_of, 'dictionary': fields,
               'material_mode':material_mode, 'template_map':{'templates':bound_templates},
               'engine_hashes': engine_hashes()}
    task_id = 'task-' + sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    task = {'schema_version': 2, 'core_version': CORE_VERSION, 'task_id': task_id,
            'as_of': as_of, 'target_product': identity, 'contract': contract, 'materials': materials,
            'material_mode':material_mode, 'template_map':payload['template_map'],
            'field_dictionary':fields, 'delivery_required':True,
            'engine_hashes': payload['engine_hashes']}
    if material_mode=='template':
        from requirement_review import seed_requirements
        task['requirement_schema_version']=1
        task['editing_policy']='template_minimal'
        task['requirement_candidates']=seed_requirements(task)
    task['checks'] = inspect_cases(task)
    task['summary'] = {'material_count': len(materials), 'fact_count': len(contract['facts']),
                       'check_count': len(task['checks']),
                       'target_regions': sum(r['status'] == 'target' for m in materials for r in m['regions']),
                       'other_regions': sum(r['status'] == 'other' for m in materials for r in m['regions']),
                       'ambiguous_regions': sum(r['status'] == 'ambiguous' for m in materials for r in m['regions'])}
    return task


def review_template(task):
    return {'schema_version': 2, 'task_id': task['task_id'],
            'source_hashes': {'contract': task['contract']['source']['sha256'],
                              **{m['material_id']: m['source']['sha256'] for m in task['materials']}},
            'supplements': [], 'decisions': [], 'resolutions': [], 'slot_dispositions':[],
            'new_fields':[], 'contract_rechecks':[], 'requirements':[], 'delivery_plan':{},
            'instructions': ('合同唯一决定目标产品；模板是最近产品的正文和格式底稿，不因期号或名称不同再问目标。'
                             '保留模板原话术，仅对有依据的差异局部replace_text；必要措辞调整说明wording_change_reason。'
                             '缺失日期、代码、费率等保留原值，以template_pending关联局部yellow highlight及简短批注；禁止默认整槽待补占位。'
                             '逐一核对交付范围内slot_dispositions；已处理标黄可以交待核稿，但不算合同核实。other/ambiguous仍不属于所选模板。'
                             if is_template_task(task) else
                             '补充信息独立于合同事实；按slot_id局部审阅，不能修改other/ambiguous区域。日期只是个例之一。')}


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
    protected = {Path(source[key]).resolve()
                 for source in [task['contract']['source']] + [m['source'] for m in task['materials']]
                 for key in ('path','converted_path','original_path') if source.get(key)}
    if out in protected: raise ReviewError('输出目录不能是输入文件')
    out.mkdir(parents=True, exist_ok=True)
    internal = out / 'internal' if task.get('delivery_required') else out
    destinations={}
    for m in task['materials']:
        source=Path(m['source']['path'])
        target=internal/(m['material_id']+'_'+source.stem+('_审阅稿' if source.suffix.lower()=='.docx' else '_更新稿')+source.suffix)
        if target.resolve() in protected: raise ReviewError('禁止覆盖原文件')
        if target.exists(): raise ReviewError(f'输出已存在，请使用新输出目录：{target.name}')
        destinations[m['material_id']]=target
    internal.mkdir(parents=True, exist_ok=True)
    results = []
    for m in task['materials']:
        mid, source = m['material_id'], Path(m['source']['path'])
        operations = checked['operations'][mid]
        target=destinations[mid]
        pending = [c for c in task['checks'] if c.get('material_id') in (None, mid)
                   and c['check_id'] not in checked['resolved_checks'] and c.get('severity') != 'info']
        coverage = checked['template_coverage'].get(mid, {})
        pending_content = coverage.get('unreviewed_slot_ids',[]) + coverage.get('pending_slot_ids',[])
        if operations:
            try:
                result = apply_patch_plan(source, target, operations, m['source']['sha256'])
                status = 'updated_with_pending_checks' if pending or pending_content else 'updated_pending_visual_qa'
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
            status = 'pending_review_source_copy' if pending or pending_content else 'unchanged_source_copy'
        results.append({'material_id': mid, 'source': str(source), 'output': str(target),
                        'status': status, 'pending_check_ids': [c['check_id'] for c in pending],
                        'pending_content_slot_ids':pending_content,
                        'operation_count': len(operations), 'verification': result,
                        'visual_status': 'not_checked', 'output_sha256': file_hash(target)})
    result = {'schema_version': 1, 'task_id': task['task_id'], 'results': results, 'applied_review': review,
              'supplements': checked['supplements'], 'resolved_checks': checked['resolved_checks'],
              'template_coverage':checked['template_coverage'],
              'requirement_coverage':checked['requirement_coverage'],'contract_rechecks':checked['contract_rechecks'],
              'template_retentions':checked['template_retentions'],
              'source_hashes_unchanged': all(source_version_matches(s) for s in
                                           [task['contract']['source']] + [m['source'] for m in task['materials']]),
              'all_updates_complete': False,
              'note': '结构验证不代替原生打开和视觉验收；含待核项的文件不会标记全部完成。'}
    if task.get('delivery_required'):
        from material_delivery import build_deliveries
        deliveries = build_deliveries(task, result, out)
        result.update(delivery_required=True, deliveries=deliveries,
                      delivery_manifest=str(out / 'delivery_manifest.json'))
        write_json(result['delivery_manifest'], {'task_id':task['task_id'], 'delivery_required':True,
                                               'deliveries':deliveries})
    else:
        result['delivery_required'] = False
    write_json(out / 'update_result.json', result)
    write_json(out / 'review.applied.json', review)
    from task_report import write_review_reports
    write_review_reports(task,review,checked,out)
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
    a.add_argument('--material-mode', choices=['template','review'], default='template')
    a.add_argument('--template-map', help='本次上传模板范围选择JSON路径')
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
        task = analyze(args.contract, args.material, product=args.product, as_of=args.as_of, project_dir=args.project_dir,
                       material_mode=args.material_mode, template_map=args.template_map,
                       preparation_dir=Path(args.out).resolve() / 'prepared_templates')
        print(json.dumps({'task': str(save_analysis(task, args.out)), **task['summary']}, ensure_ascii=False))
    elif args.command == 'apply':
        result = apply_task(read_json(args.task), read_json(args.review), args.out)
        summary = {k: result[k] for k in ['task_id','source_hashes_unchanged','all_updates_complete']}
        if result.get('delivery_required'):
            summary.update(delivery_manifest=result['delivery_manifest'],
                           deliveries=[{k:d[k] for k in ('material_id','kind','status','output','pages','content_status','reason') if k in d}
                                       for d in result['deliveries']],
                           blocked_material_ids=[d['material_id'] for d in result['deliveries'] if d['status']=='blocked'])
        print(json.dumps(summary, ensure_ascii=False))
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
