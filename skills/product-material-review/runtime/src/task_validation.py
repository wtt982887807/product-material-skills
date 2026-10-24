from pathlib import Path
from copy import deepcopy
from zipfile import ZipFile, BadZipFile
from lxml import etree
from material_task import file_hash, validate_review, ReviewError, source_version_matches


def validate_task_outputs(task,result,check_deliveries=True,material_id=None):
    from material_writer import validate_updated
    issues=[]
    if result.get('task_id')!=task['task_id']: issues.append('输出结果不属于本任务')
    materials={m['material_id']:m for m in task['materials']}
    if material_id is not None and material_id not in materials:
        issues.append('局部复核物料不属于本任务')
    try:
        checked=validate_review(task,result.get('applied_review',{}))
    except ReviewError as exc:
        checked={'operations':{},'template_coverage':{},'resolved_checks':[], 'requirement_coverage':{},'contract_rechecks':{}}
        issues.append(f'审阅决定无效：{exc}')
    sources=[task['contract']['source']]+[m['source'] for m in task['materials']]
    for s in sources:
        if not source_version_matches(s): issues.append(f"来源或转换副本变化：{s['path']}")
    seen=set()
    for r in result['results']:
        mid=r['material_id']
        if mid not in materials or mid in seen:
            issues.append(f'未知或重复物料：{mid}'); continue
        seen.add(mid)
        # A failed edit in another material must not block this material's
        # export. Task identity, review, source versions and record coverage
        # remain global; acceptance always uses the default full-batch scope.
        if material_id is not None and mid!=material_id: continue
        output=Path(r['output'])
        if not output.exists() or file_hash(output)!=r['output_sha256']: issues.append(f'输出变化或不存在：{output}')
        if not r['verification'].get('passed'): issues.append(f'修改未通过：{mid}')
        if output.exists() and mid in checked['operations']:
            fresh=validate_updated(materials[mid]['source']['path'],output,checked['operations'][mid])
            if not fresh.get('passed'): issues.extend(f"{mid}重新验证：{x}" for x in fresh.get('issues',['修改不匹配']))
        if output.exists() and mid in checked['template_coverage']:
            from material_writer import _parse, _text, NS
            kind=Path(materials[mid]['source']['path']).suffix.lower().lstrip('.')
            try:
                with ZipFile(output) as package:
                    for sid,expected in checked['template_coverage'][mid]['final_texts'].items():
                        slot=next(s for s in materials[mid]['slots'] if s['slot_id']==sid)
                        root=_parse(package.read(slot['part']),slot['part'])
                        namespaces=dict(NS); namespaces.update({k:v for k,v in root.nsmap.items() if k})
                        nodes=root.xpath(slot['xpath'],namespaces=namespaces)
                        if len(nodes)!=1 or _text(nodes[0],kind)!=expected:
                            issues.append(f'{mid}/{sid}最终可见正文与审阅处置不一致')
            except (KeyError,ValueError,OSError,BadZipFile,etree.XPathError) as exc:
                issues.append(f'{mid}正文覆盖重新验证失败：{exc}')
    if seen!=set(materials): issues.append('有上传物料未生成结果记录')
    delivery_check={'passed':True,'issues':[],'blocked_material_ids':[]}
    if check_deliveries and task.get('delivery_required'):
        from material_delivery import validate_deliveries
        delivery_check=validate_deliveries(task,result)
        issues.extend(delivery_check['issues'])
    return {'passed':not issues,'issues':issues,'material_count':len(seen),'material_scope':material_id,
            'pending_review_count':sum(c.get('severity')!='info' and c['check_id'] not in checked['resolved_checks']
                                       for c in task.get('checks',[])),
            'template_coverage':checked['template_coverage'],'delivery_validation':delivery_check,
            'requirement_coverage':checked['requirement_coverage'],'contract_rechecks':checked['contract_rechecks'],
            'visual_status':{r['material_id']:r.get('visual_status','not_checked') for r in result['results']},
            'scope':'来源及输出完整性；不替代视觉验收和待核项处理'}


def record_acceptance(task,result,qa):
    """Record explicit native/visual review against exact output versions."""
    fresh=validate_task_outputs(task,result)
    if not fresh['passed']: raise ReviewError('输出复核未通过，不能登记验收：'+str(fresh['issues']))
    if qa.get('task_id')!=task['task_id']: raise ReviewError('验收记录不属于本任务')
    records=qa.get('materials',[])
    ids=[r.get('material_id') for r in records]
    if len(ids)!=len(set(ids)) or set(ids)!={r['material_id'] for r in result['results']}:
        raise ReviewError('验收须覆盖全部物料且不得重复')
    by_id={r['material_id']:r for r in records}
    accepted=deepcopy(result)
    reviewed=validate_review(task,result['applied_review'])
    accepted['resolved_checks']=reviewed['resolved_checks']
    accepted['template_coverage']=reviewed['template_coverage']
    accepted['requirement_coverage']=reviewed['requirement_coverage']
    accepted['contract_rechecks']=reviewed['contract_rechecks']
    for r in accepted['results']:
        check=by_id[r['material_id']]
        if check.get('output_sha256')!=r['output_sha256']: raise ReviewError('验收输出版本不一致')
        if not check.get('reviewer') or not check.get('notes'): raise ReviewError('验收须有审阅者和检查说明')
        r['native_opened']=check.get('native_opened') is True
        r['visual_status']='passed' if check.get('visual_reviewed') is True else 'not_checked'
        r['acceptance']=deepcopy(check)
        r['pending_check_ids']=[c['check_id'] for c in task.get('checks',[]) if c.get('severity')!='info'
                                and c.get('material_id') in (None,r['material_id'])
                                and c['check_id'] not in reviewed['resolved_checks']]
        coverage=reviewed['template_coverage'].get(r['material_id'],{})
        r['pending_content_slot_ids']=coverage.get('unreviewed_slot_ids',[])+coverage.get('pending_slot_ids',[])
    remaining=[c for c in task.get('checks',[]) if c.get('severity')!='info' and c['check_id'] not in accepted['resolved_checks']]
    accepted['all_updates_complete']=not remaining and all(r['native_opened'] and r['visual_status']=='passed'
                                                          and not r['pending_content_slot_ids'] for r in accepted['results'])
    if task.get('delivery_required'):
        delivery_qa=qa.get('deliveries',[])
        if not isinstance(delivery_qa,list):raise ReviewError('交付验收记录须为数组')
        ready={d['material_id']:d for d in accepted['deliveries'] if d.get('status')=='ready'}
        ids=[d.get('material_id') for d in delivery_qa]
        if len(ids)!=len(set(ids)) or not set(ids).issubset(ready):
            raise ReviewError('交付验收含重复、未知或受阻物料')
        final_checks={d['material_id']:d for d in delivery_qa}
        for delivery in accepted['deliveries']:
            delivery['native_opened']=False
            delivery['visual_status']='not_checked'
            check=final_checks.get(delivery['material_id'])
            if check:
                if check.get('output_sha256')!=delivery['output_sha256']:
                    raise ReviewError('交付验收输出版本不一致')
                if not check.get('reviewer') or not check.get('notes'):
                    raise ReviewError('交付验收须有审阅者和检查说明')
                delivery['native_opened']=check.get('native_opened') is True
                delivery['visual_status']='passed' if check.get('visual_reviewed') is True else 'not_checked'
                delivery['acceptance']=deepcopy(check)
        accepted['all_updates_complete']=accepted['all_updates_complete'] and all(
            d.get('status')=='ready' and d['native_opened'] and d['visual_status']=='passed'
            and d.get('content_status')!='pending_draft' for d in accepted['deliveries'])
    accepted['acceptance_note']='验收声明绑定具体输出哈希；待核项全部结案且原生打开及视觉检查通过后才可全部完成。'
    return accepted
