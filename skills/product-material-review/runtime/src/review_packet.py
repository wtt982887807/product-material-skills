"""Bounded, lossless reads of review work; full task evidence remains on disk."""
from collections import Counter, defaultdict
from copy import deepcopy


def _pick(value, names):
    return {name:deepcopy(value[name]) for name in names if name in value}


def coverage_summary(coverage):
    summary=_pick(coverage, ('parse_status','source_block_count','retained_block_count',
        'retained_source_text_ratio','retained_character_count','all_source_content_parsed',
        'structured_extraction_complete','verified_fact_count','unreadable_pages','block_roles'))
    summary['chapter_count']=len(coverage.get('chapters',[]))
    summary['field_state_counts']=dict(Counter(coverage.get('field_states',{}).values()))
    attachments=coverage.get('attachments',{})
    summary['attachments']={key:(len(value) if isinstance(value,list) else deepcopy(value))
                            for key,value in attachments.items()}
    summary['content_unreadable']=bool(coverage.get('unreadable_pages')) or coverage.get('parse_status') in {
        'ocr_required','parse_error','legacy_pending','unsupported'}
    return summary


def _warning_counts(warnings):
    return dict(Counter(w.get('code','unspecified') for w in warnings if isinstance(w,dict)))


def _block(block):
    return _pick(block, ('block_id','text','location','section_path','kind','role','quality','source_sha256'))


def _page(items, offset, limit):
    end=min(offset+limit,len(items))
    return items[offset:end], {'offset':offset,'limit':limit,'returned_count':max(end-offset,0),
        'total':len(items),'next_offset':end if end<len(items) else None,
        'page_complete':end>=len(items)}


def _materials(task, identity):
    values=task.get('materials',[])
    if identity is None:return values
    selected=[m for m in values if m['material_id']==identity]
    if not selected:raise ValueError('material_id不属于本任务')
    return selected


def build_review_packet(task, section='overview', material_id=None, fields=None, offset=0, limit=40):
    """Exact text is paginated by records, never silently truncated by characters."""
    from material_cli import _require_fresh_task
    from material_task import template_required_slots
    _require_fresh_task(task)
    if section not in {'overview','contract','slots','facts'}:raise ValueError('未知review-packet section')
    if type(offset) is not int or offset<0:raise ValueError('offset须为非负整数')
    if type(limit) is not int or not 1<=limit<=200:raise ValueError('limit须为1至200的整数')
    if fields is not None and (not isinstance(fields,list) or any(not isinstance(f,str) or not f.strip() for f in fields)):
        raise ValueError('field筛选须为非空字段ID列表')
    selected=_materials(task,material_id)
    fields=set(fields or [])
    contract=task['contract']
    blocks=contract.get('blocks',[])
    block_index={b['block_id']:b for b in blocks}
    facts=[f for f in contract.get('facts',[]) if not fields or f.get('field_id') in fields]
    packet={'task_id':task['task_id'],'section':section,'source':deepcopy(contract['source']),
            'filters':{'material_id':material_id,'fields':sorted(fields)},
            'semantic_review_complete':False,
            'scope':'只读工作视图；分页结束不表示语义审阅完成。精确全文保留于task.json及原文件，检索未命中不证明合同未提。'}
    if section=='overview':
        packet.update(target_product=_pick(task['target_product'],('name','aliases','status')),
            material_mode=task.get('material_mode'),coverage=coverage_summary(contract.get('coverage',{})),
            editing_policy=task.get('editing_policy'),
            target_rule='合同决定目标产品；模板原产品不同不需确认，模板原话术和格式为基线，仅修改有依据的差异。',
            warning_counts=_warning_counts(contract.get('issues',[])),
            field_candidate_counts=dict(Counter(f.get('field_id') or '__unknown__' for f in facts)),
            materials=[])
        for material in selected:
            selection=material.get('template_selection',{})
            slots=template_required_slots(material)
            packet['materials'].append({'material_id':material['material_id'],
                'source':_pick(material['source'],('path','sha256','original_path','original_sha256')),
                'template_source_product':deepcopy(material.get('template_source_product')),
                'selection':_pick(selection,('status','method','reason','selected_region_ids','element_pages')),
                'selection_candidates':[_pick(c,('name','region_ids')) for c in selection.get('candidates',[])],
                'region_status_counts':dict(Counter(r.get('status','unknown') for r in material.get('regions',[]))),
                'delivery_slot_count':len(slots),'noneditable_slot_count':sum(not s.get('editable') for s in slots),
                'unclassified_slot_count':sum(not s.get('field_id') for s in slots)})
        return packet
    if section=='contract':
        source_ids={bid for fact in facts for bid in fact.get('source_block_ids',[]) if bid in block_index}
        selected_blocks=[b for b in blocks if not fields or b['block_id'] in source_ids]
        page,meta=_page(selected_blocks,offset,limit)
        packet.update(meta,blocks=[_block(b) for b in page])
        if fields:
            packet['unmapped_fact_ids']=[f['fact_id'] for f in facts if not f.get('source_block_ids')
                or any(bid not in block_index for bid in f.get('source_block_ids',[]))]
            packet['field_filter_note']='字段视图按真实原文块ID筛选；unmapped_fact_ids须读取facts视图的完整原文，不能判作缺项。'
        return packet
    if section=='slots':
        candidates=defaultdict(list)
        for candidate in task.get('requirement_candidates',[]):
            candidates[(candidate['material_id'],candidate['slot_id'])].append(candidate)
        items=[]
        for material in selected:
            for slot in template_required_slots(material):
                seeds=candidates[(material['material_id'],slot['slot_id'])]
                known_fields={slot.get('field_id'),*[c.get('field_id') for c in seeds]}
                # Unknown slots remain visible even during a field-filtered read.
                if fields and slot.get('field_id') and not fields.intersection(known_fields):continue
                item=_pick(slot,('slot_id','region_id','field_id','label','text','part','xpath','editable',
                    'product_status','location','page','content_role','shared_scope','inherited_context','delivery_required'))
                item['material_id']=material['material_id']
                item['requirement_candidates']=[_pick(c,('candidate_id','field_id','component','description',
                    'display_rule','status','discovery_method')) for c in seeds]
                items.append(item)
        page,meta=_page(items,offset,limit)
        packet.update(meta,slots=page,field_filter_note='字段筛选仍保留未分类槽位；新字段与不可编辑内容仍须核对。')
        return packet
    page,meta=_page(facts,offset,limit)
    packet.update(meta,facts=[],blocks=[])
    emitted=set()
    for fact in page:
        item=_pick(fact,('fact_id','field_id','name','status','value','extraction_level','normalization_status',
            'null_state','condition_sufficiency','units','share_classes','source_block_ids','source_locations',
            'source_sha256','cross_references','extraction_note'))
        references=fact.get('source_block_ids',[])
        missing=[bid for bid in references if bid not in block_index]
        exact='\n\n'.join(block_index[bid]['text'] for bid in references if bid in block_index)
        raw=fact.get('raw_text')
        if not references or missing or (raw and raw not in exact):
            item['raw_text']=deepcopy(raw)
            item['raw_text_note']='完整原文无法由本页真实块引用完全还原，保留原文。'
        if missing:item['unresolved_source_block_ids']=missing
        for bid in references:
            if bid in block_index and bid not in emitted:
                emitted.add(bid);packet['blocks'].append(_block(block_index[bid]))
        packet['facts'].append(item)
    return packet


def compact_search_report(report):
    """Keep actual hits and adjacent exact text; summarize repeated corpus metadata."""
    result={key:deepcopy(value) for key,value in report.items() if key not in {'coverage','warnings'}}
    result['compact']=True
    result['coverage']=coverage_summary(report.get('coverage',{}))
    result['warning_counts']=_warning_counts(report.get('warnings',[]))
    result['warning_count']=len(report.get('warnings',[]))
    result['coverage']['content_unreadable']=result['coverage']['content_unreadable'] or (
        report.get('status')=='source_unreadable' or 'unreadable_source_content' in result['warning_counts'])
    result['metadata_note']='coverage/warnings为计数摘要；详细风险及位置请读取task.json或不带--compact的搜索报告。命中与相邻正文保持完整。'
    return result


def batch_search_contract(task, groups, default_limit=20, compact=False):
    from material_cli import _require_fresh_task
    from contract_recheck import search_contract
    _require_fresh_task(task)
    if not isinstance(groups,list) or not groups:raise ValueError('batch须为非空分组数组')
    if type(default_limit) is not int or default_limit<1:raise ValueError('默认limit须为正整数')
    normalized=[];seen=set()
    for group in groups:
        if not isinstance(group,dict):raise ValueError('batch每组须为id/queries对象')
        identity=group.get('id');queries=group.get('queries');limit=group.get('limit',default_limit)
        if not isinstance(identity,str) or not identity.strip() or identity in seen:raise ValueError('batch id为空或重复')
        if not isinstance(queries,list) or not queries or any(not isinstance(q,str) or not q.strip() for q in queries):
            raise ValueError('batch queries须为非空检索词数组')
        if type(limit) is not int or limit<1:raise ValueError('每组limit须为正整数')
        seen.add(identity);normalized.append((identity,queries,limit))
    result={'task_id':task['task_id'],'batch':True,'group_count':len(normalized),'groups':[],'absence_proven':False}
    for identity,queries,limit in normalized:
        found=search_contract(task,queries,limit=limit)
        result['groups'].append({'id':identity,'result':compact_search_report(found) if compact else found})
    return result


def compact_review_report(report):
    """Report remaining identifiers without repeating complete template/check text."""
    by_material={}
    def entry(identity):
        return by_material.setdefault(identity or '__contract__',{'pending_check_ids':[],
            'unreviewed_slot_ids':[],'pending_slot_ids':[]})
    for check in report.get('pending_checks',[]):entry(check.get('material_id'))['pending_check_ids'].append(check['check_id'])
    for identity,coverage in report.get('template_coverage',{}).items():
        entry(identity).update({k:deepcopy(v) for k,v in coverage.items() if k.endswith('_ids')})
    requirements=report.get('requirement_coverage',{})
    for identity,coverage in requirements.get('by_material',{}).items():
        entry(identity).update({k:deepcopy(v) for k,v in coverage.items() if k.endswith('_ids')})
    result=_pick(report,('valid','task_id','semantic_review_complete','scope'))
    result.update(compact=True,pending_check_count=len(report.get('pending_checks',[])),
        pending_check_type_counts=dict(Counter(c.get('type','unknown') for c in report.get('pending_checks',[]))),
        pending_content_slot_count=sum(len(set(c.get('pending_slot_ids',[]))|set(c.get('unreviewed_slot_ids',[])))
                                       for c in report.get('template_coverage',{}).values()),
        unprocessed_by_material=by_material,
        requirement_counts={key:deepcopy(value) for key,value in requirements.items()
                            if isinstance(value,(int,bool)) or key.endswith('_counts')},
        contract_recheck_count=len(report.get('contract_rechecks',{})),
        contract_recheck_ids=list(report.get('contract_rechecks',{})))
    return result
