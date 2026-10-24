from pathlib import Path
from copy import deepcopy
from material_task import file_hash, validate_review, ReviewError, source_version_matches


def validate_task_outputs(task,result):
    from material_writer import validate_updated
    issues=[]
    if result.get('task_id')!=task['task_id']: issues.append('输出结果不属于本任务')
    materials={m['material_id']:m for m in task['materials']}
    try:
        checked=validate_review(task,result.get('applied_review',{}))
    except ReviewError as exc:
        checked={'operations':{}}
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
        output=Path(r['output'])
        if not output.exists() or file_hash(output)!=r['output_sha256']: issues.append(f'输出变化或不存在：{output}')
        if not r['verification'].get('passed'): issues.append(f'修改未通过：{mid}')
        if output.exists() and mid in checked['operations']:
            fresh=validate_updated(materials[mid]['source']['path'],output,checked['operations'][mid])
            if not fresh.get('passed'): issues.extend(f"{mid}重新验证：{x}" for x in fresh.get('issues',['修改不匹配']))
    if seen!=set(materials): issues.append('有上传物料未生成结果记录')
    return {'passed':not issues,'issues':issues,'material_count':len(seen),
            'pending_review_count':sum(len(r['pending_check_ids']) for r in result['results']),
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
    remaining=[c for c in task.get('checks',[]) if c.get('severity')!='info' and c['check_id'] not in accepted['resolved_checks']]
    accepted['all_updates_complete']=not remaining and all(r['native_opened'] and r['visual_status']=='passed' for r in accepted['results'])
    accepted['acceptance_note']='验收声明绑定具体输出哈希；待核项全部结案且原生打开及视觉检查通过后才可全部完成。'
    return accepted
