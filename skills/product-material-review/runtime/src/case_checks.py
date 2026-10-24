"""Evidence-based exception checks. Findings are review tasks, never new facts."""
from collections import defaultdict
from hashlib import sha256
import re

EXTERNAL_FIELDS = {'current_open_date','nav','cumulative_return','annualized_return','max_drawdown',
                   'volatility','excess_return','rank','peer_performance','manager_profile','awards','aum',
                   'sales_period_start','sales_period_end','distributor_risk_rating'}


def inspect_cases(task):
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
        targets = [r for r in m['regions'] if r['status']=='target']
        others = [r for r in m['regions'] if r['status']=='other']
        ambiguous = [r for r in m['regions'] if r['status']=='ambiguous' and r.get('identity_review_required',True)]
        if not targets:
            add('product_selection','没有可确定的目标产品区域，禁止回写；需确认产品名称及对应位置。',m,severity='blocking')
        if others:
            add('other_products',f'识别到{len(others)}个非目标产品区域，保持原样。',m,severity='info',
                related_region_ids=[r['region_id'] for r in others])
        if ambiguous:
            add('ambiguous_regions',f'{len(ambiguous)}个区域身份未确定，禁止自动修改。',m,severity='review',
                related_region_ids=[r['region_id'] for r in ambiguous])
        for issue in m.get('issues',[]):
            add('material_parse',issue if isinstance(issue,str) else issue.get('message',str(issue)),m)
        formulas=defaultdict(list)
        observed_fields=set()
        for s in m['slots']:
            if s.get('product_status')!='target': continue
            text=s['text']; fid=s.get('field_id'); observed_fields.add(fid)
            evidence=[f['fact_id'] for f in facts.get(fid,[])]
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
            if fid and not evidence:
                add('missing_evidence','物料有此要素，但当前底稿未建立对应事实；需回查合同或补充来源，不能视为无或不适用。',m,s)
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
