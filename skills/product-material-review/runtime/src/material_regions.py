"""Read-only OOXML material slots scoped to locally evidenced product regions.

Product names in tables and local headings are evidence; filenames and cover
pages are not. The original trees are never normalized or rewritten, so every
slot's XPath points directly at a paragraph in the source ZIP member. Unknown
identities and conflicting/combined identities fail closed for editing.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import posixpath
import re
import unicodedata
from zipfile import ZipFile

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
REVERSE_NS = {uri: prefix for prefix, uri in NS.items()}
CONFIG = Path(__file__).resolve().parents[1] / "config"
NAME_LABELS = {"产品名称", "产品全称", "信托名称", "信托计划名称", "基金名称", "计划名称", "资产管理计划名称", "产品简称"}
SUFFIX = r"(?:集合资金信托计划|集合资产信托计划|集合资产管理计划|单一资产管理计划|资产管理信托|证券投资基金|集合信托计划)"
NUMBER = re.compile(r"([0-9０-９]+|[一二三四五六七八九十百零〇]+|[XYZxyz])\s*(号|期)")
CONTINUED = re.compile(r"(?:续表|接上页|续页|[（(]续[）)]|要素.*续|条款.*续)")


def _normalize(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return re.sub(r"[\s·•\-—_：:、，,。;；()（）【】\[\]《》]", "", text)


def _xml(raw):
    return etree.fromstring(raw, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _q(prefix, local):
    return "{" + NS[prefix] + "}" + local


def _visible(node):
    """Accepted content, excluding removed rows/cells and duplicate MC branches."""
    for ancestor in [node, *node.iterancestors()]:
        if ancestor.tag in {_q("w", "del"), _q("w", "moveFrom")}:
            return False
        if ancestor.tag == _q("w", "tr") and ancestor.find("w:trPr/w:del", NS) is not None:
            return False
        if ancestor.tag == _q("w", "tc") and ancestor.find("w:tcPr/w:cellDel", NS) is not None:
            return False
        if ancestor.tag in {_q("mc", "Choice"), _q("mc", "Fallback")}:
            parent = ancestor.getparent()
            selected = parent.find("mc:Choice", NS)
            if selected is None:
                selected = parent.find("mc:Fallback", NS)
            if ancestor is not selected:
                return False
    return True


def _text(node):
    chunks = []
    for descendant in node.iter():
        if not _visible(descendant):
            continue
        if descendant.tag in {_q("w", "t"), _q("a", "t")}:
            chunks.append(descendant.text or "")
        elif descendant.tag in {_q("w", "tab"), _q("a", "tab")}:
            chunks.append("\t")
        elif descendant.tag in {_q("w", "br"), _q("w", "cr"), _q("a", "br")}:
            chunks.append("\n")
    return "".join(chunks)


def _xpath(node):
    components = []
    for current in reversed([node, *node.iterancestors()]):
        name = etree.QName(current)
        prefix = REVERSE_NS.get(name.namespace)
        tag = f"{prefix}:{name.localname}" if prefix else f"*[local-name()='{name.localname}']"
        index = 1 + sum(sibling.tag == current.tag for sibling in current.itersiblings(preceding=True))
        components.append(f"{tag}[{index}]")
    return "/" + "/".join(components)


def _load_dictionary(dictionary):
    if dictionary is None:
        dictionary = []
        for filename in ("field_dictionary.json", "contract_field_extensions.json", "field_dictionary_extensions.json"):
            path = CONFIG / filename
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                dictionary.extend(data if isinstance(data, list) else data.get("fields", data.get("field_dictionary", [])))
    if isinstance(dictionary, dict):
        dictionary = dictionary.get("fields", dictionary.get("field_dictionary", []))
    labels = {}
    for field in dictionary:
        for label in [field.get("name", ""), *field.get("aliases", [])]:
            if label:
                labels.setdefault(_normalize(label), []).append(field["field_id"])
    return labels


def _numbers(text):
    return [(unicodedata.normalize("NFKC", n).casefold(), unit) for n, unit in NUMBER.findall(text)]


def _explicit_names(text):
    """Return None when absent, [] for a blank identity label, else literal names.

    Explicit business labels are identity evidence even when a short name has
    no familiar legal suffix, institution prefix, or issue number.
    """
    pattern = r"^[ \t]*(?:" + "|".join(re.escape(s) for s in sorted(NAME_LABELS, key=len, reverse=True)) + r")[ \t]*[：:][ \t]*(.*)$"
    matches = re.findall(pattern, text, flags=re.M)
    return list(dict.fromkeys(value.strip() for value in matches if value.strip())) if matches else None


def _names(text, allow_short=False):
    """Extract explicit names only, never names mentioned inside long clauses."""
    text = text.strip()
    explicit = _explicit_names(text)
    if explicit is not None:
        return explicit
    if not text or len(text) > 240:
        return []
    # Series definitions (X=1,2,...) are part of an identity rather than a list of names.
    pieces = re.split(r"(?<=[划金托])\s*[、；;\n]\s*(?=.{2,80}(?:信托|计划|基金))", text)
    names = []
    for piece in pieces:
        piece = re.sub(r"^(?:产品名称|产品全称|信托名称|信托计划名称|计划名称)\s*[：:]\s*", "", piece).strip()
        piece = re.sub(r"^(?:拟投资标的(?:产品)?|投资标的(?:产品)?|底层产品|标的产品)\s*[：:—\-]+\s*", "", piece)
        piece = re.sub(r"(?:产品要素(?:介绍)?|要素表|基本要素|[（(]续[）)])$", "", piece).strip(" ：:-—")
        if re.search(r"截至|净值|年化收益|累计增长|过往投资|不代表|不预示|风险揭示|本材料", piece):
            continue
        if re.fullmatch(SUFFIX, piece):
            continue
        if re.search(SUFFIX, piece) and len(piece) < 160:
            # Titles are allowed, but explanatory investment sentences are not identity headings.
            if not re.search(r"(?:投资于|包括|本材料|本产品|风险揭示|历史业绩|不代表|为准|管理人|受托人)", piece):
                names.append(piece)
        elif allow_short and re.search(r"(?:信托|君享|鑫源|鼎安)", piece) and NUMBER.search(piece) and len(piece) < 100:
            names.append(piece)
    return list(dict.fromkeys(names))


def _compare_name(name, target):
    canonical = target["name"]
    name_norm, canonical_norm = _normalize(name), _normalize(canonical)
    if name_norm == canonical_norm:
        return "target"
    original_nums, target_nums = _numbers(name), _numbers(canonical)
    # Broad aliases may not erase period/issue conflicts.
    by_unit = {}
    for number, unit in target_nums:
        by_unit.setdefault(unit, set()).add(number)
    for number, unit in original_nums:
        if unit in by_unit and number not in by_unit[unit]:
            if number in {"x", "y", "z"} or by_unit[unit] & {"x", "y", "z"}:
                return "ambiguous"
            return "other"
    if target_nums and not original_nums:
        return "ambiguous" if any(_normalize(a) in name_norm for a in target["aliases"] if a) else "other"
    for alias in target["aliases"]:
        alias_norm = _normalize(alias)
        if alias_norm and name_norm == alias_norm:
            return "target"
    # A full target plus a presentation label is safe; arbitrary substring matching is not.
    stripped = re.sub(r"(?:产品要素介绍|产品要素|基本要素|要素表|续表|续)$", "", name_norm)
    if stripped == canonical_norm:
        return "target"
    # Same product core with a changed legal wrapper is not silently accepted.
    name_core = re.sub(SUFFIX, "", name_norm)
    target_core = re.sub(SUFFIX, "", canonical_norm)
    if name_core == target_core:
        if not re.search(SUFFIX, name_norm) and original_nums:
            return "target"
        return "ambiguous"
    return "other"


def _subject_name(text):
    """A product-led performance sentence defines its own subject, not its owner."""
    compact = re.sub(r"\s+", "", text)
    match = re.match(r"^((?:国泰君安|国泰海通|平安信托|中信信托|中粮信托|外贸信托|海通)[^，。；：:]{2,90}?(?:[0-9]+|[一二三四五六七八九十]+)号(?:" + SUFFIX + r")?)(?:产品)?(?:截至|成立于|成立以来|的历史业绩|历史业绩|过往投资)", compact)
    return match.group(1) if match else None


def _bounds(shape):
    xf = shape.find("p:xfrm", NS)
    if xf is None:
        xf = shape.find("p:spPr/a:xfrm", NS)
    if xf is None:
        return None
    off, ext = xf.find("a:off", NS), xf.find("a:ext", NS)
    if off is None or ext is None:
        return None
    return {"x": int(off.get("x", 0)), "y": int(off.get("y", 0)), "width": int(ext.get("cx", 0)), "height": int(ext.get("cy", 0))}


class _Analyzer:
    def __init__(self, target, labels, keep_empty=False):
        self.target, self.labels = target, labels
        self.keep_empty = keep_empty
        self.regions, self.slots, self.issues = [], [], []
        self.seen_slots = set()

    def field(self, label):
        clean = _normalize(label)
        if clean in {_normalize(s) for s in NAME_LABELS}:
            return "product_name"
        matches = list(dict.fromkeys(self.labels.get(clean, [])))
        return matches[0] if len(matches) == 1 else None

    def region(self, names, reason, location, title="", inherited=None, conflict=False):
        names = list(dict.fromkeys(name for name in names if name))
        classes = {_compare_name(n, self.target) for n in names}
        status = next(iter(classes)) if len(names) == 1 and len(classes) == 1 else "ambiguous"
        if conflict:
            status = "ambiguous"
        region = {"region_id": f"region_{len(self.regions) + 1:04d}", "product_names": names,
                  "status": status, "reason": reason, "locations": [location], "title": title,
                  "inherited_context": inherited or []}
        self.regions.append(region)
        if status == "ambiguous":
            self.issues.append({"code": "ambiguous_product_region", "region_id": region["region_id"],
                                "message": reason, "location": location})
        return region

    def slot(self, paragraph, region, label, field, location, shared=False, field_context=None, content_role="value"):
        text = _text(paragraph)
        if (not text.strip() and not self.keep_empty) or not _visible(paragraph):
            return
        if not text.strip() and content_role != "layout_blank":
            content_role = "empty_value"
        if content_role == "layout_blank":
            field = None
        path = _xpath(paragraph)
        key = (location["part"], path)
        if key in self.seen_slots:
            return
        self.seen_slots.add(key)
        status = "ambiguous" if shared else region["status"]
        loc = {**location, "xpath": path}
        self.slots.append({"slot_id": f"slot_{len(self.slots) + 1:05d}", "region_id": region["region_id"],
                           "field_id": field, "label": label, "text": text, "part": loc["part"],
                           "xpath": path, "editable": status == "target" and bool(text.strip()), "product_status": status,
                           "location": loc, "title": region["title"],
                           "inherited_context": [*region["inherited_context"], *(field_context or [])],
                           "content_role": content_role,
                           "shared_scope": shared,
                           "identity_evidence": region["product_names"]})
        if content_role == "empty_value":
            self.issues.append({"code": "empty_template_value", "region_id": region["region_id"],
                                "slot_id": self.slots[-1]["slot_id"], "location": loc,
                                "message": "模板存在空白值格，须补充目标合同内容；当前文本替换器不能直接填入空段落。"})

    def local_title(self, obj, titles):
        candidates = []
        for title in titles:
            if obj.get("shape_id") == title.get("shape_id"):
                continue
            a, b = obj.get("bounds"), title.get("bounds")
            if a and b:
                overlap = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
                if overlap <= 0 or b["y"] > a["y"]:
                    continue
                score = (a["y"] - b["y"], -overlap / max(1, min(a["width"], b["width"])))
            else:
                score = (0, 0)
            candidates.append((score, title))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        best = [t for score, t in candidates if score == candidates[0][0]]
        if len(best) == 1:
            return best[0]
        return {"text": " | ".join(t["text"] for t in best), "names": list(dict.fromkeys(n for t in best for n in t["names"])),
                "location": best[0]["location"], "explicit_identity": any(t.get("explicit_identity") for t in best)}

    def cells(self, table, location, word=False):
        pre = "w" if word else "a"
        rows = []
        for ri, row in enumerate(table.findall(f"{pre}:tr", NS), 1):
            if not _visible(row):
                continue
            cells, grid_col = [], 1
            for ci, cell in enumerate(row.findall(f"{pre}:tc", NS), 1):
                span_node = cell.find("w:tcPr/w:gridSpan", NS) if word else None
                span = int(span_node.get(_q("w", "val"), "1")) if span_node is not None else int(cell.get("gridSpan", "1"))
                visible_paras = [p for p in cell.findall(f".//{pre}:p", NS) if _visible(p)]
                paras = [p for p in visible_paras if _text(p).strip()]
                if self.keep_empty and not paras:
                    paras = visible_paras[:1]
                merge_node = cell.find("w:tcPr/w:vMerge", NS) if word else None
                merged = cell.get("hMerge") == "1" or cell.get("vMerge") == "1" or (merge_node is not None and merge_node.get(_q("w", "val")) != "restart")
                if _visible(cell) and not merged:
                    text = "\n".join(_text(p) for p in paras)
                    open_edges = []
                    if word and not text.strip():
                        for edge in ("top", "bottom", "right"):
                            border = cell.find(f"w:tcPr/w:tcBorders/w:{edge}", NS)
                            if border is not None and border.get(_q("w", "val")) in {"nil", "none"}:
                                open_edges.append(edge)
                    cells.append({"text": text, "paragraphs": paras, "column": ci,
                                  "grid_column": grid_col, "span": span, "field": self.field(text),
                                  "open_edges": open_edges,
                                  "location": {**location, "row": ri, "column": ci, "grid_column": grid_col,
                                               "column_span": span, "xpath": _xpath(cell)}})
                grid_col += span if word else 1
            rows.append({"number": ri, "cells": cells})
        return rows

    def table(self, table, location, title=None, previous=None, word=False):
        pre = "w" if word else "a"
        grid = table.findall(f"{pre}:tblGrid/{pre}:gridCol", NS)
        columns = len(grid) or max((len(r.findall(f"{pre}:tc", NS)) for r in table.findall(f"{pre}:tr", NS)), default=0)
        location = {**location, "table_columns": columns}
        rows = self.cells(table, location, word)
        heading = title["text"] if title else ""
        title_names = title.get("names", []) if title else []
        # An identity row may contain one label with multiple product columns,
        # or repeated label/value pairs. Blank merged placeholders are ignored.
        anchors = []
        for index, row in enumerate(rows):
            cells = row["cells"]
            pairs = []
            for i, cell in enumerate(cells):
                if cell["field"] != "product_name":
                    continue
                for value in cells[i + 1:]:
                    if value["field"]:
                        break
                    if value["text"].strip():
                        pairs.append((value, _names(value["text"], True) or [value["text"].strip()]))
            # A heading row with named product columns need not repeat 产品名称.
            if not pairs and index == 0:
                pairs = [(cell, _names(cell["text"], True)) for cell in cells[1:] if _names(cell["text"], True)]
                if len(pairs) < 2:
                    pairs = []
            if pairs:
                anchors.append((index, pairs))
        table_regions = []
        if not anchors:
            inherited = []
            names, reason = title_names, "same_page_local_title" if title_names else "no_local_product_identity"
            if title and title.get("explicit_identity") and not names:
                reason = "explicit_product_name_missing"
            if not names and reason != "explicit_product_name_missing" and CONTINUED.search(heading) and previous:
                eligible = [r for r in previous if r["status"] != "ambiguous" and r["locations"][0].get("table_columns") == columns]
                unique = {tuple(r["product_names"]) for r in eligible}
                if len(previous) == len(eligible) and len(unique) == 1:
                    names = eligible[0]["product_names"]
                    inherited = [{"region_id": r["region_id"], "location": r["locations"][0], "method": "explicit_adjacent_continuation"} for r in eligible]
                    reason = "explicit_adjacent_continuation"
                else:
                    reason = "continuation_has_multiple_or_ambiguous_predecessors"
            region = self.region(names, reason, location, heading, inherited)
            table_regions.append(region)
            self.table_rows(rows, [region], None)
        else:
            if anchors[0][0] > 0:
                region = self.region([], "rows_precede_product_identity", location, heading)
                table_regions.append(region)
                self.table_rows(rows[:anchors[0][0]], [region], None)
            for k, (start, pairs) in enumerate(anchors):
                end = anchors[k + 1][0] if k + 1 < len(anchors) else len(rows)
                region_columns = []
                for cell, names in pairs:
                    conflict = bool(title_names) and title.get("evidence_type") != "prose_subject" and len(title_names) == 1 and len(pairs) == 1 and not any(_normalize(n) == _normalize(title_names[0]) for n in names)
                    reason = "local_title_conflicts_with_table_name" if conflict else "explicit_table_product_name"
                    region = self.region(names + title_names if conflict else names, reason,
                                         cell["location"], heading)
                    table_regions.append(region)
                    region_columns.append((cell["grid_column"], region))
                self.table_rows(rows[start:end], [r for _, r in region_columns], region_columns)
        return table_regions

    def table_rows(self, rows, regions, region_columns):
        for row in rows:
            cells = row["cells"]
            if not cells:
                continue
            # Every recognized label starts a new label/value group. The first
            # cell is retained as an unclassified label when dictionary coverage
            # is incomplete, so unresolved material requirements remain visible.
            label_indices = [i for i, c in enumerate(cells) if c["field"] is not None]
            if not label_indices:
                label_indices = [0]
            if label_indices[0] > 0:
                label_indices.insert(0, 0)
            for j, label_index in enumerate(label_indices):
                label_cell = cells[label_index]
                stop = label_indices[j + 1] if j + 1 < len(label_indices) else len(cells)
                for cell in cells[label_index + 1:stop]:
                    if len(regions) == 1:
                        owner, shared = regions[0], False
                    else:
                        columns = [(c, r) for c, r in region_columns if cell["grid_column"] <= c < cell["grid_column"] + cell["span"]]
                        if len(columns) == 1:
                            owner, shared = columns[0][1], False
                        else:
                            left = [(c, r) for c, r in region_columns if c <= cell["grid_column"]]
                            owner = left[-1][1] if left else regions[0]
                            shared = len(columns) > 1 or not left
                    location, blank_role = cell["location"], "value"
                    filled_peers = [c for c in cells[label_index + 1:stop] if c is not cell and c["text"].strip()]
                    if self.keep_empty and not cell["text"].strip() and cell["open_edges"] == ["top", "bottom", "right"] and filled_peers and len(regions) == 1:
                        blank_role = "layout_blank"
                        location = {**location, "layout_blank_evidence": {
                            "method": "open_outer_edges_beside_populated_value",
                            "open_edges": cell["open_edges"], "filled_peer_locations": [c["location"] for c in filled_peers]}}
                    self.paragraphs(cell["paragraphs"], owner, location, shared,
                                    label_cell["text"], label_cell["field"], label_cell["location"], blank_role)

    def paragraphs(self, paragraphs, region, location, shared=False, default_label="", default_field=None, label_location=None, default_role="value"):
        state = {"label": default_label, "field": default_field, "location": label_location}
        for paragraph in paragraphs:
            text = _text(paragraph)
            match = re.match(r"^\s*([^：:\n]{1,50})\s*[：:]", text)
            role = default_role
            own_field = self.field(match.group(1)) if match else None
            if match and own_field:
                state = {"label": match.group(1), "field": own_field,
                         "location": {**location, "xpath": _xpath(paragraph)}}
                if not text[match.end():].strip():
                    role = "label_only"
            context = []
            if state["field"] and state["location"] and (not match or own_field is None):
                context = [{"method": "field_label_within_text_object", "field_id": state["field"],
                            "label": state["label"], "location": state["location"]}]
            self.slot(paragraph, region, state["label"], state["field"], location, shared, context, role)

    def paragraph(self, paragraph, region, location):
        text = _text(paragraph)
        label, field = "", None
        # Prefix matching requires a delimiter; prose merely mentioning a fee
        # does not become a management-fee replacement slot.
        match = re.match(r"^\s*([^：:\n]{1,50})\s*[：:]", text)
        if match:
            label = match.group(1)
            field = self.field(label)
        self.slot(paragraph, region, label, field, location)


def _slide_parts(package):
    """Follow presentation relationships, as slide filenames are not page order."""
    if "ppt/presentation.xml" in package.namelist() and "ppt/_rels/presentation.xml.rels" in package.namelist():
        rels = _xml(package.read("ppt/_rels/presentation.xml.rels"))
        targets = {r.get("Id"): posixpath.normpath(posixpath.join("ppt", r.get("Target", ""))) for r in rels if r.get("TargetMode") != "External"}
        presentation = _xml(package.read("ppt/presentation.xml"))
        ordered = [targets.get(slide.get(_q("r", "id"))) for slide in presentation.findall("p:sldIdLst/p:sldId", NS)]
        if ordered and all(part in package.namelist() for part in ordered):
            return ordered
    return sorted((n for n in package.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))


def _analyze_pptx(package, analyzer):
    previous = []
    embedded_names = []
    roots = {"国泰君安", "国泰海通", "平安信托", "中信信托", "中粮信托", "外贸信托", "海通", analyzer.target["name"][:4]}
    embedded_pattern = re.compile("(?:" + "|".join(re.escape(x) for x in sorted(roots, key=len, reverse=True)) + r")[^，,。；;：:<>《》“”「」]{0,90}?" + SUFFIX)
    for page, part in enumerate(_slide_parts(package), 1):
        root = _xml(package.read(part))
        for paragraph in root.findall(".//a:p", NS):
            paragraph_text = _text(paragraph)
            embedded_names.extend(n for n in embedded_pattern.findall(re.sub(r"\s+", "", paragraph_text)) if _numbers(n))
            subject = _subject_name(paragraph_text)
            if subject:
                embedded_names.append(subject)
        tables, text_shapes, titles = [], [], []
        for shape in root.xpath("//p:sp | //p:graphicFrame", namespaces=NS):
            if not _visible(shape):
                continue
            id_node = shape.find(".//p:cNvPr", NS)
            shape_id = id_node.get("id") if id_node is not None else None
            location = {"part": part, "page": page, "shape_id": shape_id,
                        "shape_name": id_node.get("name") if id_node is not None else None,
                        "xpath": _xpath(shape)}
            obj = {"shape": shape, "shape_id": shape_id, "bounds": _bounds(shape), "location": location}
            found = shape.findall(".//a:tbl", NS)
            if found:
                for table in found:
                    tables.append({**obj, "table": table})
            else:
                paragraphs = [p for p in shape.findall(".//a:p", NS) if _visible(p) and _text(p).strip()]
                text = "\n".join(_text(p) for p in paragraphs)
                obj.update(text=text, paragraphs=paragraphs, explicit_identity=_explicit_names(text) is not None)
                text_shapes.append(obj)
                subject = _subject_name(text)
                if subject:
                    obj["subject_name"] = subject
                if text and (len(text) < 180 or obj["explicit_identity"]):
                    names = _names(text, True)
                    placeholder = shape.find(".//p:ph", NS)
                    is_title = placeholder is not None and placeholder.get("type", "title") in {"title", "ctrTitle"}
                    if names or is_title or obj["explicit_identity"] or re.search(r"产品要素|基本要素|续表|主要条款", text):
                        titles.append({**obj, "names": names})
        current = []
        for i, obj in enumerate(tables, 1):
            location = {**obj["location"], "table": i, "bounds": obj["bounds"]}
            current.extend(analyzer.table(obj["table"], location, analyzer.local_title(obj, titles), previous))
        for obj in text_shapes:
            title = next((t for t in titles if t["shape"] is obj["shape"] and (t["names"] or t.get("explicit_identity"))), None)
            if title is None:
                title = analyzer.local_title(obj, titles)
            # Ordinary unscoped prose is retained as an ambiguous region, making
            # placeholders reviewable without granting a global editing scope.
            if not obj["paragraphs"]:
                continue
            names = title["names"] if title else []
            if obj.get("subject_name"):
                names = [obj["subject_name"]]
            reason = "same_page_local_title" if names else "unscoped_slide_text"
            if title and title.get("explicit_identity"):
                reason = "explicit_inline_product_name" if names else "explicit_product_name_missing"
            region = analyzer.region(names, reason,
                                     obj["location"], title["text"] if title else "")
            analyzer.paragraphs(obj["paragraphs"], region, obj["location"])
        previous = current
    # A full-deck context requires actual body-table identity evidence and an
    # absence of competing local identities. A cover by itself never grants it.
    body_targets = [r for r in analyzer.regions if r["reason"] == "explicit_table_product_name" and r["status"] == "target"]
    competitors = [r for r in analyzer.regions if (r["product_names"] and r["status"] != "target") or r["reason"] == "explicit_product_name_missing"]
    embedded_competitors = [name for name in embedded_names if _compare_name(name, analyzer.target) != "target"]
    if body_targets and not competitors and not embedded_competitors:
        evidence = [{"method": "unique_body_table_document_context", "region_id": r["region_id"], "location": r["locations"][0]} for r in body_targets]
        amended = set()
        for region in analyzer.regions:
            if region["status"] == "ambiguous" and not region["product_names"] and region["reason"] in {"unscoped_slide_text", "no_local_product_identity"}:
                region.update(status="target", product_names=[analyzer.target["name"]], reason="unique_body_table_document_context", inherited_context=evidence)
                amended.add(region["region_id"])
        for slot in analyzer.slots:
            if slot["region_id"] in amended:
                slot.update(product_status="target", editable=bool(slot["text"].strip()), identity_evidence=[analyzer.target["name"]])
                slot["inherited_context"] = [*evidence, *slot["inherited_context"]]
        analyzer.issues = [i for i in analyzer.issues if i.get("region_id") not in amended]


def _analyze_docx(package, analyzer):
    parts = ["word/document.xml"]
    parts += sorted(n for n in package.namelist() if re.fullmatch(r"word/(?:header|footer)\d+\.xml", n))
    for part in parts:
        root = _xml(package.read(part))
        title, context, previous = None, None, []
        table_index, paragraph_index = 0, 0
        # Walk in document order but process tables as one unit. Content-control
        # wrappers do not affect the preserved source XPath.
        for node in root.iter():
            if not _visible(node):
                continue
            if any(a.tag == _q("w", "tbl") for a in node.iterancestors()):
                continue
            if node.tag == _q("w", "tbl"):
                table_index += 1
                location = {"part": part, "table": table_index, "page": None, "xpath": _xpath(node)}
                previous = analyzer.table(node, location, title, previous, word=True)
                # An explicit table identity governs subsequent inline notes,
                # but multiple product columns cannot define one text owner.
                if len(previous) == 1:
                    context = previous[0]
                else:
                    context, title = None, None
            elif node.tag == _q("w", "p"):
                paragraph_index += 1
                text = _text(node)
                if not text.strip():
                    continue
                location = {"part": part, "paragraph": paragraph_index, "page": None, "xpath": _xpath(node)}
                names = _names(text, True)
                explicit = _explicit_names(text)
                subject = _subject_name(text)
                # Some one-pagers split the full product title across adjacent
                # paragraphs. Combine only short neighboring paragraphs ending
                # with the legal suffix; keep every source paragraph separate.
                combined = text
                neighbor = node.getnext() if names and not re.search(SUFFIX, text) else None
                for _ in range(2):
                    if neighbor is None or neighbor.tag != _q("w", "p") or len(_text(neighbor)) > 100:
                        break
                    next_text = _text(neighbor).strip()
                    if not next_text or re.search(r"[：:。；]", next_text):
                        break
                    combined += next_text
                    combined_names = _names(combined)
                    if combined_names and re.search(SUFFIX + r"$", combined):
                        names = combined_names
                        break
                    neighbor = neighbor.getnext()
                style = node.find("w:pPr/w:pStyle", NS)
                heading = node.find("w:pPr/w:outlineLvl", NS) is not None or (style is not None and re.search(r"heading|标题", style.get(_q("w", "val"), ""), re.I))
                if explicit is not None:
                    title = {"text": text, "names": explicit, "location": location, "explicit_identity": True}
                    context = analyzer.region(explicit, "explicit_inline_product_name" if explicit else "explicit_product_name_missing", location, text)
                elif subject:
                    title = {"text": text, "names": [subject], "location": location, "evidence_type": "prose_subject"}
                    context = analyzer.region([subject], "explicit_prose_product_subject", location, text)
                elif names:
                    title = {"text": text, "names": names, "location": location}
                    context = analyzer.region(names, "local_document_heading", location, text)
                elif heading:
                    title = {"text": text, "names": [], "location": location}
                    context = None
                elif CONTINUED.search(text) and len(text) < 60:
                    title = {"text": text, "names": [], "location": location}
                if context is None:
                    context = analyzer.region([], "no_local_product_identity", location, title["text"] if title else "")
                analyzer.paragraph(node, context, location)


def _run_analysis(path, target, labels, keep_empty=False):
    analyzer = _Analyzer(target, labels, keep_empty)
    with ZipFile(path) as package:
        if path.suffix.lower() == ".pptx":
            _analyze_pptx(package, analyzer)
        else:
            _analyze_docx(package, analyzer)
    return analyzer


def _template_candidates(analyzer, is_ppt):
    """Find source subjects without comparing any name to the destination."""
    slots = defaultdict(list)
    for slot in analyzer.slots:
        slots[slot["region_id"]].append(slot)
    candidates = {}
    for region in analyzer.regions:
        names = region["product_names"]
        fields = {s["field_id"] for s in slots[region["region_id"]] if s["field_id"]}
        if len(names) != 1 or region["status"] == "ambiguous" or not fields - {"product_name"}:
            continue
        location = region["locations"][0]
        if not is_ppt and location["part"] != "word/document.xml":
            continue
        name = re.sub(r"\s+", "", names[0])
        key = _normalize(name)
        candidate = candidates.setdefault(key, {"name": name, "aliases": [], "region_ids": [], "locations": []})
        candidate["region_ids"].append(region["region_id"])
        candidate["locations"].append(location)
    merged = []
    for candidate in sorted(candidates.values(), key=lambda c: -len(c["name"])):
        same = next((prior for prior in merged if _same_source_name(candidate["name"], prior)), None)
        if same is None:
            merged.append(candidate)
        else:
            same["aliases"].append(candidate["name"])
            same["region_ids"].extend(candidate["region_ids"])
            same["locations"].extend(candidate["locations"])
    # A first-slide subject must be outside its tables. A first-page table by
    # itself is not cover evidence and cannot defeat another product table.
    covers = []
    for region in analyzer.regions:
        location = region["locations"][0]
        fields = {s["field_id"] for s in slots[region["region_id"]] if s["field_id"]}
        cover = (location.get("page") == 1 and not location.get("table")) if is_ppt else (
            location["part"] == "word/document.xml" and location.get("paragraph") == 1)
        if cover and len(region["product_names"]) == 1 and region["status"] != "ambiguous" and not fields - {"product_name"}:
            name = re.sub(r"\s+", "", region["product_names"][0])
            if name not in covers:
                covers.append(name)
    return merged, covers, slots


def _same_source_name(name, candidate):
    # Only exact complete names and mechanically shortened legal suffixes count.
    # User aliases cannot make distinct product issues equivalent.
    return _compare_name(name, {"name": candidate["name"], "aliases": []}) == "target"


def _choose_template(analyzer, is_ppt, requested):
    candidates, covers, slots = _template_candidates(analyzer, is_ppt)
    selection = {"status": "needs_selection", "method": "unresolved_template_scope",
                 "candidates": candidates, "cover_names": covers, "selected_region_ids": [],
                 "reason": "模板主体范围尚未确定；选择要使用的模板主体，与合同产品名称是否相同无关。"}
    chosen = None
    if requested is not None:
        if isinstance(requested, str):
            request = {"name": requested, "reason": "explicit_template_product_argument"}
        elif isinstance(requested, dict):
            request = dict(requested)
            if not isinstance(request.get("reason"), str) or not request["reason"].strip():
                raise ValueError("template_product mapping requires a non-empty selection reason")
        else:
            raise ValueError("template_product must be a name or a mapping with name and reason")
        name = request.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("template_product requires a non-empty source name")
        matches = [candidate for candidate in candidates if _normalize(name) in
                   {_normalize(value) for value in [candidate["name"], *candidate["aliases"]]}]
        if len(matches) != 1:
            raise ValueError("template_product must select one observed template body subject")
        chosen = dict(matches[0])
        aliases = request.get("aliases", []) or []
        if isinstance(aliases, str):
            aliases = [aliases]
        observed = {n for r in analyzer.regions for n in r["product_names"]}
        if not isinstance(aliases, list) or any(not isinstance(a, str) or not _same_source_name(a, chosen)
                                               or not any(_normalize(a) == _normalize(n) for n in observed) for a in aliases):
            raise ValueError("template aliases must be observed names of the same selected source subject")
        chosen["aliases"] = list(dict.fromkeys([*chosen["aliases"], *aliases]))
        selection.update(status="selected", method="explicit_observed_template_subject", reason=request["reason"])
    else:
        cover_matches = [candidate for candidate in candidates if any(_same_source_name(name, candidate) for name in covers)]
        if len(cover_matches) == 1 and len(covers) == 1:
            chosen = cover_matches[0]
            selection.update(status="selected", method="cover_and_body_template_subject", reason="模板封面主体与正文要素共同确定使用范围。")
        elif len(candidates) == 1 and not covers:
            chosen = candidates[0]
            selection.update(status="selected", method="unique_body_template_subject", reason="模板只有一个有正文要素支持的主体。")
        elif not candidates and not any(r["product_names"] for r in analyzer.regions):
            # A genuinely unnamed single group is useful as a blank template;
            # multiple anonymous tables remain separate selectable scopes.
            groups = [r for r in analyzer.regions if any(s["field_id"] for s in slots[r["region_id"]])]
            if len(groups) == 1 and groups[0]["reason"] in {"no_local_product_identity", "unscoped_slide_text"}:
                selection.update(status="selected", method="unique_unnamed_field_group",
                                 reason="模板不含产品身份，唯一字段组作为换填范围。",
                                 selected_region_ids=[groups[0]["region_id"]])
                return {"name": None, "aliases": [], "status": "unnamed_template"}, selection
    if chosen:
        return {"name": chosen["name"], "aliases": chosen.get("aliases", []), "status": "identified_from_template"}, selection
    return None, selection


def _bind_template_context(analyzer, region_ids, reason, source):
    """Bind only evidenced local context; never relabel every other region."""
    for region in analyzer.regions:
        if region["region_id"] in region_ids:
            region.update(status="target", reason=reason)
            region["inherited_context"].append({"method": reason, "template_product": source})
    for slot in analyzer.slots:
        if slot["region_id"] in region_ids and not slot.get("shared_scope"):
            slot.update(product_status="target", editable=bool(slot["text"].strip()))
            slot["inherited_context"].append({"method": reason, "template_product": source})
    analyzer.issues = [issue for issue in analyzer.issues
                       if not (issue.get("code") == "ambiguous_product_region" and issue.get("region_id") in region_ids)]


def _template_context(analyzer, is_ppt, source, selection):
    if selection["status"] != "selected":
        return
    if selection["method"] == "unique_unnamed_field_group":
        _bind_template_context(analyzer, set(selection["selected_region_ids"]), selection["method"], source)
    regions = analyzer.regions
    generic_reasons = {"unscoped_slide_text", "no_local_product_identity"}
    selected = [r for r in regions if r["status"] == "target"]
    bindings = set()
    if is_ppt:
        pages = {loc.get("page") for r in selected for loc in r["locations"]}
        for page in pages:
            local = [r for r in regions if any(loc.get("page") == page for loc in r["locations"])]
            if any(r["product_names"] and r["status"] != "target" for r in local):
                continue
            bindings.update(r["region_id"] for r in local if r["status"] == "ambiguous"
                            and not r["product_names"] and r["reason"] in generic_reasons)
        reason = "selected_template_page_context"
    else:
        # A one-subject document's generic headings/header/footer belong to the
        # format too. Explicit competing names or shared regions still fence it.
        if not any(r["product_names"] and r["status"] != "target" for r in regions):
            bindings.update(r["region_id"] for r in regions if r["status"] == "ambiguous"
                            and not r["product_names"] and r["reason"] in generic_reasons)
        reason = "selected_template_document_context"
    _bind_template_context(analyzer, bindings, reason, source)
    selection["selected_region_ids"] = [r["region_id"] for r in regions if r["status"] == "target"]


def _delivery_scope(result, is_ppt):
    pages = set()
    if is_ppt:
        # Also works for direct importlib loaders used by integrations/tests.
        try:
            from ppt_delivery import suggest_element_pages
        except ModuleNotFoundError:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_material_ppt_delivery", Path(__file__).with_name("ppt_delivery.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            suggest_element_pages = module.suggest_element_pages
        suggestion = suggest_element_pages(result)
        result["template_selection"]["element_pages"] = suggestion
        pages = set(suggestion["pages"] + suggestion["review_pages"])
    for slot in result["slots"]:
        slot["delivery_required"] = slot["product_status"] == "target" and slot.get("content_role") != "layout_blank" and (
            not is_ppt or slot["location"].get("page") in pages)
    if not is_ppt and result["template_selection"]["status"] == "selected":
        excluded = [s for s in result["slots"] if s["product_status"] != "target" and s["text"].strip()]
        if excluded:
            result["issues"].append({"code": "template_unselected_document_content",
                "message": "Word整份交付仍含未选定区域内容；须确认换填或移除范围，不能将旧产品文字静默保留。",
                "related_region_ids": list(dict.fromkeys(s["region_id"] for s in excluded)),
                "related_slot_ids": [s["slot_id"] for s in excluded]})


def analyze_material(path, target_product, dictionary=None, *, material_mode="review", template_product=None):
    """Return product regions and paragraph slots from a DOCX or PPTX file.

    ``target_product`` is a full product name or ``{"name": ..., "aliases": [...]}``.
    Aliases must denote the same complete issue; a broad alias never overrides a
    conflicting issue number. The returned source SHA binds review decisions to
    this exact input, and ``editable`` is true only for confidently target slots.
    ``dictionary`` may be a list, or a mapping with ``fields``/``field_dictionary``.
    Unclassified material labels remain slots with ``field_id=None``.

    ``template`` mode selects the source template's subject independently, then
    uses target/other as editing scope. Destination identity always remains the
    contract's. An explicit source selection cannot invent a new source subject.
    """
    path = Path(path).resolve()
    target = {"name": target_product, "aliases": []} if isinstance(target_product, str) else dict(target_product)
    if not target.get("name", "").strip():
        raise ValueError("target_product must contain a non-empty name")
    target["aliases"] = target.get("aliases", []) or []
    if isinstance(target["aliases"], str):
        target["aliases"] = [target["aliases"]]
    if material_mode not in {"review", "template"}:
        raise ValueError("material_mode must be review or template")
    if material_mode == "review" and template_product is not None:
        raise ValueError("template_product is only valid in template mode")
    blob = path.read_bytes()
    if path.suffix.lower() not in {".docx", ".pptx"}:
        raise ValueError("Material region analysis supports DOCX and PPTX source packages only")
    labels = _load_dictionary(dictionary)
    source, selection = None, None
    if material_mode == "template":
        analyzer = _run_analysis(path, {"name": "__template_scope_probe__", "aliases": []}, labels, True)
        source, selection = _choose_template(analyzer, path.suffix.lower() == ".pptx", template_product)
        if source and source["name"]:
            analyzer = _run_analysis(path, source, labels, True)
        _template_context(analyzer, path.suffix.lower() == ".pptx", source, selection)
        if selection["status"] != "selected":
            analyzer.issues.append({"code": "template_scope_selection_required", "message": selection["reason"]})
    else:
        analyzer = _run_analysis(path, target, labels)
    for region in analyzer.regions:
        slots = [s for s in analyzer.slots if s["region_id"] == region["region_id"]]
        region["identity_review_required"] = any(s["field_id"] is not None or re.search(r"xx|待补充|待核实|待填写", s["text"], re.I) for s in slots)
    required = {r["region_id"] for r in analyzer.regions if r["identity_review_required"]}
    analyzer.issues = [i for i in analyzer.issues if not i.get("region_id") or i.get("region_id") in required]
    counts = Counter(r["status"] for r in analyzer.regions)
    result = {"source": {"path": str(path), "sha256": sha256(blob).hexdigest()}, "target_product": target,
            "material_mode": material_mode, "template_source_product": source,
            "destination_product": target, "template_selection": selection,
            "regions": analyzer.regions, "slots": analyzer.slots, "issues": analyzer.issues,
            "summary": {"regions": len(analyzer.regions), "slots": len(analyzer.slots),
                        "target_regions": counts["target"], "other_regions": counts["other"],
                        "ambiguous_regions": counts["ambiguous"],
                        "editable_slots": sum(s["editable"] for s in analyzer.slots),
                        "unclassified_slots": sum(s["field_id"] is None for s in analyzer.slots),
                        "identity_policy": "local_evidence_only_no_filename_or_cover_propagation",
                        "revision_policy": "accepted_text_source_xpath_preserved"}}
    if material_mode == "template":
        result["summary"]["identity_policy"] = "template_subject_scope_independent_of_contract_destination"
        _delivery_scope(result, path.suffix.lower() == ".pptx")
    return result
