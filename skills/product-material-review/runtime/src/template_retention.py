"""Narrow user-authorized template retention, never a verified contract fact.

The reviewer decides whether the complete relevant contract context supplies a
value. This module binds that decision to current source text, required searches,
unchanged template text and an actual comment operation.
"""
from copy import deepcopy

from contract_recheck import (
    _block_index, _contract, _digest, _source_blocks, _text,
    _validated_citation, search_contract,
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


def _queries(task, field_id, extras):
    if field_id not in ALLOWED_FIELDS:
        raise ValueError('模板保留例外仅适用已定义的业绩报酬字段和代销机构销售评级')
    if not isinstance(extras, (list, tuple)) or any(not isinstance(q, str) or not q.strip() for q in extras):
        raise ValueError('模板保留回查queries须为非空词语的数组')
    fixed = PERFORMANCE_QUERIES if field_id in PERFORMANCE_FIELDS else DISTRIBUTOR_QUERIES
    aliases = []
    for field in task.get('field_dictionary', []):
        if field.get('field_id') == field_id:
            aliases.extend([field.get('name', '')] + field.get('aliases', []))
    return list(dict.fromkeys([*fixed, *(q.strip() for q in aliases if isinstance(q, str) and q.strip()),
                               *(q.strip() for q in extras)]))


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


def _retention_search(task, field_id, queries, blocks):
    actual = search_contract(task, _queries(task, field_id, queries), limit=max(len(blocks), 1))
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
    actual['retention_field_id'] = field_id
    return actual


def retention_search(task, field_id, queries=()):
    """Return every forced synonym hit and neighbouring source block to review."""
    return _retention_search(task, field_id, queries, _verify_snapshot(task))


def validate_template_retentions(task, review, rechecks):
    """Return template-exception sources keyed by their own requirement IDs."""
    records = [r for r in review.get('requirements', []) if r.get('status') == 'template_retained']
    if not records:
        return {}
    if task.get('material_mode') != 'template':
        raise ValueError('模板保留例外仅用于template模式')
    from material_task import template_required_slots
    materials = {m['material_id']: m for m in task['materials']}
    slots = {(m['material_id'], s['slot_id']): s for m in task['materials'] for s in template_required_slots(m)}
    blocks = _verify_snapshot(task)
    source_hash = _digest(task['contract']['source'].get('sha256'), '合同来源')
    searches, sources = {}, {}
    decisions = review.get('decisions', [])
    for record in records:
        rid = _text(record.get('requirement_id'), '模板保留requirement_id')
        if rid in sources:
            raise ValueError('模板保留requirement_id重复')
        field_id = record.get('field_id')
        if field_id not in ALLOWED_FIELDS:
            raise ValueError('该字段不在模板保留例外名单内')
        key = (record.get('material_id'), record.get('slot_id'))
        if key not in slots or record.get('reviewed') is not True:
            raise ValueError('模板保留须经审阅并绑定本次交付范围的槽位')
        slot = slots[key]
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
        forced = _queries(task, field_id, queries)
        cache_key = (field_id, tuple(forced))
        if cache_key not in searches:
            searches[cache_key] = _retention_search(task, field_id, queries, blocks)
        actual = searches[cache_key]
        inspected = search.get('reviewed_block_ids')
        if (not isinstance(inspected, list) or any(not isinstance(b, str) for b in inspected)
                or len(inspected) != len(set(inspected)) or not set(inspected).issubset(blocks)
                or not set(actual['required_review_block_ids']).issubset(inspected)):
            raise ValueError('模板保留回查须审阅全部强制检索命中及相邻上下文，不得遗漏或伪造原文块')
        # A reviewed supplementary/recheck value is usable evidence, unlike
        # a broad raw-clause candidate. Do not erase it by declaring absence.
        if any(r.get('field_id') == field_id for r in rechecks.values()):
            raise ValueError('已有对应字段的合同补提依据，不能按合同未提保留模板')
        if any(s.get('field_id') == field_id for s in review.get('supplements', [])):
            raise ValueError('已有对应字段的用户补充依据，应核对该依据而非沿用模板')
        comment_id = _text(record.get('comment_decision_id'), '模板保留comment_decision_id')
        comments = [d for d in decisions if d.get('decision_id') == comment_id]
        if len(comments) != 1:
            raise ValueError('模板保留必须关联唯一实际批注决定')
        comment = comments[0]
        before = comment.get('before')
        if (comment.get('kind') != 'comment' or comment.get('reviewed') is not True
                or (comment.get('material_id'), comment.get('slot_id')) != key
                or comment.get('after') != COMMENT_TEXT or not isinstance(before, str)
                or not before or slot['text'].count(before) != 1 or retained not in before):
            raise ValueError('模板保留批注须锚定同槽保留片段并写入“合同未提，沿用模板”')
        if {'type':'template', 'id':rid} not in comment.get('evidence', []):
            raise ValueError('模板保留批注须关联本需求的template依据')
        material = materials[key[0]]
        sources[rid] = {
            'requirement_id':rid, 'field_id':field_id, 'material_id':key[0], 'slot_id':key[1],
            'value':retained, 'raw_text':retained, 'retained_text':retained,
            'status':'template_retained', 'source_kind':'template_exception',
            'provenance':'template_exception', 'contract_verified':False,
            'source_path':material['source']['path'], 'source_sha256':material['source']['sha256'],
            'comment_decision_id':comment_id,
            'contract_search':{'source_sha256':source_hash, 'queries':actual['queries'],
                'reviewed_block_ids':list(inspected), 'required_review_block_ids':actual['required_review_block_ids'],
                'conclusion':conclusion, 'contract_value_status':'not_specified',
                'hit_count':actual['total_match_count'], 'absence_proven':False,
                'warnings':deepcopy(actual['warnings'])},
            'validation_scope':'exact_source_template_and_annotation_bindings; absence_is_reviewer_judgment',
        }
    return sources
