"""Portable CLI facade; core code/config location is based on __file__, never cwd."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from material_task import PROJECT, ReviewError, file_hash, read_json, write_json


def doctor():
    dependencies={name:importlib.util.find_spec(name) is not None for name in ('lxml','pypdf')}
    required=['field_dictionary.json','contract_field_extensions.json','qdii25_profile.json']
    configs={name:(PROJECT/'config'/name).is_file() for name in required}
    modules={name:(PROJECT/'src'/(name+'.py')).is_file() for name in ('material_cli','material_task','contract_facts',
             'material_regions','material_writer','case_checks','corpus','qdii_pipeline','task_report','task_validation','ppt_delivery')}
    return {'ok':sys.version_info >= (3,11) and all(dependencies.values()) and all(configs.values()) and all(modules.values()),
            'python':sys.version.split()[0],'executable':sys.executable,'runtime':str(PROJECT),
            'dependencies':dependencies,'configurations':configs,'modules':modules,
            'office_required_for_core':False,'office_note':'原生打开及视觉验收需目标机器可用的Office或等效渲染器；不得以结构检查冒充视觉检查。'}


def register_conversion(source,converted,cache_dir):
    source,converted,cache_dir=Path(source).resolve(),Path(converted).resolve(),Path(cache_dir).resolve()
    if source.suffix.lower()!='.doc' or converted.suffix.lower()!='.docx':
        raise ReviewError('合同转换登记仅接受原DOC与对应DOCX；转换本身须用可信Office工具完成')
    if source==converted or not source.is_file() or not converted.is_file():raise ReviewError('转换来源或副本不存在')
    from zipfile import ZipFile
    with ZipFile(converted) as package:
        if 'word/document.xml' not in package.namelist():raise ReviewError('转换副本不是有效DOCX')
    index=cache_dir/'cache/legacy/index.json'
    data=read_json(index) if index.exists() else {'schema_version':2,'items':[]}
    if isinstance(data,list):data={'schema_version':2,'items':data}
    entries=data.setdefault('items',[])
    def relative(p):
        try:return os.path.relpath(p,cache_dir)
        except ValueError:return str(p)
    def resolved(p):
        p=Path(p);return (cache_dir/p).resolve() if not p.is_absolute() else p.resolve()
    entries[:]=[e for e in entries if resolved(e['source'])!=source]
    entry={'source':relative(source),'converted':relative(converted),'sha256':file_hash(source),
           'converted_sha256':file_hash(converted),'status':'converted','provenance':'explicit_user_provided_conversion',
           'note':'双hash绑定版本，不自动证明DOC与DOCX内容等价；转换后须核对产品身份和完整性。'}
    entries.append(entry);write_json(index,data)
    return {'index':str(index),'entry':entry,'analyze_option':'--project-dir '+str(cache_dir)}


def deliver_ppt(task,result,material_id,output,pages=None,review_reason=None):
    from task_validation import validate_task_outputs
    from ppt_delivery import suggest_element_pages,export_selected_slides
    verified=validate_task_outputs(task,result)
    if not verified['passed']:raise ReviewError('完整审阅稿未通过重新验证：'+str(verified['issues']))
    material=next((m for m in task['materials'] if m['material_id']==material_id),None)
    record=next((r for r in result['results'] if r['material_id']==material_id),None)
    if material is None or record is None or Path(record['output']).suffix.lower()!='.pptx':raise ReviewError('请选择本任务的PPTX物料')
    suggestion=suggest_element_pages(material)
    if pages is None:
        if suggestion['needs_review']:raise ReviewError('产品要素页范围需核实：'+json.dumps(suggestion,ensure_ascii=False))
        pages=suggestion['pages']
    elif not review_reason or not review_reason.strip():raise ReviewError('手工选页须说明核实依据')
    for page in pages:
        regions=[r for r in material['regions'] if any(loc.get('page')==page for loc in r.get('locations',[]))]
        if not any(r['status']=='target' for r in regions):raise ReviewError(f'第{page}页没有明确目标产品区域')
        if any(r['status']=='other' for r in regions):raise ReviewError(f'第{page}页含其他产品，不能作为仅目标产品的整页交付')
        if any(r.get('status')=='ambiguous' and r.get('product_names') for r in regions):
            raise ReviewError(f'第{page}页含尚未确认的产品身份，不能通过手工页码绕过')
        if any((s.get('location',{}).get('page') or s.get('page'))==page and s.get('field_id') and s.get('product_status')=='ambiguous' for s in material['slots']):
            raise ReviewError(f'第{page}页含身份待核要素，不能通过手工页码绕过')
    output=Path(output).resolve()
    if output.suffix.lower()!='.pptx':raise ReviewError('交付文件扩展名必须为.pptx')
    manifest=output.with_suffix('.delivery.json')
    if output.exists() or manifest.exists():raise ReviewError('交付输出已存在，请使用新文件名')
    source=Path(record['output'])
    exported=export_selected_slides(source,output,pages,record['output_sha256'])
    if not exported['valid']:raise ReviewError(str(exported['errors']))
    slots={s['slot_id']:s for s in material['slots']}
    decisions=[d for d in result['applied_review'].get('decisions',[]) if d['material_id']==material_id]
    included=[];excluded=[]
    for d in decisions:
        slot=slots[d['slot_id']]
        page=slot.get('location',{}).get('page') or slot.get('page')
        (included if page in pages else excluded).append(d['decision_id'])
    exported.update(task_id=task['task_id'],material_id=material_id,product=task['target_product'],
                    source_material=material['source']['path'],source_material_sha256=material['source']['sha256'],
                    selection=suggestion,review_reason=review_reason,
                    included_decisions=included,excluded_decisions=excluded,visual_status='not_checked',
                    note='只交付所选产品要素页；完整稿中的其他页及其修改不在本交付中，待核信息不因此自动结案。')
    write_json(manifest,exported)
    return {'output':str(output),'manifest':str(manifest),'pages':pages,'valid':True,
            'included_decisions':included,'excluded_decisions':excluded,'visual_status':'not_checked'}


def main():
    if len(sys.argv)>1 and sys.argv[1] in {'analyze','apply','validate','accept'}:
        if sys.argv[1]=='analyze' and '--contract' in sys.argv:
            contract=Path(sys.argv[sys.argv.index('--contract')+1])
            if contract.suffix.lower()=='.doc' and '--project-dir' not in sys.argv:
                raise ReviewError('旧DOC需先转换成DOCX；可直接分析DOCX，或register-conversion登记后用--project-dir指定缓存。')
        from material_task import main as core_main
        return core_main()
    parser=argparse.ArgumentParser(description='产品物料审阅CLI：analyze / apply / validate / accept / deliver-ppt / doctor / register-conversion')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor')
    d=sub.add_parser('deliver-ppt')
    for name in ('task','result','material-id','out'):d.add_argument('--'+name,required=True)
    d.add_argument('--pages',help='核实后的原PPT页码，例如22,23；不填则保守识别')
    d.add_argument('--review-reason')
    r=sub.add_parser('register-conversion')
    for name in ('source','converted','cache-dir'):r.add_argument('--'+name,required=True)
    args=parser.parse_args()
    if args.command=='doctor':
        value=doctor();print(json.dumps(value,ensure_ascii=False,indent=2))
        if not value['ok']:raise SystemExit(1)
    elif args.command=='register-conversion':print(json.dumps(register_conversion(args.source,args.converted,args.cache_dir),ensure_ascii=False,indent=2))
    else:
        pages=[int(x.strip()) for x in args.pages.split(',')] if args.pages else None
        value=deliver_ppt(read_json(args.task),read_json(args.result),args.material_id,args.out,pages,args.review_reason)
        print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as error:print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);raise SystemExit(2)
