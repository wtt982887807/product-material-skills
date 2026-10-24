"""Guarded, local OOXML edits for reviewed material operations.

No source is overwritten. Unsafe or ambiguous operations raise PatchPlanError
before any destination is changed. Word changes remain real review revisions;
existing revisions are never accepted, flattened, or rewritten.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import tempfile
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from lxml import etree

try:
    from text_spans import locate_span
except ModuleNotFoundError as exc:
    if exc.name != "text_spans":
        raise
    # Also support isolated file-based loaders used by portable skill clients.
    import importlib.util
    _span_spec = importlib.util.spec_from_file_location("material_text_spans", Path(__file__).with_name("text_spans.py"))
    _span_module = importlib.util.module_from_spec(_span_spec)
    _span_spec.loader.exec_module(_span_module)
    locate_span = _span_module.locate_span

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
XML = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W, "a": A, "p": P, "r": OFFICE_REL.rstrip("/"), "rel": REL, "ct": CT}
REVIEW_TAGS = {f"{{{W}}}{tag}" for tag in ("ins", "del", "moveFrom", "moveTo", "comment")}


class PatchPlanError(ValueError):
    """The source/plan cannot be updated without an unsafe guess."""


def _fail(message):
    raise PatchPlanError(message)


def _sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _parse(data, name):
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                 remove_blank_text=False, strip_cdata=False)
        root = etree.fromstring(data, parser)
        if root.getroottree().docinfo.doctype:
            _fail(f"DTD is unsupported in OOXML part: {name}")
        return root
    except etree.XMLSyntaxError as exc:
        _fail(f"Invalid XML part {name}: {exc}")


def _package(path):
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(set(names)) != len(names):
                _fail("Duplicate ZIP members are unsafe")
            return {n: archive.read(n) for n in names}, infos, archive.comment
    except (BadZipFile, OSError) as exc:
        _fail(f"Cannot read OOXML package: {exc}")


def _is_hidden_word(node, paragraph):
    for ancestor in node.iterancestors():
        if ancestor is paragraph:
            return False
        if ancestor.tag in {f"{{{W}}}del", f"{{{W}}}moveFrom"}:
            return True
        if ancestor.tag == f"{{{W}}}p":
            return True  # Do not fold nested text-box paragraphs into their host.
    return False


def _text_nodes(paragraph, kind):
    """Visible accepted text with source nodes and character offsets."""
    cursor = 0
    result = []
    for node in paragraph.iter():
        text = None
        if kind == "docx" and not _is_hidden_word(node, paragraph):
            if node.tag == f"{{{W}}}t": text = node.text or ""
            elif node.tag == f"{{{W}}}tab": text = "\t"
            elif node.tag in {f"{{{W}}}br", f"{{{W}}}cr"}: text = "\n"
        elif kind == "pptx":
            if node.tag == f"{{{A}}}t": text = node.text or ""
            elif node.tag == f"{{{A}}}br": text = "\n"
        if text is not None:
            result.append((node, cursor, cursor + len(text), text))
            cursor += len(text)
    return result


def _text(paragraph, kind):
    return "".join(record[3] for record in _text_nodes(paragraph, kind))


def _plain_word_run(run, paragraph):
    return (run.tag == f"{{{W}}}r" and run.getparent() is paragraph
            and all(child.tag in {f"{{{W}}}rPr", f"{{{W}}}t"} for child in run))


def _word_context(paragraph):
    """Reject structural contexts that neither local edits nor anchors can alter."""
    protected = {f"{{{W}}}{tag}" for tag in ("ins", "del", "moveFrom", "moveTo", "fldSimple")}
    if any(node.tag in protected for node in paragraph.iterancestors()):
        _fail("Manual review required: paragraph is inside an existing revision or field")
    field_depth = 0
    for node in paragraph.getroottree().getroot().iter():
        if node is paragraph:
            break
        if node.tag == f"{{{W}}}fldChar":
            field_type = node.get(f"{{{W}}}fldCharType")
            if field_type == "begin": field_depth += 1
            elif field_type == "end": field_depth = max(0, field_depth - 1)
    if field_depth:
        _fail("Manual review required: target lies within a complex field spanning paragraphs")
    if paragraph.xpath(".//w:fldChar | .//w:instrText | .//w:fldSimple", namespaces=NS):
        _fail("Manual review required: target paragraph contains a field")


def _word_segments(paragraph, start, end):
    _word_context(paragraph)
    segments = []
    by_run = {}
    for node, lo, hi, text in _text_nodes(paragraph, "docx"):
        if hi <= start or lo >= end:
            continue
        run = node.getparent()
        if node.tag != f"{{{W}}}t" or not _plain_word_run(run, paragraph):
            if any(a.tag in {f"{{{W}}}ins", f"{{{W}}}moveTo"} for a in node.iterancestors()):
                _fail("Manual review required: replacement intersects existing revision text; "
                      "choose an ordinary-run range or comment on the complete revision without rewriting its history")
            _fail("Manual review required: target intersects an existing revision, hyperlink, drawing, or non-text run")
        if run not in by_run:
            by_run[run] = [run, lo, hi, text]
            segments.append(by_run[run])
        else:
            by_run[run][2] = hi
            by_run[run][3] += text
    # Include all text in a touched run (including text nodes outside the range).
    for segment in segments:
        run = segment[0]
        records = [r for r in _text_nodes(paragraph, "docx") if r[0].getparent() is run]
        segment[1] = records[0][1]
        segment[2] = records[-1][2]
        segment[3] = "".join(r[3] for r in records)
    if not segments:
        _fail("No editable text run at target range")
    children = list(paragraph)
    left, right = children.index(segments[0][0]), children.index(segments[-1][0])
    if any(child not in by_run for child in children[left:right + 1]):
        _fail("Manual review required: replacement crosses a bookmark, comment, revision, or non-text boundary")
    return segments


def _word_comment_range(paragraph, start, end):
    """Allow anchors around intact old revisions, never inside or splitting them.

    Commenting does not replace the covered content. Complete simple insertion
    nodes can therefore remain byte-for-byte unchanged between paragraph-level
    anchors, together with any existing deletions between them. A boundary
    inside such a node still requires a wider, explicitly reviewed range.
    """
    _word_context(paragraph)
    children = list(paragraph)
    touched = []
    cursor = 0
    for index, child in enumerate(children):
        text = _child_word_text(child)
        lo, hi = cursor, cursor + len(text)
        cursor = hi
        if not text or hi <= start or lo >= end:
            continue
        if _plain_word_run(child, paragraph):
            touched.append(index)
            continue
        if child.tag in {f"{{{W}}}ins", f"{{{W}}}moveTo"}:
            if start > lo or end < hi:
                _fail("Manual review required: comment boundary cuts through an existing revision; "
                      "select its complete visible text to preserve revision history")
            if all(_plain_word_run(run, child) for run in child):
                touched.append(index)
                continue
        _fail("Manual review required: comment target intersects a hyperlink, drawing, or unsupported run structure")
    if not touched:
        _fail("No commentable text at target range")
    for index in range(touched[0], touched[-1] + 1):
        child = children[index]
        if (index not in touched and child.tag not in {f"{{{W}}}del", f"{{{W}}}moveFrom"}
                and not _plain_word_run(child, paragraph)):
            _fail("Manual review required: comment crosses a bookmark, comment, or non-text boundary")


def _ppt_segments(paragraph, start, end):
    touched = []
    for node, lo, hi, text in _text_nodes(paragraph, "pptx"):
        if hi <= start or lo >= end:
            continue
        if node.tag != f"{{{A}}}t" or node.getparent().tag != f"{{{A}}}r" or node.getparent().getparent() is not paragraph:
            _fail("Manual review required: PPT target intersects a field or line break")
        touched.append((node, lo, hi, text))
    if not touched:
        _fail("No editable PPT run at target range")
    return touched


def _resolve(root, op, kind):
    try:
        namespaces = dict(NS)
        namespaces.update({key: value for key, value in root.nsmap.items() if key})
        matches = root.xpath(op["xpath"], namespaces=namespaces)
    except (etree.XPathError, TypeError) as exc:
        _fail(f"Invalid XPath for {op['op_id']}: {exc}")
    if not isinstance(matches, list) or len(matches) != 1 or not isinstance(matches[0], etree._Element):
        _fail(f"XPath must resolve to exactly one text object: {op['op_id']}")
    selected = matches[0]
    if kind == "docx":
        if selected.tag != f"{{{W}}}p":
            _fail("Word target must be one paragraph, including a table-cell paragraph")
        candidates = [selected]
    elif selected.tag == f"{{{A}}}p":
        candidates = [selected]
    elif selected.tag in {f"{{{P}}}sp", f"{{{P}}}graphicFrame", f"{{{A}}}tc"}:
        candidates = selected.xpath(".//a:p", namespaces=NS)
    else:
        _fail("PPT target must be a paragraph or explicitly scoped text shape/cell/table")
    hits = []
    before = op["before"]
    source_start = op.get("start")
    if source_start is not None and (isinstance(source_start, bool) or not isinstance(source_start, int) or source_start < 0):
        _fail("start must be a nonnegative integer source paragraph offset")
    for paragraph in candidates:
        text = _text(paragraph, kind)
        if before not in text:
            continue
        try:
            start, end = locate_span(text, before, source_start)
        except ValueError as exc:
            if source_start is None or len(candidates) == 1:
                _fail(f"{exc}: {op['op_id']}")
            continue
        hits.append((paragraph, start, end))
    if len(hits) != 1:
        _fail(f"before text must occur exactly once within one paragraph: {op['op_id']}")
    return hits[0]


def _prepare(data, operations, kind):
    if not isinstance(operations, (list, tuple)):
        _fail("operations must be a list")
    roots = {}
    prepared = []
    op_ids = set()
    for raw in operations:
        if not isinstance(raw, dict): _fail("Each operation must be an object")
        op = deepcopy(raw)
        for key in ("op_id", "kind", "part", "xpath", "before"):
            if not isinstance(op.get(key), str) or not op[key]:
                _fail(f"Operation requires nonempty {key}")
        if op["op_id"] in op_ids: _fail("Duplicate op_id")
        op_ids.add(op["op_id"])
        if op["kind"] not in {"replace_text", "comment", "highlight"}: _fail("Unsupported operation kind")
        if op["kind"] == "replace_text":
            if not isinstance(op.get("after"), str): _fail("Replacement requires string after")
            if op["after"] == op["before"]: _fail("Replacement before and after are identical")
        elif op["kind"] == "highlight":
            if op.get("color") != "yellow": _fail("Only yellow highlight is supported")
            if "after" in op and op["after"] != op["before"]:
                _fail("Highlight cannot change text; omit after or keep it equal to before")
            op["after"] = op["before"]
        else:
            comment = op.get("after") or op.get("reason")
            if not isinstance(comment, str) or not comment.strip(): _fail("Comment requires text in after or reason")
            op["after"] = comment
        if op["kind"] == "replace_text" and any(ch in op["after"] for ch in ("\r", "\n", "\t")):
            _fail("Multiline/tab replacements require manual review")
        part = op["part"]
        prefix = "word/" if kind == "docx" else "ppt/slides/"
        if not part.startswith(prefix) or not part.endswith(".xml") or ".." in PurePosixPath(part).parts or "\\" in part:
            _fail("Operation part is outside editable content")
        if kind == "docx" and (part == "word/comments.xml" or "/_rels/" in part):
            _fail("Comments and relationship parts cannot be explicit content targets")
        if part not in data: _fail(f"Missing source part: {part}")
        root = roots.setdefault(part, _parse(data[part], part))
        paragraph, start, end = _resolve(root, op, kind)
        if kind == "docx":
            if op["kind"] == "comment": _word_comment_range(paragraph, start, end)
            else: _word_segments(paragraph, start, end)
        else: _ppt_segments(paragraph, start, end)
        prepared.append({"op": op, "paragraph": paragraph, "start": start, "end": end,
                         "paragraph_xpath": paragraph.getroottree().getpath(paragraph)})
    grouped = defaultdict(list)
    for entry in prepared:
        grouped[(entry["op"]["part"], entry["paragraph_xpath"])].append(entry)
    for entries in grouped.values():
        replacements = sorted((e for e in entries if e["op"]["kind"] == "replace_text"), key=lambda e: e["start"])
        for first, second in zip(replacements, replacements[1:]):
            if first["end"] > second["start"]: _fail("Overlapping replacements are unsafe")
        for comment in (e for e in entries if e["op"]["kind"] == "comment"):
            for replacement in replacements:
                overlap = comment["start"] < replacement["end"] and replacement["start"] < comment["end"]
                contains = comment["start"] <= replacement["start"] and comment["end"] >= replacement["end"]
                if overlap and not contains:
                    _fail("Comment boundary cuts through a replacement; use the whole replacement range")
        for highlight in (e for e in entries if e["op"]["kind"] == "highlight"):
            if any(highlight["start"] < r["end"] and r["start"] < highlight["end"] for r in replacements):
                _fail("Retained highlight span overlaps a replacement")
    return roots, prepared, grouped


def _run_piece(run, text, deleted=False):
    piece = deepcopy(run)
    piece.tail = None
    for child in list(piece):
        if child.tag != f"{{{W}}}rPr": piece.remove(child)
    node = etree.SubElement(piece, f"{{{W}}}{'delText' if deleted else 't'}")
    node.text = text
    if text[:1].isspace() or text[-1:].isspace(): node.set(f"{{{XML}}}space", "preserve")
    return piece


def _allocate_ids(data):
    ids = set()
    for name, body in data.items():
        if name.startswith("word/") and name.endswith(".xml"):
            # Do not parse opaque/unrelated parts just to inspect existing ids.
            try:
                root = _parse(body, name)
            except PatchPlanError:
                continue
            for node in root.iter():
                value = node.get(f"{{{W}}}id")
                if value and re.fullmatch(r"\d+", value): ids.add(int(value))
    current = max(ids, default=-1) + 1
    while True:
        yield str(current)
        current += 1


def _replace_word(entry, ids, author, timestamp):
    paragraph, start, end = entry["paragraph"], entry["start"], entry["end"]
    segments = _word_segments(paragraph, start, end)
    first, last = segments[0], segments[-1]
    index = paragraph.index(first[0])
    output = []
    prefix = first[3][:max(0, start - first[1])]
    suffix = last[3][min(len(last[3]), end - last[1]):]
    if prefix: output.append(_run_piece(first[0], prefix))
    deletion = etree.Element(f"{{{W}}}del")
    deletion_id = next(ids)
    deletion.attrib.update({f"{{{W}}}id": deletion_id, f"{{{W}}}author": author, f"{{{W}}}date": timestamp})
    insertion = etree.Element(f"{{{W}}}ins")
    insertion_id = next(ids)
    insertion.attrib.update({f"{{{W}}}id": insertion_id, f"{{{W}}}author": author, f"{{{W}}}date": timestamp})
    remaining = entry["op"]["after"]
    for number, (run, lo, hi, text) in enumerate(segments):
        old = text[max(0, start - lo):min(len(text), end - lo)]
        deletion.append(_run_piece(run, old, deleted=True))
        length = len(old) if number < len(segments) - 1 else len(remaining)
        new, remaining = remaining[:length], remaining[length:]
        if new: insertion.append(_run_piece(run, new))
    output.append(deletion)
    if len(insertion): output.append(insertion)
    if suffix: output.append(_run_piece(last[0], suffix))
    tail = last[0].tail
    for segment in segments: paragraph.remove(segment[0])
    for offset, node in enumerate(output): paragraph.insert(index + offset, node)
    output[-1].tail = tail
    return {"deletion_id": deletion_id, "insertion_id": insertion_id if len(insertion) else None}


def _replace_ppt(entry):
    segments = _ppt_segments(entry["paragraph"], entry["start"], entry["end"])
    remaining = entry["op"]["after"]
    for number, (node, lo, hi, old) in enumerate(segments):
        left, right = max(0, entry["start"] - lo), min(len(old), entry["end"] - lo)
        length = right - left if number < len(segments) - 1 else len(remaining)
        new, remaining = remaining[:length], remaining[length:]
        node.text = old[:left] + new + old[right:]


def _translated_position(position, replacements):
    return position + sum(len(r["op"]["after"]) - (r["end"] - r["start"])
                          for r in replacements if r["end"] <= position)


def _set_run_highlight(run, kind):
    """Insert the native run property in schema order, preserving other styles."""
    namespace = W if kind == "docx" else A
    properties = run.find(f"{{{namespace}}}rPr")
    if properties is None:
        properties = etree.Element(f"{{{namespace}}}rPr")
        run.insert(0, properties)
    old = properties.findall(f"{{{namespace}}}highlight")
    mark = etree.Element(f"{{{namespace}}}highlight")
    if kind == "docx":
        mark.set(f"{{{W}}}val", "yellow")
        later = {"u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em",
                 "lang", "eastAsianLayout", "specVanish", "oMath", "rPrChange"}
    else:
        etree.SubElement(mark, f"{{{A}}}srgbClr", val="FFFF00")
        later = {"uLnTx", "uLn", "uFillTx", "uFill", "latin", "ea", "cs", "sym",
                 "hlinkClick", "hlinkMouseOver", "rtl", "extLst"}
    if old:
        index = properties.index(old[0])
        for node in old: properties.remove(node)
    else:
        index = next((i for i, child in enumerate(properties)
                      if etree.QName(child).localname in later), len(properties))
    properties.insert(index, mark)


def _ppt_run_piece(run, text):
    if (len(run.findall(f"{{{A}}}t")) != 1 or
            any(child.tag not in {f"{{{A}}}rPr", f"{{{A}}}t"} for child in run)):
        _fail("Manual review required: unsupported PPT run structure for precise highlight")
    piece = deepcopy(run)
    piece.tail = None
    piece.find(f"{{{A}}}t").text = text
    return piece


def _highlight_range(entry, kind, replacements):
    paragraph = entry["paragraph"]
    start = _translated_position(entry["start"], replacements)
    end = _translated_position(entry["end"], replacements)
    segments = (_word_segments(paragraph, start, end) if kind == "docx"
                else [(node.getparent(), lo, hi, value)
                      for node, lo, hi, value in _ppt_segments(paragraph, start, end)])
    piece_factory = _run_piece if kind == "docx" else _ppt_run_piece
    for run, lo, hi, value in segments:
        left, right = max(0, start - lo), min(len(value), end - lo)
        pieces = []
        if left: pieces.append(piece_factory(run, value[:left]))
        highlighted = piece_factory(run, value[left:right])
        _set_run_highlight(highlighted, kind)
        pieces.append(highlighted)
        if right < len(value): pieces.append(piece_factory(run, value[right:]))
        index, tail = paragraph.index(run), run.tail
        paragraph.remove(run)
        for offset, piece in enumerate(pieces): paragraph.insert(index + offset, piece)
        pieces[-1].tail = tail
    return {"color": "yellow", "applied_start": start, "applied_end": end,
            "highlight_text": entry["op"]["before"]}


def _child_word_text(child):
    if child.tag in {f"{{{W}}}del", f"{{{W}}}moveFrom"}: return ""
    # Use exactly the same character model as source selection: tabs and both
    # break elements each occupy one character, while deleted/nested text does
    # not. A separate t-only count silently shifted anchors after controls.
    return _text(child, "docx")


def _split_boundary(paragraph, boundary):
    cursor = 0
    for child in list(paragraph):
        text = _child_word_text(child)
        if cursor < boundary < cursor + len(text):
            if not _plain_word_run(child, paragraph):
                _fail("Manual review required: comment boundary would split an existing revision or non-text object")
            index = paragraph.index(child)
            offset = boundary - cursor
            left, right = _run_piece(child, text[:offset]), _run_piece(child, text[offset:])
            right.tail = child.tail
            paragraph.remove(child)
            paragraph.insert(index, left)
            paragraph.insert(index + 1, right)
            return
        cursor += len(text)


def _anchor_comment(paragraph, start, end, comment_id):
    _split_boundary(paragraph, end)
    _split_boundary(paragraph, start)
    cursor = 0
    covered = []
    for child in paragraph:
        text = _child_word_text(child)
        if text and cursor >= start and cursor + len(text) <= end:
            covered.append(child)
        cursor += len(text)
    begin = etree.Element(f"{{{W}}}commentRangeStart", {f"{{{W}}}id": comment_id})
    finish = etree.Element(f"{{{W}}}commentRangeEnd", {f"{{{W}}}id": comment_id})
    reference = etree.Element(f"{{{W}}}r")
    properties = etree.SubElement(reference, f"{{{W}}}rPr")
    etree.SubElement(properties, f"{{{W}}}rStyle", {f"{{{W}}}val": "CommentReference"})
    etree.SubElement(reference, f"{{{W}}}commentReference", {f"{{{W}}}id": comment_id})
    if covered:
        paragraph.insert(paragraph.index(covered[0]), begin)
        index = paragraph.index(covered[-1]) + 1
    else:
        # A comment on a complete deletion is a point anchor in that paragraph.
        cursor, index = 0, len(paragraph)
        for i, child in enumerate(paragraph):
            text = _child_word_text(child)
            if text and cursor >= start:
                index = i
                break
            cursor += len(text)
        paragraph.insert(index, begin)
        index += 1
    paragraph.insert(index, finish)
    paragraph.insert(index + 1, reference)


def _serialize(root):
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _relationship(roots, data, part, rel_type, target):
    rel_part = str(PurePosixPath(part).parent / "_rels" / (PurePosixPath(part).name + ".rels"))
    if rel_part not in roots:
        roots[rel_part] = (_parse(data[rel_part], rel_part) if rel_part in data
                           else etree.Element(f"{{{REL}}}Relationships", nsmap={None: REL}))
    root = roots[rel_part]
    existing = [n for n in root if n.get("Type") == OFFICE_REL + rel_type]
    if len(existing) > 1: _fail(f"Multiple existing {rel_type} relationships require manual review")
    rel_ids = [node.get("Id") for node in root]
    if None in rel_ids or len(set(rel_ids)) != len(rel_ids): _fail("Relationship IDs are missing or duplicate")
    if existing:
        if existing[0].get("Target") != target or existing[0].get("TargetMode") == "External":
            _fail(f"Nonstandard {rel_type} relationship target requires manual review")
        return rel_part
    number = 1
    while f"rId{number}" in rel_ids: number += 1
    etree.SubElement(root, f"{{{REL}}}Relationship", Id=f"rId{number}",
                     Type=OFFICE_REL + rel_type, Target=target)
    return rel_part


def _content_type(roots, data, part, content_type):
    name = "[Content_Types].xml"
    if name not in data: _fail("Package is missing [Content_Types].xml")
    if name not in roots: roots[name] = _parse(data[name], name)
    root = roots[name]
    matches = [n for n in root if n.get("PartName") == "/" + part]
    if len(matches) > 1: _fail("Duplicate content type overrides")
    if matches:
        if matches[0].get("ContentType") != content_type: _fail("Conflicting content type")
    else:
        etree.SubElement(root, f"{{{CT}}}Override", PartName="/" + part, ContentType=content_type)


def _add_comment(entry, roots, data, ids, author, timestamp, replacements):
    if entry["op"]["part"] != "word/document.xml":
        _fail("Word comments outside the main document require manual review")
    name = "word/comments.xml"
    if name not in roots:
        roots[name] = (_parse(data[name], name) if name in data
                       else etree.Element(f"{{{W}}}comments", nsmap={"w": W}))
    comment_id = next(ids)
    comment = etree.SubElement(roots[name], f"{{{W}}}comment",
        {f"{{{W}}}id": comment_id, f"{{{W}}}author": author, f"{{{W}}}date": timestamp})
    lines = entry["op"]["after"].replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines:
        paragraph = etree.SubElement(comment, f"{{{W}}}p")
        run = etree.SubElement(paragraph, f"{{{W}}}r")
        for index, fragment in enumerate(line.split("\t")):
            if index: etree.SubElement(run, f"{{{W}}}tab")
            text_node = etree.SubElement(run, f"{{{W}}}t")
            text_node.text = fragment
            if fragment[:1].isspace() or fragment[-1:].isspace():
                text_node.set(f"{{{XML}}}space", "preserve")
    start, end = (_translated_position(entry[key], replacements) for key in ("start", "end"))
    _anchor_comment(entry["paragraph"], start, end, comment_id)
    _relationship(roots, data, "word/document.xml", "comments", "comments.xml")
    _content_type(roots, data, name, "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml")
    return {"comment_id": comment_id, "applied_start": start, "applied_end": end}


def _ppt_annotation_part(roots, data, owner, kind, default_part, tag):
    """Resolve the actual internal legacy comment relationship, preserving names."""
    rel_name = str(PurePosixPath(owner).parent / '_rels' / (PurePosixPath(owner).name + '.rels'))
    rel_root = roots.get(rel_name)
    if rel_root is None:
        rel_root = _parse(data[rel_name], rel_name) if rel_name in data else etree.Element(f'{{{REL}}}Relationships', nsmap={None: REL})
        roots[rel_name] = rel_root
    links = [n for n in rel_root if n.get('Type') == OFFICE_REL + kind]
    if len(links) > 1: _fail(f'Multiple PPT {kind} relationships are unsupported')
    if links:
        target = links[0].get('Target', '')
        if links[0].get('TargetMode') == 'External' or not target or any(x in target for x in ('\\', ':', '?', '#', '%')):
            _fail(f'Unsafe PPT {kind} relationship')
        part = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(owner), target))
        if part.startswith('../') or not part.startswith('ppt/') or not part.endswith('.xml'):
            _fail(f'Unsafe PPT {kind} part')
        if part not in roots and part not in data: _fail(f'Missing PPT {kind} part')
    else:
        part = default_part
        number = 1
        while part in roots or part in data:
            stem, extension = posixpath.splitext(default_part)
            part = f'{stem}-{number}{extension}'; number += 1
        _relationship(roots, data, owner, kind, posixpath.relpath(part, posixpath.dirname(owner)))
    if part not in roots:
        roots[part] = _parse(data[part], part) if part in data else etree.Element(f'{{{P}}}{tag}', nsmap={'p': P})
    if roots[part].tag != f'{{{P}}}{tag}': _fail('Modern or unsupported PPT comments require manual review')
    _content_type(roots, data, part, 'application/vnd.openxmlformats-officedocument.presentationml.' + kind + '+xml')
    return part, roots[part]


def _ppt_comment_state(roots, data, author):
    if 'ppt/presentation.xml' not in data: _fail('PPT comments require a standard presentation part')
    presentation = _parse(data['ppt/presentation.xml'], 'ppt/presentation.xml')
    if presentation.tag != f'{{{P}}}presentation': _fail('Unsupported PPT presentation namespace')
    for name, body in data.items():
        if name.endswith('.rels'):
            for relation in _parse(body, name):
                typ = relation.get('Type', '')
                if any(word in typ.lower() for word in ('comment', 'person')) and typ not in {OFFICE_REL+'comments', OFFICE_REL+'commentAuthors'}:
                    _fail('Modern or unsupported PPT comments require manual review')
    part, root = _ppt_annotation_part(roots, data, 'ppt/presentation.xml', 'commentAuthors', 'ppt/commentAuthors.xml', 'cmAuthorLst')
    ids = []
    for node in root:
        if node.tag != f'{{{P}}}cmAuthor' or not re.fullmatch(r'\d+', node.get('id', '')):
            _fail('Unsupported PPT comment author list')
        ids.append(int(node.get('id')))
    if len(ids) != len(set(ids)): _fail('Duplicate PPT comment author IDs')
    # A dedicated author record for this run keeps old authors/index counters
    # untouched and makes source-bound replay independent of display name.
    author_id = str(max(ids, default=-1) + 1)
    node = etree.SubElement(root, f'{{{P}}}cmAuthor', id=author_id, name=author,
                            initials=author[:2], lastIdx='0', clrIdx=str(len(ids)))
    return {'author_id': author_id, 'author': node, 'author_part': part, 'index': 0}


def _add_ppt_comment(entry, roots, data, state, timestamp):
    slide = entry['op']['part']
    if not re.fullmatch(r'ppt/slides/[^/]+\.xml', slide): _fail('PPT comments must target a slide part')
    root = entry['paragraph'].getroottree().getroot()
    if root.tag != f'{{{P}}}sld': _fail('PPT comments must target slide content')
    part, comments = _ppt_annotation_part(roots, data, slide, 'comments',
        'ppt/comments/' + PurePosixPath(slide).stem + '-comments.xml', 'cmLst')
    if any(node.tag != f'{{{P}}}cm' for node in comments):
        _fail('Unsupported PPT comment list structure requires manual review')
    state['index'] += 1
    index = str(state['index']); state['author'].set('lastIdx', index)
    x, y = 0, 0
    for ancestor in entry['paragraph'].iterancestors():
        if ancestor.tag in {f'{{{P}}}sp', f'{{{P}}}graphicFrame'}:
            offset = ancestor.find('p:spPr/a:xfrm/a:off', NS) if ancestor.tag == f'{{{P}}}sp' else ancestor.find('p:xfrm/a:off', NS)
            if offset is not None:
                try: x, y = round(int(offset.get('x')) / 1587.5), round(int(offset.get('y')) / 1587.5)
                except (TypeError, ValueError): _fail('Invalid PPT shape position for comment')
            break
    comment = etree.SubElement(comments, f'{{{P}}}cm', authorId=state['author_id'], dt=timestamp, idx=index)
    etree.SubElement(comment, f'{{{P}}}pos', x=str(x), y=str(y))
    etree.SubElement(comment, f'{{{P}}}text').text = entry['op']['after']
    return {'comment_id': index, 'comment_part': part, 'author_id': state['author_id'],
            'author_part': state['author_part'], 'anchor_text': entry['op']['before'],
            'anchor_kind': 'slide_position'}


def _enable_tracking(roots, data):
    name = "word/settings.xml"
    if name not in roots:
        roots[name] = (_parse(data[name], name) if name in data
                       else etree.Element(f"{{{W}}}settings", nsmap={"w": W}))
    tracking = roots[name].find(f"{{{W}}}trackRevisions")
    if tracking is None:
        # Insert before later schema settings when possible; Word preserves all
        # unrelated settings. Common early settings precede trackRevisions.
        early = {"writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime",
                 "doNotDisplayPageBoundaries", "displayBackgroundShape", "printPostScriptOverText",
                 "printFractionalCharacterWidth", "printFormsData", "embedTrueTypeFonts", "embedSystemFonts",
                 "saveSubsetFonts", "saveFormsData", "mirrorMargins", "alignBordersAndEdges", "bordersDoNotSurroundHeader",
                 "bordersDoNotSurroundFooter", "gutterAtTop", "hideSpellingErrors", "hideGrammaticalErrors",
                 "activeWritingStyle", "proofState", "formsDesign", "attachedTemplate", "linkStyles", "stylePaneFormatFilter",
                 "stylePaneSortMethod", "documentType", "mailMerge", "revisionView"}
        index = 0
        for child in roots[name]:
            if etree.QName(child).localname not in early: break
            index += 1
        roots[name].insert(index, etree.Element(f"{{{W}}}trackRevisions"))
    elif tracking.get(f"{{{W}}}val") in {"false", "0", "off"}:
        tracking.set(f"{{{W}}}val", "true")
    _relationship(roots, data, "word/document.xml", "settings", "settings.xml")
    _content_type(roots, data, name, "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml")


def _build(data, operations, kind, author):
    roots, prepared, grouped = _prepare(data, operations, kind)
    ids = _allocate_ids(data)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = {}
    ppt_state = (_ppt_comment_state(roots, data, author) if kind == 'pptx' and
                 any(e['op']['kind'] == 'comment' for e in prepared) else None)
    for entries in grouped.values():
        replacements = sorted((e for e in entries if e["op"]["kind"] == "replace_text"), key=lambda e: e["start"], reverse=True)
        for entry in replacements:
            detail = (_replace_word(entry, ids, author, timestamp) if kind == "docx" else _replace_ppt(entry)) or {}
            results[entry["op"]["op_id"]] = detail
        for entry in (e for e in entries if e["op"]["kind"] == "highlight"):
            results[entry["op"]["op_id"]] = _highlight_range(entry, kind, replacements)
        for entry in (e for e in entries if e["op"]["kind"] == "comment"):
            results[entry["op"]["op_id"]] = (_add_comment(entry, roots, data, ids, author, timestamp, replacements)
                if kind == 'docx' else _add_ppt_comment(entry, roots, data, ppt_state, timestamp))
            if kind == "pptx":
                results[entry["op"]["op_id"]].update(
                    applied_start=_translated_position(entry["start"], replacements),
                    applied_end=_translated_position(entry["end"], replacements))
    if kind == "docx" and any(e["op"]["kind"] == "replace_text" for e in prepared):
        _enable_tracking(roots, data)
    updated = dict(data)
    for name, root in roots.items():
        # Infrastructure that already has correct wiring remains byte-identical.
        if name in data and _canonical(_parse(data[name], name)) == _canonical(root): continue
        updated[name] = _serialize(root)
    operation_results = []
    for entry in prepared:
        op = entry["op"]
        operation_results.append({"op_id": op["op_id"], "status": "applied", "kind": op["kind"],
            "part": op["part"], "xpath": op["xpath"], "region_id": op.get("region_id"),
            "before": op["before"], "after": op["after"],
            "source_start": entry["start"], "source_end": entry["end"], **results[op["op_id"]]})
    return updated, operation_results


def _canonical(root):
    return etree.tostring(root, method="c14n")


def _normalized_review(data, original, name):
    root = _parse(data, name)
    old = set()
    if original is not None:
        source_root = _parse(original, name)
        old = {(n.tag, n.get(f"{{{W}}}id")) for n in source_root.iter() if n.tag in REVIEW_TAGS}
    for node in root.iter():
        if node.tag in REVIEW_TAGS and (node.tag, node.get(f"{{{W}}}id")) not in old:
            if not node.get(f"{{{W}}}author", "").strip() or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", node.get(f"{{{W}}}date", "")):
                _fail("New revisions/comments must retain review author and UTC date")
            for attr in ("author", "date", "initials"):
                node.attrib.pop(f"{{{W}}}{attr}", None)
    if root.tag == f'{{{P}}}cmLst':
        old_ids = {(n.get('authorId'), n.get('idx')) for n in _parse(original, name)} if original else set()
        for node in root:
            if (node.get('authorId'), node.get('idx')) not in old_ids:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', node.get('dt', '')):
                    _fail('New PPT comments must retain UTC date')
                node.attrib.pop('dt', None)
    if root.tag == f'{{{P}}}cmAuthorLst':
        old_ids = {n.get('id') for n in _parse(original, name)} if original else set()
        for node in root:
            if node.get('id') not in old_ids:
                if not node.get('name', '').strip(): _fail('New PPT comments must retain author')
                node.attrib.pop('name', None); node.attrib.pop('initials', None)
    return _canonical(root)


def _read_comment_range(paragraph, comment_id):
    """Independently read actual XML anchors, without authoring offset helpers."""
    pieces = []
    locations = {"commentRangeStart": [], "commentRangeEnd": [], "commentReference": []}
    cursor, order = 0, 0

    def visit(node):
        nonlocal cursor, order
        if node.tag in {f"{{{W}}}del", f"{{{W}}}moveFrom"}:
            return
        if node is not paragraph and node.tag == f"{{{W}}}p":
            return
        order += 1
        if node.get(f"{{{W}}}id") == comment_id:
            for tag in locations:
                if node.tag == f"{{{W}}}{tag}":
                    locations[tag].append((cursor, order))
        token = None
        if node.tag == f"{{{W}}}t": token = node.text or ""
        elif node.tag == f"{{{W}}}tab": token = "\t"
        elif node.tag in {f"{{{W}}}br", f"{{{W}}}cr"}: token = "\n"
        if token is not None:
            pieces.append(token)
            cursor += len(token)
        for child in node:
            visit(child)

    visit(paragraph)
    if any(len(value) != 1 for value in locations.values()):
        return {"valid": False, "text": None, "start": None}
    begin = locations["commentRangeStart"][0]
    end = locations["commentRangeEnd"][0]
    reference = locations["commentReference"][0]
    return {"valid": begin[1] < end[1] < reference[1], "start": begin[0],
            "text": "".join(pieces)[begin[0]:end[0]]}


def _validate_comment_anchors(source, output, operations, results):
    """Check source-intended ranges against what the output really anchors.

    This is intentionally additional to rebuilding expected XML. It reads the
    serialized anchors with an independent walker, so an authoring/replay bug
    in run splitting or offset translation cannot validate its own mistake.
    """
    if not any(op.get("kind") == "comment" for op in operations): return []
    _, prepared, grouped = _prepare(source, operations, "docx")
    by_id = {result["op_id"]: result for result in results}
    parsed = {}
    issues = []
    for entry in (e for e in prepared if e["op"]["kind"] == "comment"):
        op, part = entry["op"], entry["op"]["part"]
        comment_id = by_id[op["op_id"]]["comment_id"]
        if part not in output: continue  # Package-level check reports this.
        if part not in parsed: parsed[part] = _parse(output[part], part)
        root = parsed[part]
        ns = dict(NS)
        ns.update({key: value for key, value in root.nsmap.items() if key})
        paragraphs = root.xpath(entry["paragraph_xpath"], namespaces=ns)
        actual = (_read_comment_range(paragraphs[0], comment_id) if len(paragraphs) == 1
                  else {"valid": False, "text": None, "start": None})
        # Require exactly one set of anchors in the entire part, not just in
        # the expected paragraph; duplicates elsewhere must also fail.
        for tag in ("commentRangeStart", "commentRangeEnd", "commentReference"):
            nodes = root.xpath(f".//w:{tag}[@w:id=$comment_id]", namespaces=NS, comment_id=comment_id)
            if len(nodes) != 1: actual["valid"] = False
        source_text = _text(entry["paragraph"], "docx")
        peers = grouped[(part, entry["paragraph_xpath"])]
        replacements = [e for e in peers if e["op"]["kind"] == "replace_text"]

        def intended_segment(left, right):
            segment = source_text[left:right]
            contained = [r for r in replacements if left <= r["start"] and r["end"] <= right]
            for replacement in sorted(contained, key=lambda r: r["start"], reverse=True):
                a, b = replacement["start"] - left, replacement["end"] - left
                segment = segment[:a] + replacement["op"]["after"] + segment[b:]
            return segment

        expected_start = len(intended_segment(0, entry["start"]))
        expected_text = intended_segment(entry["start"], entry["end"])
        if (not actual["valid"] or actual["start"] != expected_start
                or actual["text"] != expected_text):
            issues.append({"code": "comment_anchor_mismatch", "part": part, "op_id": op["op_id"],
                           "expected_text": expected_text, "actual_text": actual["text"],
                           "expected_start": expected_start, "actual_start": actual["start"]})
    return issues


def _property_signature(node):
    """Namespace-independent structure, without serialization whitespace."""
    return (node.tag, tuple(sorted(node.attrib.items())), node.text or "",
            tuple(_property_signature(child) for child in node))


def _read_highlight_characters(paragraph, kind):
    """Independent accepted-text walker with each character's original style.

    This intentionally avoids the authoring text/segment/translation helpers.
    Run identity is retained only to model replacement style allocation.
    """
    uri = W if kind == "docx" else A
    characters = []

    def visit(node, run=None):
        if kind == "docx":
            if node.tag in {f"{{{W}}}del", f"{{{W}}}moveFrom"}: return
            if node is not paragraph and node.tag == f"{{{W}}}p": return
        if node.tag in {f"{{{uri}}}r", f"{{{A}}}br"}: run = node
        token = None
        if node.tag == f"{{{uri}}}t": token = node.text or ""
        elif kind == "docx" and node.tag == f"{{{W}}}tab": token = "\t"
        elif node.tag in ({f"{{{W}}}br", f"{{{W}}}cr"} if kind == "docx" else {f"{{{A}}}br"}): token = "\n"
        if token is not None:
            props = run.find(f"{{{uri}}}rPr") if run is not None else None
            children = list(props) if props is not None else []
            marks = tuple(_property_signature(c) for c in children if c.tag == f"{{{uri}}}highlight")
            style = (tuple(sorted(run.attrib.items())) if run is not None else (),
                     tuple(sorted(props.attrib.items())) if props is not None else (),
                     tuple(_property_signature(c) for c in children if c.tag != f"{{{uri}}}highlight"))
            characters.extend((character, style, marks, run) for character in token)
        for child in node: visit(child, run)

    visit(paragraph)
    return characters


def _validate_highlights(source, output, operations, kind):
    """Verify actual marks and preserved styles independently of XML replay."""
    if not any(op.get("kind") == "highlight" for op in operations): return []
    _, _, grouped = _prepare(source, operations, kind)
    yellow = (((f"{{{W}}}highlight", ((f"{{{W}}}val", "yellow"),), "", ()),)
              if kind == "docx" else
              ((f"{{{A}}}highlight", (), "", ((f"{{{A}}}srgbClr", (("val", "FFFF00"),), "", ()),)),))
    issues, parsed = [], {}
    for (part, paragraph_xpath), entries in grouped.items():
        highlights = [e for e in entries if e["op"]["kind"] == "highlight"]
        if not highlights or part not in output: continue
        if part not in parsed: parsed[part] = _parse(output[part], part)
        root = parsed[part]
        ns = dict(NS)
        ns.update({key: value for key, value in root.nsmap.items() if key})
        matches = root.xpath(paragraph_xpath, namespaces=ns)
        if len(matches) != 1:
            issues.append({"code": "highlight_paragraph_missing", "part": part})
            continue
        original = _read_highlight_characters(entries[0]["paragraph"], kind)
        actual = _read_highlight_characters(matches[0], kind)
        marked = {i for entry in highlights for i in range(entry["start"], entry["end"])}
        expected = []
        cursor = 0

        def keep(left, right):
            for i in range(left, right):
                char, style, old_mark, _ = original[i]
                expected.append((char, style, yellow if i in marked else old_mark))

        for replacement in sorted((e for e in entries if e["op"]["kind"] == "replace_text"), key=lambda e: e["start"]):
            left, right = replacement["start"], replacement["end"]
            keep(cursor, left)
            # The writer distributes the new characters across the original
            # runs in order; the last run receives the remaining characters.
            styles = []
            for record in original[left:right]:
                if not styles or record[3] is not styles[-1][0]:
                    styles.append([record[3], record[1], record[2], 1])
                else: styles[-1][3] += 1
            remaining = replacement["op"]["after"]
            for index, (_, style, mark, length) in enumerate(styles):
                length = length if index < len(styles) - 1 else len(remaining)
                fragment, remaining = remaining[:length], remaining[length:]
                expected.extend((char, style, mark) for char in fragment)
            cursor = right
        keep(cursor, len(original))
        expected_text, actual_text = "".join(r[0] for r in expected), "".join(r[0] for r in actual)
        op_ids = [e["op"]["op_id"] for e in highlights]
        if actual_text != expected_text:
            issues.append({"code": "highlight_text_mismatch", "part": part, "op_ids": op_ids})
            continue
        for code, index in (("highlight_style_mismatch", 1), ("highlight_range_mismatch", 2)):
            mismatch = next((i for i, (wanted, found) in enumerate(zip(expected, actual)) if wanted[index] != found[index]), None)
            if mismatch is not None:
                issues.append({"code": code, "part": part, "op_ids": op_ids, "position": mismatch})
    return issues


def _validate_data(source, output, operations, kind):
    expected, results = _build(source, operations, kind, "validation")
    issues = []
    changed = sorted(name for name in set(source) | set(output) if source.get(name) != output.get(name))
    expected_changed = {name for name in set(source) | set(expected) if source.get(name) != expected.get(name)}
    for name in sorted(set(output) | set(expected)):
        if name not in output:
            issues.append({"code": "missing_part", "part": name})
        elif name not in expected:
            issues.append({"code": "unexpected_part", "part": name})
        elif name not in expected_changed:
            if output[name] != expected[name]: issues.append({"code": "unrelated_part_changed", "part": name})
        elif name.endswith(".xml") or name.endswith(".rels"):
            try:
                if _normalized_review(output[name], source.get(name), name) != _normalized_review(expected[name], source.get(name), name):
                    issues.append({"code": "unexpected_xml_change", "part": name,
                                   "message": "Content, run styles, review anchors/history, or relationships differ from the reviewed plan"})
            except PatchPlanError as exc:
                issues.append({"code": "invalid_xml", "part": name, "message": str(exc)})
        elif output[name] != expected[name]:
            issues.append({"code": "unexpected_part_change", "part": name})
    if kind == "docx":
        try:
            issues.extend(_validate_comment_anchors(source, output, operations, results))
        except PatchPlanError as exc:
            issues.append({"code": "comment_anchor_validation_error", "message": str(exc)})
    try:
        issues.extend(_validate_highlights(source, output, operations, kind))
    except PatchPlanError as exc:
        issues.append({"code": "highlight_validation_error", "message": str(exc)})
    for result in results:
        related = {result['part'], result.get('comment_part'), result.get('author_part')}
        result["passed"] = not any(i.get("part") in related for i in issues)
    return {"passed": not issues, "issues": issues, "changed_parts": changed, "operation_results": results}


def validate_updated(source_path, output_path, operations):
    """Check the plan, byte-identical unrelated parts, and complete expected XML.

    Review author/date may vary for *new* annotations. All old revision/comment
    metadata and every other XML attribute/element must match exactly.
    """
    try:
        source_path, output_path = Path(source_path), Path(output_path)
        kind = source_path.suffix.lower().lstrip(".")
        if kind not in {"docx", "pptx"}: _fail("Only DOCX and PPTX are supported")
        source, _, _ = _package(source_path)
        output, _, _ = _package(output_path)
        return _validate_data(source, output, operations, kind)
    except (PatchPlanError, OSError) as exc:
        return {"passed": False, "issues": [{"code": "validation_error", "message": str(exc)}],
                "changed_parts": [], "operation_results": []}


def apply_patch_plan(source_path, output_path, operations, expected_sha256, author="产品要素审阅"):
    """Validate every operation, write one atomic reviewed copy, and verify it."""
    source_path, output_path = Path(source_path).resolve(), Path(output_path).resolve()
    if os.path.normcase(str(source_path)) == os.path.normcase(str(output_path)):
        _fail("Source and output must be different paths")
    if source_path.exists() and output_path.exists() and os.path.samefile(source_path, output_path):
        _fail("Source and output resolve to the same file")
    kind = source_path.suffix.lower().lstrip(".")
    if kind not in {"docx", "pptx"} or output_path.suffix.lower() != source_path.suffix.lower():
        _fail("Source/output must have the same DOCX or PPTX extension")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        _fail("A valid expected source SHA256 is required")
    actual_hash = _sha(source_path)
    if actual_hash.lower() != expected_sha256.lower(): _fail("Source SHA256 does not match reviewed source")
    if not isinstance(author, str) or not author.strip(): _fail("Revision author is required")
    data, infos, archive_comment = _package(source_path)
    updated, operation_results = _build(data, operations, kind, author)
    validation = _validate_data(data, updated, operations, kind)
    if not validation["passed"]: _fail(f"Generated package failed validation: {validation['issues']}")
    if _sha(source_path) != actual_hash: _fail("Source changed during preparation")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".material-review-", suffix=output_path.suffix, dir=output_path.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            archive.comment = archive_comment
            for info in infos: archive.writestr(info, updated[info.filename])
            for name in updated.keys() - data.keys(): archive.writestr(name, updated[name])
        check_data, _, _ = _package(temporary)
        validation = _validate_data(data, check_data, operations, kind)
        if not validation["passed"]: _fail(f"Written package failed validation: {validation['issues']}")
        if _sha(source_path) != actual_hash: _fail("Source changed before output commit")
        os.replace(temporary, output_path)
    finally:
        if temporary.exists(): temporary.unlink()
    return {"passed": True, "output": str(output_path), "source_sha256": actual_hash,
            "output_sha256": _sha(output_path), "changed_parts": validation["changed_parts"],
            "operation_results": operation_results, "validation": validation}
