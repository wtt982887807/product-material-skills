"""Create and verify final material files separately from complete internal drafts."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import shutil

from material_task import ReviewError, file_hash, validate_review, source_version_matches

KINDS = {'ppt', 'poster', 'onepager'}


def retained_requirement_ids(checked, material_id, slot_ids=None):
    return sorted(rid for rid, source in checked.get('template_retentions', {}).items()
                  if source['material_id'] == material_id and (slot_ids is None or source['slot_id'] in slot_ids))


def content_status(pending, retained_ids):
    return 'pending_draft' if pending else ('content_reviewed_with_template_exceptions' if retained_ids else 'content_reviewed')


def _kind(material, explicit=None):
    if explicit is not None:
        if explicit not in KINDS:
            raise ReviewError('交付类型须为ppt、poster或onepager')
        return explicit
    source = Path(material['source'].get('original_path', material['source']['path']))
    name = source.stem.casefold()
    if '海报' in name or 'poster' in name:
        return 'poster'
    if '一页通' in name or 'factsheet' in name or 'onepager' in name:
        return 'onepager'
    return 'ppt' if source.suffix.lower() == '.pptx' else 'onepager'


def delivery_name(material, kind=None):
    source = Path(material['source'].get('original_path', material['source']['path']))
    selected = _kind(material, kind)
    if source.suffix.lower() == '.pptx' and selected == 'ppt':
        return source.stem + '产品要素' + source.suffix
    return source.name


def _plans(task, result):
    plans = result.get('applied_review', {}).get('delivery_plan', {})
    if not isinstance(plans, dict):
        raise ReviewError('delivery_plan须为按material_id索引的对象')
    ids = {m['material_id'] for m in task['materials']}
    if set(plans) - ids:
        raise ReviewError('delivery_plan包含未知物料')
    for plan in plans.values():
        if not isinstance(plan, dict):
            raise ReviewError('交付计划须为对象')
        if 'kind' in plan and plan['kind'] not in KINDS:
            raise ReviewError('交付类型须为ppt、poster或onepager')
    return plans


def _destinations(task, plans, output_dir):
    folder = Path(output_dir).resolve() / 'delivery'
    names = {m['material_id']: delivery_name(m, plans.get(m['material_id'], {}).get('kind'))
             for m in task['materials']}
    counts = Counter(name.casefold() for name in names.values())
    paths = {}
    for mid, name in names.items():
        if not isinstance(mid, str) or not mid or Path(mid).name != mid or mid in {'.', '..'}:
            raise ReviewError('物料ID不能用于交付子目录')
        paths[mid] = folder / mid / name if counts[name.casefold()] > 1 else folder / name
    return paths


def _protected_paths(task, result):
    sources = [task['contract']['source']] + [m['source'] for m in task['materials']]
    paths = {Path(source[key]).resolve() for source in sources
             for key in ('path', 'converted_path', 'original_path') if source.get(key)}
    paths.update(Path(r['output']).resolve() for r in result.get('results', []) if r.get('output'))
    return paths


def _verify_internal(material, record, operations):
    from material_writer import validate_updated
    if not record or record.get('status') == 'edit_blocked_source_copy' or not record.get('verification', {}).get('passed'):
        raise ReviewError('内部修改未通过，不交付源副本')
    if record.get('status') == 'pending_review_source_copy':
        raise ReviewError('模板尚未审阅修改，不交付未处理的源副本')
    source = Path(material['source']['path'])
    output = Path(record['output'])
    if not source_version_matches(material['source']):
        raise ReviewError('物料来源已变化')
    if not output.is_file() or file_hash(output) != record.get('output_sha256'):
        raise ReviewError('内部输出已变化或不存在')
    verified = validate_updated(source, output, operations)
    if not verified.get('passed'):
        raise ReviewError('内部输出重新验证失败：' + str(verified.get('issues', [])))


def build_deliveries(task, result, output_dir):
    """Always return one ready/blocked record per material; never fall back to a full deck."""
    records = {r['material_id']: r for r in result.get('results', [])}
    setup_error = None
    try:
        plans = _plans(task, result)
        destinations = _destinations(task, plans, output_dir)
        checked = validate_review(task, result['applied_review'])
    except (ValueError, KeyError, TypeError) as exc:
        plans, destinations, checked = {}, {}, {}
        setup_error = str(exc)
    protected = _protected_paths(task, result)
    # Inspect all intended outputs before creating any final file.
    conflicts = {}
    for mid, target in destinations.items():
        if target.resolve() in protected:
            conflicts[mid] = '禁止覆盖合同、模板或内部稿'
        elif target.exists() or target.with_suffix('.delivery.json').exists():
            conflicts[mid] = '交付输出已存在，请使用新输出目录'
    deliveries = []
    for material in task['materials']:
        mid = material['material_id']
        plan = plans.get(mid, {})
        kind = _kind(material, plan.get('kind'))
        item = {'material_id': mid, 'kind': kind, 'status': 'blocked', 'pages': [],
                'content_status': 'blocked', 'pending_slot_ids': [],
                'native_opened': False, 'visual_status': 'not_checked'}
        target = destinations.get(mid)
        created = False
        try:
            if setup_error or mid in conflicts:
                raise ReviewError(setup_error or conflicts[mid])
            record = records.get(mid)
            _verify_internal(material, record, checked['operations'][mid])
            if Path(material['source']['path']).suffix.lower() == '.pptx':
                from material_cli import deliver_ppt
                exported = deliver_ppt(task, result, mid, target,
                                       plan.get('pages'), plan.get('review_reason'))
                created = True
                if not exported.get('valid'):
                    raise ReviewError('PPT产品要素页导出未通过')
                item.update(pages=exported['pages'], content_status=exported['content_status'],
                            pending_slot_ids=exported.get('pending_slot_ids', []),
                            template_retained_requirement_ids=exported.get('template_retained_requirement_ids', []),
                            manifest=exported.get('manifest'))
            elif Path(material['source']['path']).suffix.lower() == '.docx':
                if plan.get('pages') is not None:
                    raise ReviewError('Word交付不支持PPT页码选择')
                target.parent.mkdir(parents=True, exist_ok=True)
                with Path(record['output']).open('rb') as source, target.open('xb') as destination:
                    created = True
                    shutil.copyfileobj(source, destination)
                if file_hash(target) != record['output_sha256']:
                    raise ReviewError('Word交付与内部审阅稿不一致')
                coverage = checked['template_coverage'].get(mid, {})
                pending_slots = sorted(set(coverage.get('unreviewed_slot_ids', []) + coverage.get('pending_slot_ids', [])))
                pending = pending_slots or record.get('pending_check_ids') or record.get('pending_content_slot_ids')
                retained_ids = retained_requirement_ids(checked, mid)
                item.update(content_status=content_status(pending, retained_ids),
                            template_retained_requirement_ids=retained_ids,
                            pending_slot_ids=pending_slots)
            else:
                raise ReviewError('交付仅支持DOCX和PPTX')
            item.update(status='ready', output=str(target.resolve()), output_sha256=file_hash(target))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            # Only a path this invocation created can be removed on failure.
            if created and target:
                target.unlink(missing_ok=True)
                target.with_suffix('.delivery.json').unlink(missing_ok=True)
            item['reason'] = str(exc)
        deliveries.append(item)
    return deliveries


def validate_deliveries(task, result):
    """Rebind delivery names, pages and package bytes to the reviewed internal outputs."""
    required = bool(task.get('delivery_required'))
    items = result.get('deliveries')
    if not required and items is None:
        return {'passed': True, 'issues': [], 'blocked_material_ids': []}
    issues, blocked = [], []
    materials = {m['material_id']: m for m in task['materials']}
    records = {r['material_id']: r for r in result.get('results', [])}
    if not isinstance(items, list):
        return {'passed': False, 'issues': ['缺少最终交付清单'], 'blocked_material_ids': []}
    try:
        plans = _plans(task, result)
        checked = validate_review(task, result['applied_review'])
    except (ValueError, KeyError, TypeError) as exc:
        return {'passed': False, 'issues': ['交付依据无效：' + str(exc)], 'blocked_material_ids': []}
    protected = _protected_paths(task, result)
    seen, output_paths = set(), set()
    for item in items:
        if not isinstance(item, dict):
            issues.append('交付记录须为对象'); continue
        mid = item.get('material_id')
        if mid not in materials or mid in seen:
            issues.append('交付记录物料未知或重复：' + str(mid)); continue
        seen.add(mid)
        material, plan = materials[mid], plans.get(mid, {})
        if item.get('kind') != _kind(material, plan.get('kind')):
            issues.append(mid + '交付类型与模板或审阅计划不一致')
        if item.get('status') == 'blocked':
            blocked.append(mid)
            if not item.get('reason') or item.get('output') or item.get('output_sha256'):
                issues.append(mid + '阻断交付记录不得含最终文件且须有原因')
            continue
        if item.get('status') != 'ready':
            issues.append(mid + '交付状态无效'); continue
        try:
            output = Path(item['output']).resolve()
            if output in protected or output in output_paths:
                raise ReviewError('交付覆盖来源、内部稿或与另一交付冲突')
            output_paths.add(output)
            if output.name != delivery_name(material, plan.get('kind')):
                raise ReviewError('交付文件名不符合上传模板命名规则')
            if not output.is_file() or file_hash(output) != item.get('output_sha256'):
                raise ReviewError('交付文件已变化或不存在')
            record = records.get(mid)
            _verify_internal(material, record, checked['operations'][mid])
            if output.suffix.lower() == '.pptx':
                from material_cli import validate_ppt_pages
                from ppt_delivery import validate_slide_delivery
                expected = validate_ppt_pages(task, result, mid, plan.get('pages'), plan.get('review_reason'))
                if item.get('pages') != expected['pages']:
                    raise ReviewError('交付页码与重新核实的产品要素范围不一致')
                if item.get('content_status') != expected['content_status'] or item.get('pending_slot_ids') != expected['pending_slot_ids']:
                    raise ReviewError('交付待核状态与重新核实结果不一致')
                if item.get('template_retained_requirement_ids',[]) != expected.get('template_retained_requirement_ids',[]):
                    raise ReviewError('PPT交付模板保留清单不正确')
                verified = validate_slide_delivery(record['output'], output, expected['pages'])
                if not verified['valid']:
                    raise ReviewError('PPT实际交付子集不一致：' + str(verified['errors']))
            else:
                if output.suffix.lower() != '.docx' or item.get('pages') != []:
                    raise ReviewError('Word交付格式或页码记录不正确')
                if file_hash(output) != record['output_sha256']:
                    raise ReviewError('Word交付与内部稿不一致')
                coverage = checked['template_coverage'].get(mid, {})
                pending_slots = sorted(set(coverage.get('unreviewed_slot_ids', []) + coverage.get('pending_slot_ids', [])))
                pending = pending_slots or record.get('pending_check_ids') or record.get('pending_content_slot_ids')
                retained_ids = retained_requirement_ids(checked, mid)
                if item.get('template_retained_requirement_ids',[]) != retained_ids:
                    raise ReviewError('交付模板保留清单与审阅记录不一致')
                if item.get('content_status') != content_status(pending, retained_ids) or item.get('pending_slot_ids') != pending_slots:
                    raise ReviewError('Word交付待核状态不正确')
        except (ValueError, OSError, KeyError, TypeError) as exc:
            issues.append(mid + '：' + str(exc))
    if seen != set(materials):
        issues.append('最终交付清单未覆盖全部上传物料')
    return {'passed': not issues, 'issues': issues, 'blocked_material_ids': blocked}
