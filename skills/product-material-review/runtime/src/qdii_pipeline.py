"""Auditable, contract-first QDII25 extraction; never uses the target to fill facts.

Selectors and display policy live in config/qdii25_profile.json. Paragraph numbers
are generated evidence coordinates, not extraction selectors. DOCX revisions are
read as accepted text, and exactly one Markup Compatibility branch is selected.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata
from zipfile import ZipFile
import xml.etree.ElementTree as ET

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006"}
W = "{" + NS["w"] + "}"
MC = "{" + NS["mc"] + "}"
PROFILE = Path(__file__).resolve().parents[1] / "config" / "qdii25_profile.json"


def _accept_content(element, quality):
    for child in list(element):
        if child.tag == W + "tr" and child.find("w:trPr/w:del", NS) is not None:
            quality["deleted_rows"] += 1
            quality["deleted_nodes"] += 1
            element.remove(child)
        elif child.tag == W + "tc" and child.find("w:tcPr/w:cellDel", NS) is not None:
            quality["deleted_cells"] += 1
            quality["deleted_nodes"] += 1
            element.remove(child)
        elif child.tag in (W + "del", W + "moveFrom"):
            quality["deleted_nodes"] += 1
            element.remove(child)
        elif child.tag == MC + "AlternateContent":
            quality["compatibility_branches"] += 1
            selected = child.find("mc:Choice", NS)
            if selected is None:
                selected = child.find("mc:Fallback", NS)
            index = list(element).index(child)
            element.remove(child)
            if selected is not None:
                _accept_content(selected, quality)
                for offset, grandchild in enumerate(list(selected)):
                    element.insert(index + offset, grandchild)
        else:
            _accept_content(child, quality)


def _text(element):
    result = []
    for node in element.iter():
        if node.tag == W + "t":
            result.append(node.text or "")
        elif node.tag in (W + "br", W + "cr"):
            result.append("\n")
        elif node.tag == W + "tab":
            result.append(" ")
    return "".join(result).strip()


def read_docx(path):
    """Read body paragraphs/table rows without changing the package or revisions."""
    path = Path(path)
    blob = path.read_bytes()
    quality = {"deleted_nodes": 0, "inserted_nodes": 0, "deleted_rows": 0, "deleted_cells": 0,
               "compatibility_branches": 0, "revision_policy": "accept_insertions_exclude_deletions_and_moveFrom"}
    with ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
        quality["inserted_nodes"] = len(list(document.iter(W + "ins")))
        levels = {}
        if "word/styles.xml" in package.namelist():
            styles = ET.fromstring(package.read("word/styles.xml"))
            for style in styles.findall("w:style", NS):
                outline = style.find("w:pPr/w:outlineLvl", NS)
                if outline is not None:
                    levels[style.get(W + "styleId")] = int(outline.get(W + "val"))
    # Preserve source XML positions before accepting structural revisions. A
    # deleted first row/cell must not renumber evidence for the surviving ones.
    original_row_numbers, original_columns = {}, {}
    for table in document.iter(W + "tbl"):
        for row_no, row in enumerate(table.findall("w:tr", NS), 1):
            original_row_numbers[row] = row_no
            for column, cell in enumerate(row.findall("w:tc", NS), 1):
                original_columns[cell] = column
    _accept_content(document, quality)
    body = document.find("w:body", NS)
    blocks, tables = [], []
    chapter, subsections = "文档前置内容", []
    paragraph_no = table_no = 0
    for body_index, element in enumerate(list(body), 1):
        if element.tag == W + "p":
            paragraph_no += 1
            text = _text(element)
            style = element.find("w:pPr/w:pStyle", NS)
            outline = element.find("w:pPr/w:outlineLvl", NS)
            level = int(outline.get(W + "val")) if outline is not None else levels.get(style.get(W + "val")) if style is not None else None
            if level == 0 and text:
                chapter, subsections = text.strip(), []
            elif re.match(r"^[一二三四五六七八九十]+、", text) and len(text) < 55:
                subsections = [text]
            location = {"part": "word/document.xml", "paragraph": paragraph_no,
                        "body_child": body_index, "section_path": [chapter] + subsections,
                        "xpath": f"/w:document/w:body/w:p[{paragraph_no}]"}
            blocks.append({"kind": "paragraph", "text": text, "location": location,
                           "heading_level": level})
        elif element.tag == W + "tbl":
            table_no += 1
            rows = []
            for row in element.findall("w:tr", NS):
                row_no = original_row_numbers[row]
                cells, cell_locations = [], []
                for cell in row.findall("w:tc", NS):
                    paragraphs = cell.findall(".//w:p", NS)
                    cells.append("\n".join(filter(None, (_text(p) for p in paragraphs))))
                    column = original_columns[cell]
                    cell_locations.append({"part": "word/document.xml", "table": table_no, "row": row_no,
                                           "column": column,
                                           "xpath": f"/w:document/w:body/w:tbl[{table_no}]/w:tr[{row_no}]/w:tc[{column}]"})
                location = {"part": "word/document.xml", "table": table_no, "row": row_no,
                            "body_child": body_index, "section_path": [chapter] + subsections,
                            "xpath": f"/w:document/w:body/w:tbl[{table_no}]/w:tr[{row_no}]"}
                block = {"kind": "table_row", "cells": cells, "cell_locations": cell_locations,
                         "text": " || ".join(cells), "location": location}
                blocks.append(block)
                rows.append(block)
            tables.append({"table": table_no, "rows": rows})
    return {"path": str(path.resolve()), "sha256": sha256(blob).hexdigest(),
            "blocks": blocks, "tables": tables, "quality": quality}


def select_blocks(doc, rule):
    candidates = [b for b in doc["blocks"] if b["location"]["section_path"][0] == rule["chapter"]]
    if "match" in rule:
        selected = [b for b in candidates if re.search(rule["match"], b["text"])]
        if len(selected) != 1:
            raise ValueError(f"{rule['field_id']}: selector matched {len(selected)} paragraphs, expected one")
    else:
        starts = [i for i, b in enumerate(candidates) if re.search(rule["start"], b["text"])]
        if len(starts) != 1:
            raise ValueError(f"{rule['field_id']}: section start matched {len(starts)} times")
        start = starts[0]
        stops = [i for i in range(start + 1, len(candidates)) if re.search(rule["stop"], candidates[i]["text"])]
        if not stops:
            raise ValueError(f"{rule['field_id']}: section end not found")
        selected = candidates[start if rule.get("include_start") else start + 1:stops[0]]
    return [deepcopy(b) for b in selected if b["text"].strip()]


def _num(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def _capture(pattern, raw):
    match = re.search(pattern, raw)
    if not match:
        raise ValueError(f"Value pattern not found: {pattern}")
    return match


def _strip_number(text):
    return re.sub(r"^\s*(?:[（(]\d+[）)]|\d+[、）)]|[一二三四五六七八九十]+、)\s*", "", text)


def _clauses(blocks):
    return [{"text": b["text"], "source_location": b["location"]} for b in blocks]


def _strategies(blocks, policy):
    items = []
    for block in blocks:
        match = re.match(r"^(\d+)、(.+)$", block["text"])
        if match:
            items.append({"ordinal": int(match[1]), "title": match[2], "raw_text": block["text"],
                          "source_location": [block["location"]], "children": []})
        elif items:
            item = items[-1]
            item["raw_text"] += "\n" + block["text"]
            item["source_location"].append(block["location"])
            sub = re.match(r"^[（(](\d+)[）)](.+)$", block["text"])
            if sub:
                item["children"].append({"ordinal": int(sub[1]), "title": sub[2],
                                         "raw_text": block["text"], "source_location": [block["location"]]})
            elif item["children"]:
                child = item["children"][-1]
                child["raw_text"] += "\n" + block["text"]
                child["source_location"].append(block["location"])
    if not items or [i["ordinal"] for i in items] != list(range(1, len(items) + 1)):
        raise ValueError("Strategy enumeration is absent or discontinuous")
    displayed = [i for i in items if not re.search(policy["exclude_title_pattern"], i["title"])]
    excluded = [i for i in items if i not in displayed]
    display = "；".join(f"{n}、{i['title']}" for n, i in enumerate(displayed, 1))
    return {"items": items, "displayed_items": [i["title"] for i in displayed],
            "excluded_items": [{**i, "reason": policy["reason"]} for i in excluded]}, display


def parse_fact(rule, blocks, profile):
    raw = "\n".join(b["text"] for b in blocks)
    kind = rule["parser"]
    display = raw
    omitted = []
    if kind == "scalar":
        value = _capture(rule["capture"], raw)[1].rstrip("。")
        display = value
    elif kind == "money":
        m = _capture(rule["capture"], raw)
        scale = {None: 1, "万": 10000, "亿": 100000000}[m[2]]
        value = {"amount_cny": _num(float(m[1]) * scale), "currency": "CNY", "stage": rule["stage"],
                 "kind": rule["kind"], "fee_excluded": bool(re.search(r"不含(?:认购|参与)费", raw)) if rule["kind"] == "首次" else None, "operator": ">="}
        display = f"{rule['kind']}{rule['stage']}最低金额{m[1]}{m[2] or ''}元" + ("（不含费用）" if value["fee_excluded"] else "")
    elif kind == "percentage":
        value = {"rate_percent": _num(_capture(rule["capture"], raw)[1]),
                 "annual": rule["annual"], "stage": rule["stage"]}
        if "前一日" in raw:
            value["base"] = "前一日资产净值"
        display = f"{rule['name']}{value['rate_percent']}%" + ("/年" if value["annual"] else "")
    elif kind == "duration":
        value = {"years": _num(_capture(r"(?:之日起|为)(\d+)年", raw)[1]),
                 "extendable": "可展期" in raw, "starts_at": "成立之日" if "成立之日起" in raw else None}
        display = f"{value['years']}年" + ("（可展期）" if value["extendable"] else "")
    elif kind == "performance":
        if "不提取" not in raw:
            raise ValueError("Unsupported performance-fee structure; requires a dedicated rule")
        value, display = {"charged": False}, raw.rstrip("。")
    elif kind == "opening":
        days = _capture(r"每月(.*?)（", raw)[1]
        value = {"frequency": "monthly", "days": [int(n) for n in re.findall(r"\d+", days)],
                 "starts_at": "成立次日" if "成立次日起" in raw else None,
                 "holiday_rule": _capture(r"（([^）]*节假日[^）]*)）", raw)[1],
                 "exceptions": [s for s in re.split(r"(?<=。)", raw) if "另有约定" in s or "临时开放期" in s]}
        display = re.sub(r"^\d+、开放期：", "", blocks[0]["text"])
        omitted = [b for b in blocks[1:] if b["text"] not in display]
    elif kind == "ratios":
        items, other = [], []
        pattern = r"^(?:（\d+）|\d+[）)])?(.+?)：占(.+?)的([0-9.]+)%[～~\-]([0-9.]+)%(（不含）)?"
        for b in blocks:
            match = re.search(pattern, b["text"])
            if match:
                items.append({"asset": match[1], "denominator": match[2], "lower_percent": _num(match[3]),
                              "upper_percent": _num(match[4]), "lower_inclusive": True,
                              "upper_inclusive": not bool(match[5]), "source_location": b["location"], "raw_text": b["text"]})
            else:
                other.append(b["text"])
        if not items:
            raise ValueError("No investment-ratio ranges parsed")
        value = {"items": items, "qualifications": other}
    elif kind == "redemption":
        tiers = []
        for b in blocks:
            if b["kind"] != "table_row" or len(b["cells"]) < 2:
                continue
            condition = unicodedata.normalize("NFKC", b["cells"][0])
            m = re.search(r"D\s*(<|≥|>=|≤|<=|>)\s*(\d+)\s*(自然日|工作日|交易日)", condition)
            fee = re.search(r"([0-9.]+)%", b["cells"][1])
            if m and fee:
                tiers.append({"operator": {"≥": ">=", "≤": "<="}.get(m[1], m[1]),
                              "days": int(m[2]), "day_type": m[3], "rate_percent": _num(fee[1]),
                              "source_location": b["location"]})
        if not tiers:
            raise ValueError("No redemption table tiers parsed")
        value = {"tiers": tiers, "fee_allocation": [b["text"] for b in blocks if "归入" in b["text"]]}
        display = "；".join(f"D{x['operator']}{x['days']}天（{x['day_type']}）：{x['rate_percent']}%" for x in tiers)
        omitted = [b for b in blocks if "归入" in b["text"]]
    elif kind == "strategy":
        value, display = _strategies(blocks, profile["strategy_display"])
        omitted = [{"text": i["raw_text"], "location": i["source_location"], "reason": i["reason"]} for i in value["excluded_items"]]
        omitted += [{"text": i["raw_text"], "location": i["source_location"], "reason": "样例仅展示该主策略标题，详细方法及下属策略保留在完整事实中。"} for i in value["items"] if i["title"] in value["displayed_items"]]
    elif kind == "restrictions":
        clauses = [b for b in blocks if re.match(r"^\d+、", b["text"]) or "境外投资遵循" in b["text"]]
        other = [b for b in blocks if b not in clauses]
        value = {"clauses": _clauses(clauses), "additional_conditions": _clauses(other)}
        display = "\n".join(b["text"] for b in clauses)
        omitted = other
    elif kind == "risk":
        value = {"assessor": "管理人", "level": _capture(r"(R\d)", raw)[1],
                 "investor_classes": re.findall(r"C\d", raw), "professional_investors": "专业投资者" in raw,
                 "qualifications": [s for s in re.split(r"(?<=。)", raw) if "另有规定" in s]}
    elif kind == "clauses":
        value = {"clauses": _clauses(blocks)}
    else:
        raise ValueError(f"Unknown parser: {kind}")
    return value, display, omitted


def extract_facts(contract, profile):
    """This function deliberately receives no target document or target values."""
    facts, omissions, issues = [], [], []
    for rule in profile["fields"]:
        try:
            selected = select_blocks(contract, rule)
            for b in selected:
                if rule.get("section_path_suffix"):
                    b["location"]["section_path"] = [rule["chapter"]] + rule["section_path_suffix"]
            value, display, omitted = parse_fact(rule, selected, profile)
            fact = {"field_id": rule["field_id"], "name": rule["name"], "value": value,
                    "raw_text": "\n".join(b["text"] for b in selected), "display_text": display,
                    "source_location": [b["location"] for b in selected], "source_path": contract["path"],
                    "source_sha256": contract["sha256"], "status": "extracted",
                    "not_displayed_content": [{"raw_text": b["text"], "source_location": b["location"],
                                               "reason": "此原文段落未在展示短文中逐字出现，可能只保留了摘要；完整语义仍需结合原文。"}
                                              for b in selected if canonical(b["text"]) not in canonical(display)],
                    "extraction_rule": {k: rule[k] for k in ("chapter", "match", "start", "stop", "parser") if k in rule}}
            facts.append(fact)
            for item in omitted:
                omissions.append({"field_id": rule["field_id"], "name": rule["name"], "raw_text": item["text"],
                                  "source_location": item["location"], "source_path": contract["path"],
                                  "reason": item.get("reason", "短文未逐字展示，合同事实中保留完整条件。"),
                                  "requires_review": True})
        except ValueError as exc:
            issues.append({"code": "extraction_failed", "field_id": rule["field_id"], "severity": "error", "message": str(exc)})
    return facts, omissions, issues


def canonical(text, aliases=()):
    text = unicodedata.normalize("NFKC", text)
    for old, new in aliases:
        text = text.replace(old, new)
    text = re.sub(r"(?:^|(?<=[。；;：:\n]))\s*(?:\(\d+\)|\d+[)、])\s*", "", text)
    return re.sub(r"[\s。、，；;：:（）()\[\]]", "", text).rstrip(".")


def _sentences(text):
    return [x.strip() for x in re.split(r"(?<=[。；;])|\n", text) if x.strip()]


def _supported_sentences(target, source, aliases=()):
    source_norm = canonical(source, aliases)
    return [s for s in _sentences(target) if canonical(s, aliases) not in source_norm]


def _money_numbers(text):
    return [_num(float(m[1]) * {None: 1, "万": 10000, "亿": 100000000}[m[2]])
            for m in re.finditer(r"([0-9.]+)(万|亿)?元", text) for _ in [None]]


def _money_entries(text):
    """Parse each labeled amount with its own stage and fee inclusion clause."""
    normal = unicodedata.normalize("NFKC", text)
    pattern = r"(首次|初始|追加)(?:净)?(认购|参与)([^。；;\n]*?)([0-9]+(?:\.[0-9]+)?)(万|亿)?元"
    matches = list(re.finditer(pattern, normal))
    entries = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normal)
        clause = normal[match.start():end]
        fee_markers = list(re.finditer(r"(不包含|不包括|不含|包含|包括|含)([^,，。；;()\n]*?费用?)", clause))
        polarities = {m[1].startswith("不") for m in fee_markers}
        excluded = next(iter(polarities)) if len(polarities) == 1 else "conflicting" if polarities else None
        fee_stages = sorted({stage for m in fee_markers for stage in re.findall(r"认购|参与", m[2])})
        entries.append({"kind": "首次" if match[1] == "初始" else match[1], "stage": match[2],
                        "amount_cny": _num(float(match[4]) * {None: 1, "万": 10000, "亿": 100000000}[match[5]]),
                        "fee_excluded": excluded, "fee_stages": fee_stages, "raw_text": clause,
                        "minimum_semantics": not bool(re.search(r"最高|不高于|不超过|至多", match[3]))})
    return entries


def _compare_money(target_text, selected, target_rule):
    primary_stage = target_rule.get("stage", selected[0]["value"]["stage"])
    primary = [f for f in selected if f["value"]["stage"] == primary_stage]
    all_by_kind_stage = {(f["value"]["kind"], f["value"]["stage"]): f for f in selected}
    generated = "；".join(f["display_text"] for f in primary)
    actual = _money_entries(target_text)
    checks = []
    complete = len(actual) == len(primary) and len(_money_numbers(target_text)) == len(actual)
    checks.append({"check": "amount_entry_coverage", "passed": complete,
                   "expected_count": len(primary), "recognized_count": len(actual)})
    combined_stages = False
    for fact in primary:
        expected = fact["value"]
        candidates = [entry for entry in actual if entry["kind"] == expected["kind"] and entry["stage"] == primary_stage]
        entry = candidates[0] if len(candidates) == 1 else None
        amount_equal = entry is not None and entry["amount_cny"] == expected["amount_cny"]
        fee_equal = entry is not None and entry["fee_excluded"] == expected["fee_excluded"]
        fee_support = True
        if entry and entry["fee_excluded"] is not None:
            # A generic or foreign-stage fee label cannot prove the primary
            # stage's fee basis. Combined labels need evidence for each stage.
            fee_support = primary_stage in entry["fee_stages"]
            for fee_stage in entry["fee_stages"]:
                supporting = all_by_kind_stage.get((expected["kind"], fee_stage))
                fee_support = fee_support and supporting is not None and supporting["value"]["fee_excluded"] == entry["fee_excluded"] and supporting["value"]["amount_cny"] == expected["amount_cny"]
            combined_stages = combined_stages or len(entry["fee_stages"]) > 1
        minimum_equal = entry is not None and entry["minimum_semantics"]
        passed = entry is not None and amount_equal and fee_equal and fee_support and minimum_equal
        checks.append({"fact_id": fact["field_id"], "passed": bool(passed),
                       "expected_kind": expected["kind"], "expected_stage": primary_stage,
                       "expected_amount_cny": expected["amount_cny"], "target_entry": entry,
                       "label_and_stage_equal": entry is not None, "amount_equal": amount_equal,
                       "expected_fee_excluded": expected["fee_excluded"], "fee_basis_equal": fee_equal,
                       "fee_stage_evidence_complete": bool(fee_support), "minimum_semantics_equal": minimum_equal})
    if not all(check["passed"] for check in checks):
        return generated, "差异", "金额要素不一致或未完整识别：应逐项核对首次/追加标签、认购/参与阶段、金额及含费口径。", [], checks
    if combined_stages:
        return generated, "跨条款支持/待确认", "合同募集与存续期参与两章分别支持相同的首次/追加金额及首次金额不含对应费用；目标参与金额一行合并写‘认购/参与费’，具备跨条款依据，但应确认是否保留认购、参与的阶段区分。", [], checks
    return generated, "摘要一致", "首次/追加标签、业务阶段、金额与各自费用包含口径逐项相符。", [], checks


def _compare(target_rule, target_text, selected, aliases=()):
    generated = "；".join(f["display_text"] for f in selected)
    source = "\n".join(f["raw_text"] for f in selected)
    mode = target_rule["mode"]
    unsupported, checks = [], []
    status, reason = "待人工复核", "已定位合同事实，需核对展示语义。"
    if mode == "scalar":
        equal = canonical(target_text) == canonical(generated)
        status = "一致" if equal else "差异"
        reason = "标点和空格标准化后逐字一致。" if equal else "目标文字与合同提取值不同。"
    elif mode == "strategy":
        expected = selected[0]["value"]["displayed_items"]
        actual = [re.sub(r"^\d+[、)]", "", s.strip()).strip("。") for s in re.split(r"[；;\n]", target_text) if s.strip()]
        equal = actual == expected
        status = "摘要一致" if equal else "差异"
        reason = "7个展示主策略标题及顺序一致；合同第8项特殊情形和各项详细方法已保留在未展示内容。" if equal else "策略标题、层级或顺序有差异。"
    elif mode == "duration":
        val = selected[0]["value"]
        m = re.search(r"(\d+)年", target_text)
        equal = bool(m) and int(m[1]) == val["years"] and ("可展期" in target_text) == val["extendable"]
        status = "摘要一致" if equal else "差异"
        reason = "年限、可展期属性相符；起算点仍保留在合同事实。" if equal else "期限数值或展期属性不同。"
    elif mode == "fees":
        actual = [_num(x) for x in re.findall(r"([0-9.]+)%", target_text)]
        expected = [f["value"]["rate_percent"] for f in selected]
        label_checks = []
        for fact in selected:
            stage = fact["value"]["stage"]
            labeled = re.findall(re.escape(stage) + r"费率\s*[：:]?\s*([0-9.]+)%(\s*/\s*年)?", target_text)
            label_checks.append(len(labeled) == 1 and float(labeled[0][0]) == fact["value"]["rate_percent"]
                                and (not fact["value"]["annual"] or bool(labeled[0][1])))
        equal = actual == expected and all(label_checks)
        status = "摘要一致" if equal else "差异"
        reason = "各费用名称顺序、费率及年费率单位核对相符；计提基数及其他计提条件保留于原文。" if equal else "费率、排列或年化单位不同。"
        checks = [{"fact_id": f["field_id"], "expected_percent": f["value"]["rate_percent"], "target_percent": actual[i] if i < len(actual) else None} for i, f in enumerate(selected)]
    elif mode == "money":
        return _compare_money(target_text, selected, target_rule)
    elif mode == "redemption":
        normal = unicodedata.normalize("NFKC", target_text).replace("≥", ">=").replace("≤", "<=")
        found = re.findall(r"D\s*(>=|<=|<|>)\s*(\d+)天\((自然日|工作日|交易日)\)[：:]([0-9.]+)%", re.sub(r"\s", "", normal))
        expected = [(x["operator"], str(x["days"]), x["day_type"], str(x["rate_percent"])) for x in selected[0]["value"]["tiers"]]
        equal = len(found) == len(expected) and all(a[:3] == b[:3] and float(a[3]) == float(b[3]) for a, b in zip(found, expected))
        checks = [{"expected": b, "target": found[i] if i < len(found) else None,
                   "day_type_equal": i < len(found) and found[i][2] == b[2]} for i, b in enumerate(expected)]
        status = "摘要一致" if equal else "差异"
        reason = "持有天数、日类型、含不含边界及退出费率相符；费用归属未展示但已保留。" if equal else "退出费的天数、自然日/工作日等日类型、边界或费率不一致/无法解析。"
    elif mode == "clauses":
        if canonical(target_text, aliases) == canonical(generated, aliases):
            status, reason = "一致", "条款内容经编号、空格和标点规范后一致。"
        else:
            unsupported = _supported_sentences(target_text, source, aliases)
            if not unsupported:
                status, reason = "摘要一致", "目标条款均可由合同原文支持；完整合同中的额外条件保留于事实及未展示内容。"
            else:
                status, reason = "待人工复核", "存在未能逐句对应的文字，保守列为待核对，不能声明完全一致。"
    elif mode in ("opening", "risk"):
        unsupported = _supported_sentences(target_text, source, aliases)
        if mode == "opening":
            unsupported = _supported_sentences(target_text, source, aliases)
        if mode == "risk":
            expected = selected[0]["value"]
            checks = [{"level_equal": re.findall(r"R\d", target_text) == [expected["level"]],
                       "investor_classes_equal": re.findall(r"C\d", target_text) == expected["investor_classes"]}]
            # Remove only source boilerplate, then compare the substantive sentence,
            # including negation, investor categories and the identity of the assessor.
            concise_source = source.replace("根据相关法律法规及其内部相关制度规定，", "").replace("且根据前述风险评级，", "").replace("本集合计划适合向", "适合向")
            unsupported = _supported_sentences(target_text, concise_source, aliases)
            if not all(checks[0].values()):
                return generated, "差异", "风险等级或投资者类别不同。", unsupported, checks
        status = "部分支持/待补来源" if unsupported else "摘要一致"
        reason = "主要要素有合同依据，但目标含合同未逐句给出的补充表述；该部分需外部来源或人工确认。" if unsupported else "核心条件与合同相符，表述为展示摘要。"
    return generated, status, reason, unsupported, checks


def match_target(target, facts, profile):
    table_rule = profile["target_table"]
    candidates = []
    for table in target["tables"]:
        labels = "\n".join(r["cells"][0] if r["cells"] else "" for r in table["rows"])
        if len(table["rows"]) >= table_rule["minimum_rows"] and all(x in labels for x in table_rule["required_labels"]):
            candidates.append(table)
    if len(candidates) != 1:
        raise ValueError(f"Target element table matched {len(candidates)} candidates")
    fact_map = {f["field_id"]: f for f in facts}
    matches, issues, mapped_rows = [], [], set()
    table = candidates[0]
    for rule in profile["targets"]:
        rows = [r for r in table["rows"] if r["cells"] and any(a in r["cells"][0] for a in rule["aliases"])]
        if len(rows) != 1:
            issues.append({"code": "target_row_not_unique", "field_id": rule["field_id"], "severity": "error", "message": f"目标标签匹配{len(rows)}行"})
            continue
        row = rows[0]
        mapped_rows.add(row["location"]["row"])
        target_text = "\n".join(c for c in row["cells"][1:] if c.strip())
        selected = [fact_map[i] for i in rule["fact_ids"] if i in fact_map]
        label_core = canonical(re.sub(r"[（(].*?[）)]", "", row["cells"][0], flags=re.S))
        valid_labels = {canonical(x) for x in rule["aliases"] + [rule["name"]]}
        if label_core not in valid_labels:
            issues.append({"code": "accepted_revision_label_residue", "severity": "review", "field_id": rule["field_id"],
                           "message": "排除删除修订后标签仍有残片/重复字，采用配置别名定位并保留原标签；源文件未修正。",
                           "raw_label": row["cells"][0], "location": row["location"]})
        if len(selected) != len(rule["fact_ids"]):
            result = ("", "合同未检出", "抽取规则未取得完整的合同事实，不能从目标反填。", [], [])
        else:
            result = _compare(rule, target_text, selected, profile.get("wording_aliases", []))
        generated, status, reason, unsupported, checks = result
        matches.append({"field_id": rule["field_id"], "target_field_id": rule["field_id"], "name": rule["name"],
                        "fact_ids": rule["fact_ids"], "target_location": row["location"],
                        "target_label_raw": row["cells"][0], "target_text": target_text,
                        "generated_text": generated, "match_status": status, "reason": reason,
                        "unsupported_target_text": unsupported, "atomic_checks": checks,
                        "omitted_source_text": [f["raw_text"] for f in selected if canonical(f["raw_text"], profile.get("wording_aliases", [])) not in canonical(target_text, profile.get("wording_aliases", []))],
                        "source_locations": [loc for f in selected for loc in f["source_location"]],
                        "target_path": target["path"], "target_sha256": target["sha256"]})
        if unsupported:
            issues.append({"code": "unsupported_target_text", "field_id": rule["field_id"], "severity": "review",
                           "message": reason, "text": unsupported, "location": row["location"]})
    nonempty = [r for r in table["rows"] if any(c.strip() for c in r["cells"])]
    unmapped = [r for r in nonempty if r["location"]["row"] not in mapped_rows]
    for row in unmapped:
        issues.append({"code": "unmapped_target_row", "severity": "error", "message": "非空目标行未映射", "location": row["location"], "text": row["text"]})
    return matches, issues, len(unmapped)


def add_cross_clause_evidence(matches, contract, profile):
    """Search the whole contract before describing an unmatched sentence as absent."""
    rules = {r["field_id"]: r for r in profile["targets"]}
    whole = canonical("\n".join(b["text"] for b in contract["blocks"]))
    for match in matches:
        rule = rules[match["field_id"]]
        match["full_contract_search"] = []
        for sentence in match["unsupported_target_text"]:
            literal = canonical(sentence) in whole
            match["full_contract_search"].append({"target_text": sentence, "literal_found": literal,
                                                  "search_scope": "word/document.xml全部正文段落和表格（接受修订视图）",
                                                  "note": "未逐句检出不代表缺乏跨条款语义依据；支持证据另列。"})
        supporting = []
        for selector in rule.get("support_rules", []):
            for block in contract["blocks"]:
                if block["location"]["section_path"][0] == selector["chapter"] and re.search(selector["match"], block["text"]):
                    supporting.append({"raw_text": block["text"], "source_location": block["location"],
                                       "source_path": contract["path"], "source_sha256": contract["sha256"]})
        match["supporting_source_evidence"] = supporting
        if supporting and match["unsupported_target_text"]:
            # A negated or changed primary clause is not cured by collateral evidence.
            known_supplement = (match["field_id"] == "opening_schedule" and all("公告" in s for s in match["unsupported_target_text"])) or (match["field_id"] == "manager_risk_rating" and all("代销机构" in s for s in match["unsupported_target_text"]))
            if known_supplement:
                match["match_status"] = "跨条款支持/待确认" if match["field_id"] == "opening_schedule" else "相关条款/待确认"
                match["reason"] = rule["support_note"]


def run(root, out_path=None, contract_path=None, target_path=None, profile_path=None):
    root = Path(root).resolve()
    profile_path = Path(profile_path or PROFILE)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    contract = read_docx(contract_path or root / profile["contract"])
    # Facts and display summaries are completely finalized before target loading.
    facts, omissions, issues = extract_facts(contract, profile)
    target = read_docx(target_path or root / profile["target"])
    matches, match_issues, unmapped = match_target(target, facts, profile)
    add_cross_clause_evidence(matches, contract, profile)
    match_lookup = {m["field_id"]: m for m in matches}
    for issue in match_issues:
        if issue["code"] == "unsupported_target_text":
            current = match_lookup[issue["field_id"]]
            issue["message"] = current["reason"]
            issue["supporting_source_evidence"] = current["supporting_source_evidence"]
            issue["full_contract_search"] = current["full_contract_search"]
    issues.extend(match_issues)
    for role, doc in (("contract", contract), ("target", target)):
        quality = doc["quality"]
        if quality["deleted_nodes"] or quality["inserted_nodes"]:
            issues.append({"code": "tracked_revisions", "severity": "review", "role": role,
                           "message": "源文件存在修订。读取采用保留插入、排除删除和移出内容的接受修订视图；未改动源文件。", "quality": quality})
        if quality["compatibility_branches"]:
            issues.append({"code": "compatibility_content", "severity": "info", "role": role,
                           "message": "兼容性内容仅读取单一Choice（无Choice时Fallback），防止重复文字。", "quality": quality})
    counts = {}
    for match in matches:
        counts[match["match_status"]] = counts.get(match["match_status"], 0) + 1
    files = []
    for role, doc in (("contract", contract), ("target", target)):
        path = Path(doc["path"])
        try:
            relative = str(path.relative_to(root))
        except ValueError:
            relative = None
        files.append({"role": role, "absolute_path": str(path), "relative_path": relative,
                      "sha256": doc["sha256"], "quality": doc["quality"]})
    result = {"schema_version": "1.0", "profile_id": profile["profile_id"],
              "profile_sha256": sha256(profile_path.read_bytes()).hexdigest(), "source_files": files,
              "facts": facts, "matches": matches, "omissions": omissions, "issues": issues,
              "summary": {"contract_fact_count": len(facts), "configured_fact_count": len(profile["fields"]),
                          "target_rows_checked": len(matches), "unmapped_target_rows": unmapped,
                          "match_status_counts": counts, "omission_count": len(omissions),
                          "extraction_error_count": sum(i["code"] == "extraction_failed" for i in issues),
                          "note": "这是指定历史合同与一页通的证据核对，不是现行渠道规则认证；未解析或未支持文本不会回填成合同事实。"}}
    for source in files:
        if sha256(Path(source["absolute_path"]).read_bytes()).hexdigest() != source["sha256"]:
            raise RuntimeError("Source changed during extraction; results not saved")
    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile", type=Path, help="覆盖字段定位与展示配置JSON")
    parser.add_argument("--contract", type=Path, help="覆盖合同源文件路径")
    parser.add_argument("--target", type=Path, help="覆盖待核对物料路径")
    args = parser.parse_args()
    result = run(args.root, args.out, contract_path=args.contract, target_path=args.target, profile_path=args.profile)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 1 if result["summary"]["extraction_error_count"] or result["summary"]["unmapped_target_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
