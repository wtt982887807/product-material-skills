"""Contract-only evidence core. Retained source text is not certified extraction.

No material file is accepted. Rule matches produce review candidates; verification
requires a later, explicit evidence-bound review. Unsupported/embedded content and
unresolved references are reported instead of being silently called complete.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

import corpus

PROJECT = Path(__file__).resolve().parents[1]
NS = corpus.NS
W = "{" + NS["w"] + "}"
NUM = r"[零〇一二三四五六七八九十百千万0-9]+"
ATTACHMENT = re.compile(r"附件\s*[：:]?\s*" + NUM)
REFERENCE = re.compile(r"附件\s*[：:]?\s*" + NUM + r"|第\s*" + NUM + r"\s*(?:部分|章|条|节)|(?:详见|参见|见本合同|按本合同).{0,35}?(?:约定|规定|办理|[。；;])")
EXPLICIT_NAME = re.compile(r"^(?:(?:[一二三四五六七八九十0-9]+[、.．]|\d+\.)\s*)?(?:产品名称|基金名称|计划名称|信托(?:计划)?名称|本(?:集合)?计划名称|名称)\s*[：:|]\s*(.+)")
PRODUCT_END = r"(?:集合资产管理计划|单一资产管理计划|资产管理计划|集合资金信托计划|资金信托计划|信托计划|证券投资基金)"


def _issue(code, message, **extra):
    return {"code": code, "message": message, "severity": "needs_review", **extra}


def _read_config():
    base = json.loads((PROJECT / "config" / "field_dictionary.json").read_text(encoding="utf-8-sig"))
    fields = [f for f in base if "contract" in f.get("source_types", [])]
    extra = json.loads((PROJECT / "config" / "contract_field_extensions.json").read_text(encoding="utf-8-sig"))
    return fields + extra


def _outline(node, styles):
    direct = node.find("w:pPr/w:outlineLvl", NS)
    if direct is not None:
        return int(direct.get(W + "val", "9"))
    style = node.find("w:pPr/w:pStyle", NS)
    return styles.get(style.get(W + "val")) if style is not None else None


def _docx_metadata(path, blocks):
    """Restore style-based chapters and exact source XML coordinates, read-only."""
    issues = []
    with ZipFile(path) as package:
        names = package.namelist()
        styles = {}
        if "word/styles.xml" in names:
            root = corpus._xml(package.read("word/styles.xml"))
            for style in root.findall("w:style", NS):
                level = style.find("w:pPr/w:outlineLvl", NS)
                if level is not None:
                    styles[style.get(W + "styleId")] = int(level.get(W + "val"))
        by_part = {}
        for block in blocks:
            by_part.setdefault(block["location"].get("part"), []).append(block)
        for part, part_blocks in by_part.items():
            if not part or part not in names:
                continue
            root = corpus._xml(package.read(part))
            tree = root.getroottree()
            ps = root.xpath(".//w:p", namespaces=NS)
            ts = root.xpath(".//w:tbl", namespaces=NS)
            sibling_counts, sibling_indexes = Counter(), {}
            for paragraph in ps:
                sibling_counts[paragraph.getparent()] += 1
                sibling_indexes[paragraph] = sibling_counts[paragraph.getparent()]
            section, in_contents, in_attachment = [], False, False
            for block in part_blocks:
                loc = block["location"]
                node = None
                if "paragraph" in loc:
                    node = ps[loc["paragraph"] - 1]
                    loc["paragraph_index_scope"] = "all_descendant_paragraphs_in_part"
                    loc["xml_paragraph_sibling_index"] = sibling_indexes[node]
                elif "table" in loc:
                    table = ts[loc["table"] - 1]
                    rows = [r for r in table.xpath(".//w:tr", namespaces=NS) if r.xpath("ancestor::w:tbl[1]", namespaces=NS)[0] is table]
                    node = rows[loc["row"] - 1]
                if node is not None:
                    loc["xpath"] = tree.getpath(node)
                text = block["text"].strip()
                level = _outline(node, styles) if node is not None and node.tag == W + "p" else None
                is_toc_style = bool(node is not None and node.xpath('./w:pPr/w:pStyle[starts-with(translate(@w:val,"TOC","toc"),"toc")]', namespaces=NS))
                if re.fullmatch(r"目\s*录", text):
                    in_contents = True
                toc_entry = in_contents and (is_toc_style or bool(re.search(r"[\t\s.]\d+\s*$", text)))
                if in_contents and not toc_entry and not re.fullmatch(r"目\s*录", text) and (level is not None or len(text) > 120):
                    in_contents = False
                if not in_contents:
                    attachment_line = next((line.strip() for line in text.splitlines() if re.match(r"^附件\s*[：:]?\s*" + NUM, line.strip()) and len(line.strip()) < 100), None)
                    attachment_heading = bool(attachment_line)
                    if attachment_heading:
                        section, in_attachment = [attachment_line], True
                        block["kind"] = "heading"
                        block["heading_level"] = 0
                        block["attachment_label"] = attachment_line
                    elif level is not None and level < 9 and text and len(text) < 150:
                        section = section[:level + int(in_attachment)] + [text]
                        block["kind"] = "heading"
                        block["heading_level"] = level
                    elif len(text) < 70 and re.match(r"^(?:第\s*" + NUM + r"\s*(?:部分|章)|[一二三四五六七八九十]+[、．.])", text):
                        level = 0 if text.startswith("第") else 1
                        section = section[:level + int(in_attachment)] + [text]
                        block["kind"] = "heading"
                        block["heading_level"] = level
                role = "header_footer" if re.search(r"/(?:header|footer)", part) else "notes" if part.endswith(("footnotes.xml", "endnotes.xml")) else "contents" if in_contents else "attachment" if in_attachment else "body"
                block.update(section_path=list(section), role=role)
                loc["section_path"] = list(section)
            if root.xpath("//w:altChunk | //w:object", namespaces=NS):
                issues.append(_issue("unparsed_embedded_content", "嵌入对象或altChunk正文尚未展开。", part=part))
        embedded = [n for n in names if n.startswith(("word/embeddings/", "word/media/"))]
        if embedded:
            issues.append(_issue("unparsed_embedded_content", "图片或嵌入附件未作为完整可读条款解析。", parts=embedded))
    return issues


def _identity(blocks, issues):
    explicit, cover = {}, {}
    for block in blocks:
        if block.get("role") in {"contents", "header_footer", "notes"}:
            continue
        text = block["text"].strip()
        for line in text.splitlines():
            match = EXPLICIT_NAME.match(line)
            if match:
                name = match[1].strip().rstrip("。；;")
                name = re.sub(r"\s*\|\s*", "", name)
                if re.search(PRODUCT_END, name) and len(name) < 150:
                    explicit.setdefault(name, []).append(block)
        match = re.fullmatch(r"(.{3,100}?" + PRODUCT_END + r")(?:资产管理合同|管理合同|信托合同|基金合同|合同)?", text)
        if match and not re.search(r"[：:|]", text) and not EXPLICIT_NAME.match(text):
            cover.setdefault(match[1], []).append(block)
    candidates = deepcopy(explicit)
    for name, evidence in cover.items():
        candidates.setdefault(name, []).extend(evidence)
    if len(candidates) != 1:
        issues.append(_issue("product_identity_conflict" if candidates else "product_identity_unresolved", "合同正文产品身份存在冲突。" if candidates else "合同正文未识别到可确认产品名称；不使用文件名或目录补足。", candidates=list(candidates)))
        return {"name": None, "aliases": [], "status": "conflict" if candidates else "unresolved", "candidates": list(candidates), "source_locations": []}
    name = next(iter(candidates))
    evidence = candidates[name]
    aliases = []
    for block in blocks:
        for match in re.finditer(r"(?:产品简称|计划简称|基金简称)\s*[：:]\s*([^。；;\n|]+)", block["text"]):
            aliases.append(match[1].strip())
    return {"name": name, "aliases": sorted(set(aliases)), "status": "identified_from_contract", "candidates": [name], "source_locations": [deepcopy(b["location"]) for b in evidence]}


def _contract_type(blocks):
    text = "\n".join(b["text"] for b in blocks[:200] if b.get("role") != "header_footer")
    if "信托合同" in text or "集合资金信托计划" in text:
        return "trust_contract"
    if "资产管理合同" in text or "集合资产管理计划" in text:
        return "asset_management_contract"
    if "基金合同" in text or "证券投资基金" in text:
        return "fund_contract"
    return "unknown"


def _references(text, headings):
    refs = []
    for match in REFERENCE.finditer(text):
        label = match[0].strip("。；; ")
        compact = re.sub(r"\s+", "", label)
        targets = [b for b in headings if re.sub(r"\s+", "", b.get("attachment_label", b["text"])).startswith(compact)]
        refs.append({"text": label, "status": "anchor_found_requires_semantic_review" if targets else "unresolved", "target_locations": [deepcopy(b["location"]) for b in targets]})
    return refs


def _qualifiers(text):
    sentences = [s for s in re.split(r"(?<=[。；;\n])", text) if s.strip()]
    return {
        "conditions": [s for s in sentences if re.search(r"若|如果|当|如有|符合|满足|条件|前提|发生|除外|除非|但|不适用|仅限|方可", s)],
        "exceptions": [s for s in sentences if re.search(r"但|除外|除非|另有|例外|豁免|免收|减免", s)],
        "units": sorted(set(re.findall(r"自然日|工作日|交易日|开放日|每年|每月|年率|年费率|/年|人民币|美元|港元|份额", text))),
        "share_classes": sorted(set(re.findall(r"[A-ZＡ-Ｚ]类(?:份额)?", text))),
    }


def _null_state(text, field):
    # Apply only an explicit labelled value. A nearby '无' in a conditional clause
    # must never turn a missing or conditional fee into an unconditional zero.
    aliases = [field["name"]] + field.get("aliases", [])
    for alias in aliases:
        match = re.search(re.escape(alias) + r"\s*[：:|]\s*(.*)$", text)
        if match:
            value = match[1].strip().strip("。；;")
            if not value or re.fullmatch(r"[_＿—\-\s□]+", value):
                return "blank_in_source"
            if value in {"无", "没有", "不设置", "未设置"}:
                return "explicit_none"
            if value in {"不适用", "N/A", "不涉及"}:
                return "explicit_not_applicable"
    return "present"


def _make_candidate(field, block, source, headings, ordinal):
    text = block["text"]
    refs = _references(text, headings)
    null_state = _null_state(text, field)
    sufficiency = "incomplete_source_value" if null_state == "blank_in_source" else "cross_reference_requires_review" if refs else "requires_review"
    explicit_null = null_state in {"explicit_none", "explicit_not_applicable"}
    return {"fact_id": f"cf-{ordinal:05d}", "field_id": field["field_id"], "name": field["name"], "raw_text": text,
            "value": {"state": null_state, "value": None} if explicit_null else None, "status": "candidate", "extraction_level": "raw_clause_candidate", "normalization_status": "explicit_null_state_only" if explicit_null else "not_normalized",
            "null_state": null_state, "condition_sufficiency": sufficiency,
            "cross_references": refs, **_qualifiers(text), "source_locations": [deepcopy(block["location"])],
            "source_block_ids": [block["block_id"]], "source_path": source["path"], "source_sha256": source["sha256"],
            "source_version": source["sha256"], "section_path": block.get("section_path", []),
            "extraction_note": "整段或整行原文候选；尚未证明字段适用范围及条件充分，也未完成值规范化。"}


def _merge_qdii_profile(path, product, source, facts, issues):
    """Explicit product + all selectors/parsers required; never read profile target."""
    if product["name"] != "国泰海通鑫源全周期QDII25号集合资产管理计划" or path.suffix.lower() != ".docx":
        return {"applied": False, "reason": "product_or_format_not_applicable"}
    import qdii_pipeline
    profile = json.loads(qdii_pipeline.PROFILE.read_text(encoding="utf-8-sig"))
    doc = qdii_pipeline.read_docx(path)
    values, _, failed = qdii_pipeline.extract_facts(doc, profile)
    by_id = {f["field_id"]: f for f in values}
    if failed or len(values) != len(profile["fields"]) or by_id.get("product_name", {}).get("value") != product["name"]:
        issues.append(_issue("structured_profile_not_applicable", "QDII合同规则未全部适配，未合并部分结构化结果。", details=failed))
        return {"applied": False, "reason": "selector_or_product_check_failed"}
    for item in values:
        value = deepcopy(item)
        # Legacy extraction also has one-pager display policy. It has no place in
        # the shared contract layer: retain every strategy, no display omissions.
        value.pop("not_displayed_content", None)
        value["display_text"] = value["raw_text"]
        if isinstance(value["value"], dict):
            value["value"].pop("displayed_items", None)
            value["value"].pop("excluded_items", None)
        value.update(fact_id=f"cf-{len(facts) + 1:05d}", status="candidate", extraction_level="structured_candidate",
                     normalization_status="profile_normalized_requires_review", source_locations=value.pop("source_location"),
                     source_path=source["path"], source_sha256=source["sha256"], source_version=source["sha256"],
                     profile_id=profile["profile_id"], null_state="present", condition_sufficiency="requires_review",
                     cross_references=[], **_qualifiers(item["raw_text"]))
        value["extraction_note"] = "同产品且21项选择/解析规则全部适配；仅结构化候选，不代表人工核验或完整条款适用性确认。"
        for location in value["source_locations"]:
            location.update(source_path=source["path"], source_sha256=source["sha256"])
            if "paragraph" in location:
                location["paragraph_index_scope"] = "legacy_profile_top_level_body_paragraphs"
                location["xml_paragraph_sibling_index"] = location["paragraph"]
            if source.get("converted_path"):
                location["converted_copy"] = source["converted_path"]
        facts.append(value)
    return {"applied": True, "profile_id": profile["profile_id"], "field_count": len(values), "status": "structured_candidates_only"}


def build_contract_facts(contract_path, project_dir=None):
    """Build immutable source-bound evidence with no dependency on target materials."""
    path = Path(contract_path).resolve()
    if path.suffix.lower() not in {".docx", ".doc", ".pdf"}:
        raise ValueError("合同支持DOCX、DOC（需hash匹配的转换缓存）、PDF")
    source = {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}
    doc_id = "contract-" + source["sha256"][:16]
    parsed = corpus.parse_document_cached(path, doc_id, source["sha256"], project_dir or PROJECT)
    blocks = deepcopy(parsed["blocks"])
    issues = [_issue(code, code) for code in dict.fromkeys(parsed["issues"])]
    source["parse_status"] = parsed["parse_status"]
    parse_path = path
    if path.suffix.lower() == ".doc" and blocks:
        parse_path = Path(blocks[0]["location"]["converted_copy"])
        source.update(converted_path=str(parse_path), converted_sha256=sha256(parse_path.read_bytes()).hexdigest())
    if parse_path.suffix.lower() == ".docx" and blocks:
        issues.extend(_docx_metadata(parse_path, blocks))
    for block in blocks:
        block.setdefault("role", "body")
        block["source_sha256"] = source["sha256"]
        block["source_path"] = source["path"]
        block["location"]["source_sha256"] = source["sha256"]
        block["location"]["source_path"] = source["path"]
    product = _identity(blocks, issues)
    source["contract_type"] = _contract_type(blocks)
    fields, facts = _read_config(), []
    headings = [b for b in blocks if b.get("kind") == "heading" and b.get("role") != "contents"]
    field_states = {f["field_id"]: "not_found" for f in fields}
    generic = {"field_id": "contract_clause", "name": "其他合同原文条款", "aliases": []}
    for block in blocks:
        if not block["text"].strip() or block["role"] in {"contents", "header_footer", "notes"} or (block["kind"] == "heading" and not re.search(r"[：:]\s*\S", block["text"])):
            continue
        text, section = block["text"], " / ".join(block.get("section_path", []))
        matches = [f for f in fields if any(a and a in text for a in [f["name"]] + f.get("aliases", [])) or
                   any(re.search(p, section) for p in f.get("section_patterns", [])) or
                   (f["field_id"] == "product_name" and EXPLICIT_NAME.match(text))]
        for field in matches or [generic]:
            fact = _make_candidate(field, block, source, headings, len(facts) + 1)
            facts.append(fact)
            if field["field_id"] in field_states:
                field_states[field["field_id"]] = "candidate"
            if fact["null_state"] == "blank_in_source":
                issues.append(_issue("blank_contract_value", "源合同字段留白，不能当作零或不适用。", fact_id=fact["fact_id"], field_id=fact["field_id"]))
    for block in blocks:
        if block.get("role") == "contents" or block.get("kind") == "heading":
            continue
        for ref in _references(block["text"], headings):
            if ref["status"] == "unresolved":
                issues.append(_issue("unresolved_cross_reference", "引用未定位到可确认标题，需补依据或人工核对。", reference=ref["text"], source_locations=[deepcopy(block["location"])]))
    profile = _merge_qdii_profile(parse_path, product, source, facts, issues)
    for fact in facts:
        if fact["field_id"] in field_states:
            field_states[fact["field_id"]] = "candidate"
    unreadable = sorted({b["location"]["page"] for b in blocks if "page" in b["location"] and b.get("quality") in {"no_text", "garbled_text", "ocr_unverified"}})
    attachment_heads = [b for b in headings if b.get("attachment_label")]
    attachment_refs = sorted(set(m[0] for b in blocks for m in ATTACHMENT.finditer(b["text"])))
    blocking_content = any(i["code"] == "unparsed_embedded_content" for i in issues)
    text_parsed = parsed["parse_status"] in {"parsed", "converted_copy_parsed", "needs_review"} and bool(blocks) and not unreadable
    coverage = {"parse_status": parsed["parse_status"], "source_block_count": len(parsed["blocks"]), "retained_block_count": len(blocks),
                "retained_source_text_ratio": 1.0 if blocks else 0.0, "retained_character_count": sum(len(b["text"]) for b in blocks),
                "all_source_content_parsed": bool(text_parsed and not blocking_content), "structured_extraction_complete": False,
                "verified_fact_count": 0, "unreadable_pages": unreadable, "field_states": field_states,
                "block_roles": dict(Counter(b["role"] for b in blocks)), "chapters": [{"title": b["text"], "location": b["location"]} for b in headings],
                "attachments": {"present": bool(attachment_heads), "headings": [b.get("attachment_label", b["text"]) for b in attachment_heads], "references": attachment_refs,
                                "completeness": "not_certified"},
                "meaning": "完整保留解析器返回的原文块；不等于所有图像、嵌入附件可读，不等于全部事实已规范化或核验。"}
    summary = {"retained_block_count": len(blocks), "fact_count": len(facts), "raw_candidate_count": sum(f["extraction_level"] == "raw_clause_candidate" for f in facts),
               "structured_candidate_count": sum(f["extraction_level"] == "structured_candidate" for f in facts), "verified_fact_count": 0,
               "matched_field_count": sum(v == "candidate" for v in field_states.values()), "missing_field_count": sum(v == "not_found" for v in field_states.values()),
               "issue_count": len(issues), "issue_code_count": len({i["code"] for i in issues}), "issue_counts": dict(Counter(i["code"] for i in issues)),
               "status": "requires_review", "profile": profile}
    # Detect source changes while parsing; never return facts bound to a stale hash.
    if sha256(path.read_bytes()).hexdigest() != source["sha256"]:
        raise ValueError("Contract changed during parsing; discard this analysis and retry")
    return {"source": source, "product": product, "facts": facts, "blocks": blocks, "coverage": coverage, "issues": issues, "summary": summary}
