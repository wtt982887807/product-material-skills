"""Create and verify final material files separately from complete internal drafts."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import shutil
import unicodedata

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


LEGAL_END = re.compile(r'(?:集合资产管理计划|单一资产管理计划|资产管理计划|集合资金信托计划|'
                       r'集合资产信托计划|集合信托计划|资金信托计划|信托计划|资产管理信托|证券投资基金)$')
METADATA_WORDS = re.compile(r'一页通|产品要素|营销垫板|业绩海报|海报|长图|PPT|factsheet|onepager|poster|'
                            r'集团|对客|对内|对外|机构|内部|零售|家族|客户|新发|持营|更新|定稿|清洁|'
                            r'工行|建行|农行|中行|交行|招行|邮储|渣打|平安|徽商|晋商|南银|'
                            r'版|稿', re.I)
DATE_TOKEN = r'(?:20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])?|\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))'
COPY_TOKEN = r'[（(](?:副本)?\d+[)）]'
VERSION_TOKEN = r'[vV]\d+(?:\.\d+)*'
REGION_TOKEN = re.compile(r'[（(](?:北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|'
                          r'江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|'
                          r'内蒙古|广西|西藏|宁夏|新疆|香港|澳门)(?:省|市|地区|自治区)?[)）]')
TRAILING_METADATA = re.compile(r'(?<!\d)((?:[ _\-]*(?:'+DATE_TOKEN+'|'+COPY_TOKEN+'|'+VERSION_TOKEN+'|'+REGION_TOKEN.pattern+r'))+)$')


def delivery_target(task):
    """The contract's canonical identity wins over display aliases or filenames."""
    contract = task.get('contract', {})
    return contract['product'] if 'product' in contract else task.get('target_product')


def _identity_name(identity):
    return identity if isinstance(identity, str) else identity.get('name') if isinstance(identity, dict) else None


def _short_name(name):
    return LEGAL_END.sub('', name).strip()


def _normalized_offsets(text):
    normalized, offsets = [], []
    for index, character in enumerate(text):
        for normalized_character in unicodedata.normalize('NFKC', character).casefold():
            if normalized_character.isspace() or normalized_character in '·•()':
                continue
            normalized.append(normalized_character)
            offsets.append(index)
    return ''.join(normalized), offsets


def _normal(text):
    return _normalized_offsets(text)[0]


def _metadata_only(text):
    """Conservative filename scaffolding, never an arbitrary product title."""
    text = re.sub(r'[\u4e00-\u9fff]{2,12}银行', '', text)
    text = METADATA_WORDS.sub('', text)
    text = re.sub(VERSION_TOKEN, '', text)
    text = re.sub(COPY_TOKEN, '', text)
    text = REGION_TOKEN.sub('', text)
    text = re.sub(r'\d{4,8}(?:更新)?', '', text)
    return not re.sub(r'[\s【】\[\]（）()_.\-—,:：/]', '', text)


def _safe_brackets(stem):
    return [match.group(0) for match in re.finditer(r'【[^【】]+】', stem)
            if _metadata_only(match.group(0))]


EDGE_TOKEN = re.compile(r'(?:'+VERSION_TOKEN+'|'+DATE_TOKEN+'|'+COPY_TOKEN+'|'+REGION_TOKEN.pattern+'|'+
                        METADATA_WORDS.pattern+r'|[\s_\-—.]+)', re.I)


def _metadata_prefix_length(text):
    """Consume only known metadata tokens, stopping at the first unknown word."""
    offset = 0
    while offset < len(text):
        # 平安 can label a bank channel, but not when it starts a trust or
        # securities issuer name. Keep that whole unknown product for removal.
        if re.match(r'平安(?:信托|证券|资管)', text[offset:]):
            break
        if text[offset] == '【':
            end = text.find('】', offset + 1)
            if end < 0 or not _metadata_only(text[offset:end+1]):
                break
            offset = end + 1
            continue
        match = EDGE_TOKEN.match(text, offset)
        if not match:
            break
        offset = match.end()
    return offset


def _fallback_edges(stem, target_short, selected):
    prefix_length = _metadata_prefix_length(stem)
    if prefix_length == len(stem):
        return None  # Preserve the established generic-template naming below.
    prefix, remaining = stem[:prefix_length], stem[prefix_length:]
    suffix_at = len(remaining)
    for index in range(len(remaining)):
        # A recognized date cannot begin in the middle of a longer number.
        if index and remaining[index-1].isdigit() and remaining[index].isdigit():
            continue
        if _metadata_prefix_length(remaining[index:]) == len(remaining)-index:
            suffix_at = index
            break
    middle, suffix = remaining[:suffix_at], remaining[suffix_at:]
    if not middle or not re.search(r'[^\s_\-—.]', prefix+suffix):
        return None
    # Bracketed metadata in the middle can still be retained, but no unknown
    # piece surrounding it is copied into the new product title.
    prefix += ''.join(_safe_brackets(middle))
    if selected == 'ppt':
        prefix, suffix = prefix.replace('产品要素', ''), suffix.replace('产品要素', '')
    type_word = {'poster':'海报', 'onepager':'一页通', 'ppt':''}[selected]
    if type_word and not re.search(r'一页通|海报|长图|factsheet|onepager|poster', prefix+suffix, re.I):
        suffix += type_word
    return prefix + target_short + suffix


def _fallback_stem(stem, target_short, selected):
    edged = _fallback_edges(stem, target_short, selected)
    if edged is not None:
        return edged
    prefix = re.match(r'^\s*('+VERSION_TOKEN+r')', stem)
    brackets = _safe_brackets(stem)
    # A date inside a bracket belongs only to that bracket, never to the tail.
    outside = re.sub(r'【[^【】]*】', '', stem).replace('产品要素', '')
    if prefix:
        outside = re.sub(r'^\s*'+re.escape(prefix.group(1)), '', outside, count=1)
    tail = TRAILING_METADATA.search(outside)
    suffix = tail.group(1) if tail else ''
    metadata = (prefix.group(1) if prefix else '') + ''.join(brackets)
    type_word = {'poster':'海报', 'onepager':'一页通', 'ppt':''}[selected]
    # Preserve an identifiable original type even when the old product portion
    # is unknown. Generic PPT filenames only need the 产品要素 suffix below.
    if type_word and not re.search(r'一页通|海报|长图|factsheet|onepager|poster', metadata, re.I):
        suffix += type_word
    if selected == 'ppt':
        metadata = metadata.replace('产品要素', '')
    return metadata + target_short + suffix


def _source_variants(material):
    identity = material.get('template_source_product')
    if not identity and material.get('material_mode') != 'template':
        identity = material.get('target_product')
    name = _identity_name(identity)
    if not isinstance(name, str) or not name.strip():
        return []
    aliases = identity.get('aliases', []) if isinstance(identity, dict) else []
    if isinstance(aliases, str):
        aliases = [aliases]
    variants = {}
    for value in [name, *aliases]:
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        # A legal full name may end in a qualifier such as （QDII）. Keep that
        # classification; do not guess a shortened form by dropping qualifiers.
        full = bool(LEGAL_END.search(value) or LEGAL_END.search(re.sub(r'[（(][^()（）]+[)）]$', '', value)))
        variants.setdefault(_normal(value), (value, full))
        shortened = _short_name(value)
        if shortened and shortened != value:
            variants.setdefault(_normal(shortened), (shortened, False))
    return sorted(variants.values(), key=lambda item: len(_normal(item[0])), reverse=True)


def _matched_name(stem, variants):
    normalized, offsets = _normalized_offsets(stem)
    for source_name, full in variants:
        needle = _normal(source_name)
        if not needle:
            continue
        for found in re.finditer(re.escape(needle), normalized):
            start, end = offsets[found.start()], offsets[found.end()-1]+1
            if source_name.endswith((')', '）')) and stem[end:end+1] in {')', '）'}:
                end += 1
            before, after = stem[:start], stem[end:]
            # Do not replace a numeric subspan or erase a different issue suffix.
            if ((needle[0].isdigit() and found.start() and normalized[found.start()-1].isdigit())
                    or (needle[-1].isdigit() and found.end()<len(normalized) and normalized[found.end()].isdigit())
                    or re.match(r'\s*[0-9０-９一二三四五六七八九十]+[号期]', after)):
                continue
            # One real trust filename repeats its issuer before the actual
            # product span. Discard only an exact prefix of that source name.
            issuer = before.strip()
            duplicated_issuer = (bool(re.fullmatch(r'[\u4e00-\u9fff]{2,16}(?:信托|银行|证券|资管)', issuer))
                                 and _normal(source_name).startswith(_normal(issuer)))
            if duplicated_issuer:
                before = ''
            if _metadata_only(before) and _metadata_only(after):
                return source_name, full, before, after, duplicated_issuer
    return None


def _filename_safe(stem, suffix):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', stem).strip(' .')
    if not stem:
        raise ReviewError('合同产品身份不能生成有效文件名')
    if re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', stem, re.I):
        stem = '_' + stem
    return stem + suffix


def delivery_naming(material, kind=None, *, target_product=None):
    source = Path(material['source'].get('original_path', material['source']['path']))
    selected = _kind(material, kind)
    target = _identity_name(target_product if target_product is not None else material.get('target_product'))
    if not isinstance(target, str) or not target.strip():
        raise ReviewError('交付命名缺少合同确定的目标产品身份，不能从模板文件名推断')
    target = target.strip()
    match = _matched_name(source.stem, _source_variants(material))
    if match:
        old_name, full, prefix, suffix, duplicate = match
        if source.suffix.lower() == '.pptx' and selected == 'ppt':
            prefix, suffix = prefix.replace('产品要素', ''), suffix.replace('产品要素', '')
        stem = prefix + (target if full else _short_name(target)) + suffix
        method = 'matched_full_name' if full else 'matched_short_name'
    else:
        old_name, duplicate = None, False
        stem = _fallback_stem(source.stem, _short_name(target), selected)
        method = 'rebuilt_from_contract_and_safe_metadata'
    if source.suffix.lower() == '.pptx' and selected == 'ppt':
        stem += '产品要素'
    return {'name':_filename_safe(stem, source.suffix), 'method':method,
            'source_template_name':source.name, 'matched_source_name':old_name,
            'target_product_name':target, 'removed_duplicate_issuer':duplicate}


def delivery_name(material, kind=None, *, target_product=None):
    return delivery_naming(material, kind, target_product=target_product)['name']


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
    names = {m['material_id']: delivery_name(m, plans.get(m['material_id'], {}).get('kind'), target_product=delivery_target(task))
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
            item['naming_metadata'] = delivery_naming(material, kind, target_product=delivery_target(task))
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
            naming = delivery_naming(material, plan.get('kind'), target_product=delivery_target(task))
            if output.name != naming['name']:
                raise ReviewError('交付文件名未按合同产品身份及模板命名格式生成')
            if item.get('naming_metadata') != naming:
                raise ReviewError('交付命名记录与合同身份和模板来源不一致')
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
