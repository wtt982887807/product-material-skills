"""Evidence-based exception checks. Findings are review tasks, never new facts."""
from collections import defaultdict
from hashlib import sha256
import re

EXTERNAL_FIELDS = {'current_open_date','nav','cumulative_return','annualized_return','max_drawdown',
                   'volatility','excess_return','rank','peer_performance','manager_profile','awards','aum',
                   'sales_period_start','sales_period_end','distributor_risk_rating'}


def inspect_cases(task):
    from requirement_review import enabled
    atomic_mode=enabled(task)
    checks, keys = [], set()
    facts = defaultdict(list)
    for fact in task['contract']['facts']: facts[fact['field_id']].append(fact)

    def add(kind, message, material=None, slot=None, severity='review', **extra):
        key = (kind, material and material['material_id'], slot and slot['slot_id'], message)
        if key in keys: return
        keys.add(key)
        checks.append({'check_id':'check-'+sha256(repr(key).encode()).hexdigest()[:14],
                       'type':kind,'severity':severity,'status':'pending' if severity!='info' else 'recorded',
                       'material_id':material and material['material_id'],
                       'slot_id':slot and slot['slot_id'],'field_id':slot and slot.get('field_id'),
                       'region_id':slot and slot.get('region_id'),'message':message,
                       'source_text':slot and slot['text'],'resolution':None,**extra})

    for issue in task['contract'].get('issues', []):
        message = issue if isinstance(issue,str) else issue.get('message',str(issue))
        add('contract_review',message)
    for m in task['materials']:
        template_mode = task.get('material_mode') == 'template'
        element_pages=m.get('template_selection',{}).get('element_pages') if template_mode else None
        scoped_regions=None
        if element_pages is not None:
            pages=set(element_pages.get('pages',[])+element_pages.get('review_pages',[]))
            scoped_regions={r['region_id'] for r in m['regions']
                            if any(loc.get('page') in pages for loc in r.get('locations',[]))}
        targets = [r for r in m['regions'] if r['status']=='target']
        others = [r for r in m['regions'] if r['status']=='other']
        ambiguous = [r for r in m['regions'] if r['status']=='ambiguous' and r.get('identity_review_required',True)
                     and (scoped_regions is None or r['region_id'] in scoped_regions)]
        if not targets:
            add('template_selection' if template_mode else 'product_selection',
                '尚未确定要套用的模板主体及要素区域；需选择模板范围，合同与模板名称不同不构成错误。'
                if template_mode else '没有可确定的目标产品区域，禁止回写；需确认产品名称及对应位置。',m,severity='blocking',
                requires_reanalysis=template_mode)
        if element_pages is not None and element_pages.get('needs_review'):
            add('template_element_pages','模板自身要素页范围尚未确定或与其他产品混页；须明确范围后重新分析。',
                m,severity='blocking',requires_reanalysis=True)
        if others:
            add('other_products',f'识别到{len(others)}个非目标产品区域，保持原样。',m,severity='info',
                related_region_ids=[r['region_id'] for r in others])
        if ambiguous:
            add('ambiguous_regions',f'{len(ambiguous)}个区域身份未确定，禁止自动修改。',m,severity='review',
                related_region_ids=[r['region_id'] for r in ambiguous],requires_reanalysis=template_mode)
        for issue in m.get('issues',[]):
            if isinstance(issue,dict) and scoped_regions is not None and issue.get('region_id') and issue['region_id'] not in scoped_regions:
                continue
            reanalyze=template_mode and isinstance(issue,dict) and issue.get('code') in {
                'template_scope_selection_required','template_unselected_document_content','empty_template_value'}
            add('material_parse',issue if isinstance(issue,str) else issue.get('message',str(issue)),m,
                requires_reanalysis=reanalyze)
        formulas=defaultdict(list)
        observed_fields=set()
        for s in m['slots']:
            if s.get('product_status')!='target': continue
            if template_mode and not s.get('delivery_required', True): continue
            text=s['text']; fid=s.get('field_id'); observed_fields.add(fid)
            evidence=[f['fact_id'] for f in facts.get(fid,[])]
            if template_mode:
                add('template_content_review','逐槽确认目标产品最终内容；模板旧值仅展示格式，不能因存在同名合同字段而直接沿用。',
                    m,s,fact_ids=evidence,requires_content_disposition=True)
                if s.get('content_role') == 'label_only': continue
            if re.search(r'(?i)(?:xx+|待填|待补|某某)\s*(?:机构|银行|公司|日期|名称)|\[\s*\]|【\s*】',text):
                add('placeholder','存在待替换占位符；按产品、渠道和本次用途核实，不能批量替换其他产品。',m,s)
            dated_opening=bool(re.search(r'下一开放期|下次开放|\d{1,2}月\d{1,2}日.{0,8}开放|开放.{0,8}\d{1,2}月\d{1,2}日',text))
            if fid in EXTERNAL_FIELDS or dated_opening or re.search(r'本次募集|最新净值',text):
                add('external_information','该项含当前/渠道/经营信息，须核实适用时点及来源；缺项由用户补充，不能从旧稿直接认定最新。',m,s,
                    fact_ids=evidence,needs_external_evidence=True,
                    required_evidence_field='current_open_date' if dated_opening else fid)
            related='\n'.join(f.get('raw_text','') for f in facts.get(fid,[]))
            if re.search(r'公告|另行确定|另行约定|R1|F1',related) and re.search(r'\d+(?:\.\d+)?\s*%',text) and fid in {
                    'performance_fee','performance_fee_calculation','management_fee','custody_fee','purchase_fee','participation_fee'}:
                add('external_parameter','合同含另行公告或可变参数；物料具体费率/门槛需有对应有效依据。',m,s,fact_ids=evidence,
                    needs_external_evidence=True,required_evidence_field=fid)
            needed_fields={c['field_id'] for c in task.get('requirement_candidates',[])
                           if c['material_id']==m['material_id'] and c['slot_id']==s['slot_id'] and c.get('field_id')} if atomic_mode else {fid}
            for needed in sorted(f for f in needed_fields if f):
                if not facts.get(needed):
                    add('missing_evidence','模板需要此要素，但当前底稿未建立对应事实；先回查合同和关联条款，仍无法取得且确认适用时再补充外部来源。',
                        m,{**s,'field_id':needed},candidate_ids=[c['candidate_id'] for c in task.get('requirement_candidates',[])
                            if c['material_id']==m['material_id'] and c['slot_id']==s['slot_id'] and c.get('field_id')==needed])
            if fid in {'initial_subscription_min','initial_participation_min','additional_subscription_min','additional_participation_min','minimum_subscription_amount','amount_fee_basis'}:
                # Broader field evidence can prove a qualifier exists, but not which
                # stage it applies to; retain as review instead of automatically adding.
                amounts=related or '\n'.join(f.get('raw_text','') for key in facts if 'min' in key or 'fee_basis' in key for f in facts[key])
                if '不含' in amounts and not re.search(r'含.{0,8}费',text):
                    add('amount_qualifier','金额展示未注明含费口径，合同存在不含费用条件；核实该阶段是否适用。',m,s,fact_ids=evidence)
            if re.search(r'(?:累计)?业绩报酬\s*[=＝]',text):
                for fragment in re.split(r'[\n；;]',text):
                    if re.search(r'(?:累计)?业绩报酬\s*[=＝]',fragment) and re.search(r'[×*]',fragment):
                        formulas[s['region_id']].append((s,fragment))
            if fid in {'opening_schedule','minimum_holding_period','redemption_window'} and re.search(r'红利再投资|前.{0,3}个|后.{0,3}个|不顺延|取消开放',text):
                add('conditional_display','该要素含业务分段、例外或日历条件，摘要与回写时须保留适用关系。',m,s,severity='info',fact_ids=evidence)
        for region_id, items in formulas.items():
            added=[(s,t) for s,t in items if re.search(r'[+＋]',t)]
            simple=[(s,t) for s,t in items if '累计业绩报酬' in t and not re.search(r'[+＋]',t)]
            if added and simple:
                s,t=simple[0]
                add('formula_conflict','同一产品区域的累计业绩报酬公式有的包含分档累加项、有的未包含；需核对完整分支及脚注，不能直接择一覆盖。',m,s,
                    related_slot_ids=list(dict.fromkeys(x['slot_id'] for x,_ in items)),
                    compared_texts=[t for _,t in items])
    return checks
