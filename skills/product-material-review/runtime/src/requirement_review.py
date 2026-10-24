"""Atomic template requirements and source-bound review, not semantic inference.

Seeds are deterministic reminders. An agent must inspect every selected slot,
discover additional meanings, and explicitly review applicability and evidence.
"""
from collections import defaultdict
from hashlib import sha256
import re

STATUSES={'verified','not_applicable','needs_external','unresolved','unreadable','conflict'}
COMPLETE={'verified','not_applicable'}


def enabled(task):
    if task.get('material_mode')!='template': return False
    version=tuple(int(v) for v in re.findall(r'\d+',task.get('core_version','0.0'))[:2])
    return bool(task.get('requirement_schema_version')) or version >= (2,3)


def seed_requirements(task):
    from material_task import _static_slot, template_required_slots
    seeds=[]
    definitions=task.get('field_dictionary',[])
    for material in task['materials']:
        for slot in template_required_slots(material):
            if _static_slot(slot): continue
            text=slot['text']; field=slot.get('field_id')
            # Inspect labels throughout the slot, not only its inherited table
            # label. Longer aliases win overlaps; these are still candidates.
            matches=[]
            for definition in definitions:
                for alias in set([definition.get('name','')]+definition.get('aliases',[])):
                    if len(alias)<2:continue
                    pattern=re.escape(alias)+r'(?:率)?\s*(?:[：:]|为|是|(?=\d))'
                    for found in re.finditer(pattern,text):
                        matches.append((found.start(),found.end(),definition['field_id'],definition.get('name',alias)))
            accepted=[]
            for match in sorted(matches,key=lambda item:(-(item[1]-item[0]),item[0])):
                if not any(match[0]<prior[1] and prior[0]<match[1] for prior in accepted):accepted.append(match)
            detected=list(dict.fromkeys((m[2],m[3]) for m in sorted(accepted)))
            components=[(fid,'value',name) for fid,name in detected] or [(field,'value',slot.get('label') or '未分类内容需求')]
            for fid,_ in detected or [(field,'')]:
                if fid and 'fee' in fid:
                    if re.search(r'计提|计费|计算基数|前一日|净值|按日',text):
                        components.append((fid,'accrual','费用计提方式、基数及频率'))
                    if re.search(r'支付|划付|收取|扣收|结算',text):
                        components.append((fid,'payment','费用支付或收取方式及频率'))
            # Unknown labelled clauses remain discoverable without inventing a
            # reusable dictionary entry. AI must map or define their field.
            for found in re.finditer(r'(?:^|[；;，,、\n])\s*([^：:；;，,、\n\d]{2,24}?)(?:为[：:]?|[：:]|(?=\d))\s*(?=\S)',text):
                if not any(found.start()<m[1] and m[0]<found.end() for m in accepted):
                    label=found.group(1).strip()
                    components.append((None,'unknown_'+sha256(label.encode()).hexdigest()[:8],label))
            if re.search(r'下一开放期|下次开放|下一开放日|\d{1,2}月\d{1,2}日.{0,8}开放|开放.{0,8}\d{1,2}月\d{1,2}日',text):
                if field!='current_open_date':components.append(('current_open_date','current_date','本次或下次开放的具体日期'))
            if re.search(r'公告|另行通知|另行公布',text):
                components.append((field,'announcement_qualifier','公告提示及其与主要规则的适用关系'))
            for fid,component,description in list(dict.fromkeys(components)):
                key=f"{material['material_id']}|{slot['slot_id']}|{fid}|{component}"
                seeds.append({'candidate_id':'rq-'+sha256(key.encode()).hexdigest()[:14],
                    'material_id':material['material_id'],'slot_id':slot['slot_id'],'field_id':fid,
                    'component':component,'description':description,'template_quote':text,
                    'display_rule':'按模板显示层次核对，须由AI补充具体口径',
                    'status':'unresolved','discovery_method':'rule_seed_requires_semantic_review'})
    return seeds


def _refs(evidence):
    return {(e.get('type'),e.get('id')) for e in evidence}


def validate_requirements(task,review,rechecks):
    """Validate record provenance; semantic correctness remains reviewed judgment."""
    from material_task import dictionary, template_required_slots
    fields={f['field_id'] for f in task.get('field_dictionary',dictionary())}
    fields.update(f['field_id'] for f in review.get('new_fields',[]))
    slots={(m['material_id'],s['slot_id']):s for m in task['materials'] for s in template_required_slots(m)}
    sources={('fact',f['fact_id']):f for f in task['contract']['facts']}
    sources.update({('recheck',rid):fact for rid,fact in rechecks.items()})
    sources.update({('supplement',s['supplement_id']):s for s in review.get('supplements',[])})
    candidates={c['candidate_id']:c for c in task.get('requirement_candidates',[])}
    records={};by_slot=defaultdict(list);covered=set();searches={};needs_search=[];effective_status={};search_cache={}
    for record in review.get('requirements',[]):
        rid=record.get('requirement_id');key=(record.get('material_id'),record.get('slot_id'))
        if not rid or rid in records or key not in slots:raise ValueError('原子需求ID为空/重复，或槽位不在交付范围')
        if record.get('field_id') not in fields:raise ValueError('原子需求字段未在字段快照或new_fields中定义')
        for name in ('description','display_rule','reason'):
            if not isinstance(record.get(name),str) or not record[name].strip():raise ValueError('原子需求缺少'+name)
        quote=record.get('template_quote')
        if not isinstance(quote,str) or (slots[key]['text'] and not quote) or quote not in slots[key]['text']:
            raise ValueError('需求template_quote须精确来自本槽模板文字')
        status=record.get('status')
        if status not in STATUSES:raise ValueError('未知原子需求状态')
        rendered=record.get('rendered_text')
        if not isinstance(rendered,str):raise ValueError('原子需求rendered_text须为字符串')
        candidate_id=record.get('candidate_id')
        if candidate_id:
            candidate=candidates.get(candidate_id)
            if not candidate or (candidate['material_id'],candidate['slot_id'])!=key:
                raise ValueError('需求种子不属于本槽位')
            if candidate.get('field_id') and candidate['field_id']!=record['field_id'] and not record.get('field_mapping_reason'):
                raise ValueError('种子字段重映射须说明field_mapping_reason')
            covered.add(candidate_id)
        evidence=record.get('evidence',[])
        if not isinstance(evidence,list):raise ValueError('原子需求evidence须为数组')
        for reference in evidence:
            source=sources.get((reference.get('type'),reference.get('id')))
            if source is None:raise ValueError('原子需求依据不存在，模板不能证明目标产品事实')
            if status!='not_applicable' and source['field_id']!=record['field_id']:
                raise ValueError('原子需求证据字段与所需字段不一致')
        if status in COMPLETE and record.get('reviewed') is True:
            if not evidence:raise ValueError('核实或不适用需求必须有来源依据')
            if status=='verified' and not rendered.strip():raise ValueError('已核实需求须指定最终渲染文字')
            if status=='not_applicable' and not any(e['type'] in {'fact','recheck'} for e in evidence):
                raise ValueError('需求不适用须有合同或合同回查依据，不能仅凭模板或用户补充')
        effective_status[rid]=status if record.get('reviewed') is True else 'unresolved'
        if status=='needs_external':
            search=record.get('contract_search')
            if search is None:
                # A proposed user question remains a recheck task until an
                # actual contract search has been recorded and replayed.
                needs_search.append(rid);effective_status[rid]='unresolved'
            else:
                if (not isinstance(search,dict) or not isinstance(search.get('queries'),list) or not search['queries']
                        or any(not isinstance(q,str) or not q.strip() for q in search['queries'])
                        or not isinstance(search.get('conclusion'),str) or not search['conclusion'].strip()):
                    raise ValueError('待外部补充需求须记录contract_search的非空queries和具体conclusion')
                if search.get('source_sha256')!=task['contract']['source']['sha256']:
                    raise ValueError('需求回查声明合同哈希不匹配')
                from contract_recheck import search_contract
                cache_key=tuple(search['queries'])
                if cache_key not in search_cache:search_cache[cache_key]=search_contract(task,search['queries'])
                actual=search_cache[cache_key]
                if actual['source_status']!='verified':raise ValueError('待补需求的合同来源不可用或已变化，须重新核对')
                searches[rid]={'queries':actual['queries'],'source_sha256':search['source_sha256'],
                    'conclusion':search['conclusion'],'hit_count':actual['total_match_count'],
                    'returned_block_ids':[m['block_id'] for m in actual['matches']],
                    'truncated':actual['truncated'],'absence_proven':False,'warnings':actual['warnings']}
                if any(w.get('code')=='unreadable_source_content' for w in actual['warnings']):
                    needs_search.append(rid);effective_status[rid]='unreadable'
        records[rid]=record;by_slot[key].append(rid)
    counts={status:0 for status in STATUSES};pending=[];complete=[];unreviewed=[]
    for rid,record in records.items():
        effective=effective_status[rid]
        counts[effective]+=1
        if record.get('reviewed') is not True:unreviewed.append(rid)
        if record['status'] in COMPLETE and record.get('reviewed') is True:complete.append(rid)
        else:pending.append(rid)
    missing=sorted(set(candidates)-covered)
    coverage={'total':len(records),**counts,'unreviewed_candidate_ids':missing,
        'unreviewed_requirement_ids':unreviewed,'pending_requirement_ids':pending,
        'completed_requirement_ids':complete,'by_material':{},'needs_contract_search_ids':needs_search,
        'contract_searches':searches,'effective_statuses':effective_status}
    for m in task['materials']:
        mid=m['material_id']
        coverage['by_material'][mid]={'pending_requirement_ids':[rid for rid in pending if records[rid]['material_id']==mid],
            'unreviewed_candidate_ids':[cid for cid in missing if candidates[cid]['material_id']==mid]}
    return {'records':records,'by_slot':dict(by_slot),'coverage':coverage,'sources':sources,
            'candidates':candidates,'covered_candidates':covered,'slots':slots,'decisions':review.get('decisions',[])}


def validate_operation_requirements(state,record,mid,sid):
    ids=record.get('requirement_ids')
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):
        raise ValueError('正文修改须关联本槽原子需求requirement_ids')
    allowed=set()
    for rid in ids:
        requirement=state['records'].get(rid)
        if not requirement or (requirement['material_id'],requirement['slot_id'])!=(mid,sid):
            raise ValueError('正文修改引用了其他槽位或不存在的原子需求')
        if requirement['status'] not in COMPLETE or requirement.get('reviewed') is not True:
            raise ValueError('待核原子需求不能授权写入目标产品事实')
        allowed |= _refs(requirement['evidence'])
    references=_refs(record.get('evidence',[]))
    if not references or not references.issubset(allowed):
        raise ValueError('正文修改依据必须来自所关联且已核实的原子需求')


def bind_slot_requirements(state,coverage,dispositions):
    """Bind complete discovery and rendered components to actual final slot text."""
    for mid,item in coverage.items():
        for sid in item['required_slot_ids']:
            key=(mid,sid);record=dispositions.get(key)
            if record is None:continue
            ids=record.get('requirement_ids')
            actual=state['by_slot'].get(key,[])
            if not isinstance(ids,list) or len(ids)!=len(set(ids)) or set(ids)!=set(actual):
                raise ValueError('槽位requirement_ids必须准确覆盖该槽全部原子需求')
            missing=[cid for cid,c in state['candidates'].items() if (c['material_id'],c['slot_id'])==key
                     and cid not in state['covered_candidates']]
            discovery=record.get('discovery_reviewed') is True and isinstance(record.get('discovery_reason'),str) and record['discovery_reason'].strip()
            if missing or not discovery or (record['action']!='retain_static' and not actual):
                if sid not in item['unreviewed_slot_ids']:item['unreviewed_slot_ids'].append(sid)
            pending=False
            for rid in actual:
                req=state['records'][rid]
                if req['status'] not in COMPLETE or req.get('reviewed') is not True:
                    pending=True;continue
                if record['action']=='pending_placeholder':pending=True;continue
                if req['status']=='not_applicable' and req['rendered_text']:
                    if not re.search(r'不适用|不涉及|不设(?:置)?|未设置|不开放|无开放期|封闭(?:式|运作)|不收取|免收',req['rendered_text']):
                        raise ValueError('不适用需求的非空展示文字须明确表达不适用、无该安排或相应否定状态')
                if req['status']=='not_applicable' and not req['rendered_text'] and not (record['action']=='not_applicable'
                                                            and record['final_text'] in {'','不适用'}):
                    # Empty rendered_text is an omission, not proof of removal.
                    # Mixed slots must name the old fragment and bind its real
                    # deletion/replacement to this very inapplicable requirement.
                    removed=req.get('removed_text')
                    original=state['slots'][key]['text']
                    if (not isinstance(removed,str) or not removed.strip() or original.count(removed)!=1
                            or removed not in req['template_quote'] or removed in record['final_text']):
                        raise ValueError('不适用子需求须用removed_text标明已从最终正文移除的唯一模板原文片段')
                    linked=[d for d in state['decisions'] if d.get('material_id')==mid and d.get('slot_id')==sid
                            and d.get('kind')=='replace_text' and rid in d.get('requirement_ids',[])
                            and removed in d.get('before','') and removed not in d.get('after','')]
                    if not linked:
                        raise ValueError('不适用子需求的removed_text须关联实际移除该片段的正文替换决定')
                if req['rendered_text'] and req['rendered_text'] not in record['final_text']:
                    raise ValueError('原子需求的渲染文字未出现在最终正文')
            if pending and sid not in item['pending_slot_ids']:item['pending_slot_ids'].append(sid)
            if sid in item['pending_slot_ids'] or sid in item['unreviewed_slot_ids']:
                if sid in item['completed_slot_ids']:item['completed_slot_ids'].remove(sid)
    return state['coverage']
