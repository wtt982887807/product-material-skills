"""Narrow user-authorized template retention, never a verified contract fact.

The reviewer decides whether the complete relevant contract context supplies a
value. This module binds that decision to current source text, required searches,
unchanged template text and an actual comment operation.
"""
from copy import deepcopy
import re

from contract_recheck import (
    _block_index, _contract, _digest, _source_blocks, _text,
    _validated_citation, _field_registry, _parameter_key, search_contract,
)

PERFORMANCE_FIELDS = frozenset({
    'performance_fee', 'performance_fee_hurdle', 'performance_fee_lower_hurdle',
    'performance_fee_upper_hurdle', 'performance_fee_lower_rate',
    'performance_fee_upper_rate', 'performance_fee_hurdle_unit',
    'performance_fee_accrual_dates', 'performance_fee_calculation',
})
ALLOWED_FIELDS = PERFORMANCE_FIELDS | {'distributor_risk_rating'}
COMMENT_TEXT = '合同未提，沿用模板'
PERFORMANCE_QUERIES = ('业绩报酬', '浮动管理费', '浮动服务费', '浮动信托管理费', '超额收益报酬')
DISTRIBUTOR_QUERIES = ('代销', '销售机构', '渠道', '风险等级', '风险评级')
PENDING_COMMENT_TEXT = '合同未提供该参数，保留模板值待确认'
IDENTITY_FIELDS = frozenset({'product_name', 'product_short_name'})


def _same_parameter(left, right):
    first, second = _parameter_key(left), _parameter_key(right)
    return first is None or second is None or first == second


def _query_extras(extras):
    if not isinstance(extras, (list, tuple)) or any(not isinstance(q, str) or not q.strip() for q in extras):
        raise ValueError('模板保留回查queries须为非空词语的数组')
    return [q.strip() for q in extras]


def _queries(task, field_id, extras):
    if field_id not in ALLOWED_FIELDS:
        raise ValueError('模板保留例外仅适用已定义的业绩报酬字段和代销机构销售评级')
    extras = _query_extras(extras)
    fixed = PERFORMANCE_QUERIES if field_id in PERFORMANCE_FIELDS else DISTRIBUTOR_QUERIES
    aliases = []
    for field in task.get('field_dictionary', []):
        if field.get('field_id') == field_id:
            aliases.extend([field.get('name', '')] + field.get('aliases', []))
    return list(dict.fromkeys([*fixed, *(q.strip() for q in aliases if isinstance(q, str) and q.strip()),
                               *(q.strip() for q in extras)]))


def _pending_queries(field_id, extras, fields):
    if field_id in IDENTITY_FIELDS:
        raise ValueError('产品名称身份必须按合同确定，不能保留模板身份待确认')
    field = fields.get(field_id)
    if field is None:
        raise ValueError('待确认字段须在字段字典或new_fields中定义')
    aliases = [field.get('name', '')] + field.get('aliases', [])
    fixed = (PERFORMANCE_QUERIES if field_id in PERFORMANCE_FIELDS else
             DISTRIBUTOR_QUERIES if field_id == 'distributor_risk_rating' else ())
    terms = [q.strip() for q in aliases if isinstance(q, str) and q.strip()]
    if not terms:
        raise ValueError('待确认字段须有可检索的字段名称或别名')
    return list(dict.fromkeys([*fixed, *terms, *_query_extras(extras)]))


def _verify_snapshot(task):
    contract, source = _contract(task)
    blocks = _block_index(contract.get('blocks', []))
    originals = _source_blocks(source)
    # The entire ordered search corpus must match; checking only the selected
    # citations would allow a removed block to manufacture a no-match result.
    if list(blocks) != list(originals):
        raise ValueError('模板保留回查的合同原文块缺失或顺序变化，须重新分析')
    for identity, block in blocks.items():
        _validated_citation(block, block['text'], source, originals)
        if block.get('section_path', []) != originals[identity].get('section_path', []):
            raise ValueError('模板保留回查的合同章节与源文件不一致')
    return blocks


def _search(task, queries, blocks):
    actual = search_contract(task, queries, limit=max(len(blocks), 1))
    if actual['source_status'] != 'verified':
        raise ValueError('模板保留回查的合同来源不可用或已变化')
    if any(w.get('code') == 'unreadable_source_content' for w in actual['warnings']):
        raise ValueError('合同原文不可读，不能按合同未提保留模板')
    required = set()
    for match in actual['matches']:
        required.add(match['block_id'])
        for side in ('previous_block', 'next_block'):
            if match.get(side):
                required.add(match[side]['block_id'])
    actual['required_review_block_ids'] = [identity for identity in blocks if identity in required]
    return actual


def _retention_search(task, field_id, queries, blocks):
    actual = _search(task, _queries(task, field_id, queries), blocks)
    actual['retention_field_id'] = field_id
    return actual


def retention_search(task, field_id, queries=()):
    """Return every forced synonym hit and neighbouring source block to review."""
    return _retention_search(task, field_id, queries, _verify_snapshot(task))


def pending_search(task, field_id, queries=(), new_fields=()):
    """Search a registered missing parameter with all field names and aliases."""
    fields = _field_registry(task, {'new_fields': list(new_fields)})
    forced = _pending_queries(field_id, queries, fields)
    actual = _search(task, forced, _verify_snapshot(task))
    actual['pending_field_id'] = field_id
    return actual


def _pending_annotations(record, slot, decisions, comment, rid, key):
    """Bind highlights, comment and unchanged value to the same original span."""
    from text_spans import locate_span
    retained = record['retained_text']
    text = slot['text']
    annotation = comment.get('after')
    if (not isinstance(annotation, str) or not re.search(r'合同[^。；\n]{0,30}未(?:提供|提及|明确|给出|载明|约定|提)', annotation)
            or '待确认' not in annotation):
        raise ValueError('待确认批注须明确合同未提供该参数、模板原值待确认')
    if (comment.get('kind') != 'comment' or comment.get('reviewed') is not True
            or (comment.get('material_id'), comment.get('slot_id')) != key):
        raise ValueError('待确认片段必须关联同槽经审阅的真实批注')
    comment_start, comment_end = locate_span(text, comment.get('before'), comment.get('start'))
    ids = record.get('highlight_decision_ids')
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('待确认片段须关联非空且不重复的highlight_decision_ids')
    spans = []
    for identity in ids:
        matches = [d for d in decisions if d.get('decision_id') == identity]
        if len(matches) != 1:
            raise ValueError('待确认片段须关联唯一实际标黄决定')
        decision = matches[0]
        if (decision.get('kind') != 'highlight' or decision.get('color') != 'yellow'
                or decision.get('reviewed') is not True
                or (decision.get('material_id'), decision.get('slot_id')) != key
                or {'type':'template', 'id':rid} not in decision.get('evidence', [])):
            raise ValueError('待确认标黄须为同槽yellow高亮且关联本需求template依据')
        start, end = locate_span(text, decision.get('before'), decision.get('start'))
        spans.append({'decision_id': identity, 'start': start, 'end': end})
    candidates = []
    offset = text.find(retained)
    while offset >= 0:
        end = offset + len(retained)
        if (comment_start <= offset and end <= comment_end
                and all(offset <= s['start'] and s['end'] <= end for s in spans)):
            candidates.append((offset, end))
        offset = text.find(retained, offset + 1)
    if len(candidates) != 1:
        raise ValueError('批注及全部标黄必须绑定同一个保留片段范围，不得标记其他重复值')
    start, end = candidates[0]
    for decision in decisions:
        if ((decision.get('material_id'), decision.get('slot_id')) != key
                or decision.get('kind') not in {'replace_text', 'placeholder'}):
            continue
        first, last = locate_span(text, decision.get('before'), decision.get('start'))
        if first < end and start < last:
            raise ValueError('待确认保留片段不能与正文替换或整段占位重叠')
    return {'pending_span': {'start':start, 'end':end},
            'highlight_decision_ids': list(ids), 'highlight_spans':spans}


def validate_template_retentions(task, review, rechecks):
    """Return template-exception sources keyed by their own requirement IDs."""
    records = [r for r in review.get('requirements', [])
               if r.get('status') in {'template_retained', 'template_pending'}]
    if not records:
        return {}
    if task.get('material_mode') != 'template':
        raise ValueError('模板保留例外仅用于template模式')
    from material_task import _static_slot, template_required_slots
    fields = _field_registry(task, review) if any(r['status'] == 'template_pending' for r in records) else {}
    materials = {m['material_id']: m for m in task['materials']}
    slots = {(m['material_id'], s['slot_id']): s for m in task['materials'] for s in template_required_slots(m)}
    blocks = _verify_snapshot(task)
    source_hash = _digest(task['contract']['source'].get('sha256'), '合同来源')
    searches, sources = {}, {}
    decisions = review.get('decisions', [])
    for item in [*review.get('requirements', []), *review.get('supplements', []), *rechecks.values()]:
        _parameter_key(item)
    for record in records:
        rid = _text(record.get('requirement_id'), '模板保留requirement_id')
        if rid in sources:
            raise ValueError('模板保留requirement_id重复')
        field_id = record.get('field_id')
        pending = record['status'] == 'template_pending'
        if not pending and field_id not in ALLOWED_FIELDS:
            raise ValueError('该字段不在模板保留例外名单内')
        if pending:
            _pending_queries(field_id, (), fields)
        key = (record.get('material_id'), record.get('slot_id'))
        if key not in slots or record.get('reviewed') is not True:
            raise ValueError('模板保留须经审阅并绑定本次交付范围的槽位')
        slot = slots[key]
        # Identity restrictions apply to the atomic field in _pending_queries,
        # not the inherited slot label: one name-labelled cell may also carry
        # a missing product code while its name is updated from the contract.
        if pending and _static_slot(slot):
            raise ValueError('静态标签内容不能作为模板待确认参数')
        retained = _text(record.get('retained_text'), '模板保留retained_text')
        quote = record.get('template_quote')
        if (not isinstance(quote, str) or quote not in slot['text'] or retained not in quote
                or record.get('rendered_text') != retained):
            raise ValueError('模板保留片段须逐字来自本槽模板并原样显示')
        if record.get('evidence') != [{'type':'template', 'id':rid}]:
            raise ValueError('模板保留依据须使用本需求的template来源，不能冒充合同或用户补充')
        search = record.get('contract_search')
        if not isinstance(search, dict) or search.get('contract_value_status') != 'not_specified':
            raise ValueError('模板保留只适用审阅后合同未给出该项具体内容；明确值、明确无及冲突不适用')
        if _digest(search.get('source_sha256'), '模板保留回查来源') != source_hash:
            raise ValueError('模板保留回查的合同哈希不匹配')
        conclusion = _text(search.get('conclusion'), '模板保留回查conclusion')
        queries = search.get('queries')
        if not isinstance(queries, list):
            raise ValueError('模板保留回查须记录queries数组')
        forced = _pending_queries(field_id, queries, fields) if pending else _queries(task, field_id, queries)
        cache_key = tuple(forced)
        if cache_key not in searches:
            searches[cache_key] = _search(task, forced, blocks)
        actual = searches[cache_key]
        inspected = search.get('reviewed_block_ids')
        if (not isinstance(inspected, list) or any(not isinstance(b, str) for b in inspected)
                or len(inspected) != len(set(inspected)) or not set(inspected).issubset(blocks)
                or not set(actual['required_review_block_ids']).issubset(inspected)):
            raise ValueError('模板保留回查须审阅全部强制检索命中及相邻上下文，不得遗漏或伪造原文块')
        # A reviewed supplementary/recheck value is usable evidence, unlike
        # a broad raw-clause candidate. Do not erase it by declaring absence.
        if any(r.get('field_id') == field_id and (not pending or _same_parameter(record, r)) for r in rechecks.values()):
            raise ValueError('已有对应字段的合同补提依据，不能按合同未提保留模板')
        if any(s.get('field_id') == field_id and (not pending or _same_parameter(record, s)) for s in review.get('supplements', [])):
            raise ValueError('已有对应字段的用户补充依据，应核对该依据而非沿用模板')
        if any(other is not record and other.get('field_id') == field_id
               and other.get('reviewed') is True and other.get('status') in {'verified', 'not_applicable'}
               and any(e.get('type') in {'fact','recheck','supplement'} for e in other.get('evidence',[]))
               and (not pending or _same_parameter(record, other)) for other in review.get('requirements', [])):
            raise ValueError('已有对应字段参数的核实或不适用结论，不能同时声明合同未提供')
        comment_id = _text(record.get('comment_decision_id'), '模板保留comment_decision_id')
        comments = [d for d in decisions if d.get('decision_id') == comment_id]
        if len(comments) != 1:
            raise ValueError('模板保留必须关联唯一实际批注决定')
        comment = comments[0]
        before = comment.get('before')
        annotation_bindings = _pending_annotations(record, slot, decisions, comment, rid, key) if pending else {}
        if not pending and (comment.get('kind') != 'comment' or comment.get('reviewed') is not True
                or (comment.get('material_id'), comment.get('slot_id')) != key
                or comment.get('after') != COMMENT_TEXT or not isinstance(before, str)
                or not before or slot['text'].count(before) != 1 or retained not in before):
            raise ValueError('模板保留批注须锚定同槽保留片段并写入“合同未提，沿用模板”')
        if {'type':'template', 'id':rid} not in comment.get('evidence', []):
            raise ValueError('模板保留批注须关联本需求的template依据')
        material = materials[key[0]]
        sources[rid] = {
            'requirement_id':rid, 'field_id':field_id, 'material_id':key[0], 'slot_id':key[1],
            'value':retained, 'raw_text':retained, 'retained_text':retained, 'rendered_text':retained,
            'status':record['status'], 'source_kind':'template_pending' if pending else 'template_exception',
            'provenance':'template_pending' if pending else 'template_exception', 'contract_verified':False,
            'source_path':material['source']['path'], 'source_sha256':material['source']['sha256'],
            'comment_decision_id':comment_id,
            'contract_search':{'source_sha256':source_hash, 'queries':actual['queries'],
                'reviewed_block_ids':list(inspected), 'required_review_block_ids':actual['required_review_block_ids'],
                'conclusion':conclusion, 'contract_value_status':'not_specified',
                'hit_count':actual['total_match_count'], 'absence_proven':False,
                'warnings':deepcopy(actual['warnings'])},
            'validation_scope':'exact_source_template_and_annotation_bindings; absence_is_reviewer_judgment',
            **annotation_bindings,
        }
        if _parameter_key(record) is not None:
            sources[rid]['parameter_key'] = record['parameter_key']
    return sources
