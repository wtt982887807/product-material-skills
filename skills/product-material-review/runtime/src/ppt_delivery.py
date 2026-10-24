"""Conservative product-element page suggestions and lossless PPTX subsetting.

The delivery package contains the selected slides and their reachable OPC
dependencies. Slide, layout, theme, media and note bytes are never reauthored.
Links into excluded slides fail closed rather than being silently retargeted.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import posixpath
import re
import tempfile
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
NS = {"p": P, "r": R}
ELEMENT_TITLE = re.compile(r"产品要素|基本要素|要素表|主要条款|产品条款")
NON_ELEMENT_TITLE = re.compile(r"风险(?:揭示|提示|披露|申明)|(?:管理人|公司|团队|基金经理)介绍|历史业绩|产品表现|投资策略|研究框架")
ELEMENT_FIELDS = {
    "product_name", "product_type", "product_code", "duration", "manager", "custodian",
    "distributor", "investment_advisor", "investment_scope", "investment_ratios",
    "investment_strategy", "investment_restrictions", "minimum_subscription_amount",
    "opening_schedule", "management_fee", "performance_fee", "participation_fee",
    "subscription_fee", "redemption_fee", "custody_fee", "sales_service_fee",
    "redemption_window", "subscription_window", "displayed_risk_rating",
    "lockup_period", "income_distribution", "distribution_policy", "valuation_frequency",
    "subscription_amount", "minimum_additional_subscription_amount", "investment_objective",
}
# Names/codes and institution/risk references alone are not an element table.
SUBSTANTIVE_FIELDS = ELEMENT_FIELDS - {
    "product_name", "product_code", "manager", "custodian", "distributor",
    "investment_advisor", "displayed_risk_rating",
}


def suggest_element_pages(material):
    """Suggest safe 1-based source pages from one analyze_material result.

    A title alone does not select section covers or contents pages. Performance,
    disclosures and company introductions do not qualify from incidental fields.
    ``needs_review`` blocks automatic delivery even if some safe pages exist.
    """
    slots = defaultdict(list)
    for slot in material.get("slots", []):
        slots[slot.get("region_id")].append(slot)
    by_page = defaultdict(list)
    for region in material.get("regions", []):
        for page in {loc.get("page") for loc in region.get("locations", [])}:
            if type(page) is int and page > 0:
                by_page[page].append(region)
    selected, reviews, excluded, evidence, reasons = [], [], [], [], []
    for page, regions in sorted(by_page.items()):
        candidates = []
        for region in regions:
            fields = {s.get("field_id") for s in slots[region.get("region_id")]}
            fields &= ELEMENT_FIELDS
            substantive = fields & SUBSTANTIVE_FIELDS
            title = re.sub(r"\s+", "", region.get("title", ""))
            table = any(loc.get("table") for loc in region.get("locations", []))
            marker = bool(ELEMENT_TITLE.search(title))
            continuation = region.get("continuation", {})
            if NON_ELEMENT_TITLE.search(title) and not marker:
                continue
            qualifies = (table and continuation.get("element_chain") and
                         continuation.get("status") in {"confirmed", "needs_review"}) or bool(substantive) and (
                marker or (table and "product_name" in fields and len(fields) >= 3)
                or len(substantive) >= 3)
            if qualifies:
                candidates.append(region)
                evidence.append({"page": page, "region_id": region.get("region_id"),
                                 "status": region.get("status"), "title": region.get("title", ""),
                                 "fields": sorted(fields), "table": bool(table),
                                 **({"continuation": continuation} if continuation else {})})
        if not candidates:
            excluded.append(page)
            continue
        targets = [r for r in candidates if r.get("status") == "target"]
        ambiguous = [r for r in candidates if r.get("status") not in {"target", "other"}
                     or r.get("continuation", {}).get("status") == "needs_review"]
        # Generic unscoped title/footer regions are not another product, but any
        # explicit competing identity or unscoped element fields require review.
        conflicts = [r for r in regions if r.get("status") == "other" or
                     (r.get("status") != "target" and (r.get("product_names") or
                      any(s.get("field_id") in SUBSTANTIVE_FIELDS for s in slots[r.get("region_id")]))) or
                     any(s.get("product_status", r.get("status")) != "target" and
                         s.get("field_id") in ELEMENT_FIELDS for s in slots[r.get("region_id")])]
        if ambiguous or (targets and conflicts):
            reviews.append(page)
            reasons.append({"page": page, "code": "mixed_or_ambiguous_element_page",
                            "region_ids": list(dict.fromkeys(r.get("region_id") for r in [*ambiguous, *conflicts]))})
        elif targets:
            selected.append(page)
        else:
            excluded.append(page)
            reasons.append({"page": page, "code": "other_product_elements"})
    if not selected and not reviews:
        reasons.append({"code": "no_confident_target_element_pages"})
    return {"pages": selected, "needs_review": bool(reviews) or not selected,
            "review_pages": reviews, "excluded_pages": excluded, "reasons": reasons,
            "evidence": evidence, "policy": "target_elements_only_whole_slide_scope"}


def _xml(blob):
    if b"<!DOCTYPE" in blob.upper():
        raise ValueError("PPTX XML with a DTD is unsupported")
    return etree.fromstring(blob, etree.XMLParser(resolve_entities=False, no_network=True))


def _serialize(root):
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _read_package(blob):
    with ZipFile(BytesIO(blob)) as package:
        names = [i.filename for i in package.infolist() if not i.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate PPTX package members are unsupported")
        if any(name.startswith("/") or "\\" in name or posixpath.normpath(name) != name for name in names):
            raise ValueError("Unsafe or noncanonical PPTX package member")
        return {name: package.read(name) for name in names}


def _rels_name(part):
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels") if part else "_rels/.rels"


def _target(part, target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query:
        raise ValueError(f"Invalid internal relationship target: {target}")
    path = unquote(parsed.path)
    path = posixpath.normpath(path.lstrip("/") if path.startswith("/") else posixpath.join(posixpath.dirname(part), path))
    if path == ".." or path.startswith("../") or "\\" in path:
        raise ValueError(f"Unsafe internal relationship target: {target}")
    return path


def _relationships(parts, part):
    name = _rels_name(part)
    if name not in parts:
        return None, {}
    root = _xml(parts[name])
    if root.tag != f"{{{REL}}}Relationships":
        raise ValueError(f"Unsupported relationships namespace: {name}")
    result = {}
    for node in root:
        rid = node.get("Id")
        if not rid or rid in result:
            raise ValueError(f"Invalid or duplicate relationship Id: {name}")
        result[rid] = node
    return root, result


def _slide_order(parts):
    root, rels = _relationships(parts, "")
    office = [n for n in rels.values() if n.get("Type", "").endswith("/officeDocument")]
    if len(office) != 1 or office[0].get("TargetMode") == "External":
        raise ValueError("PPTX must have one internal officeDocument relationship")
    part = _target("", office[0].get("Target", ""))
    if part not in parts:
        raise ValueError(f"Missing presentation part: {part}")
    presentation = _xml(parts[part])
    if presentation.tag != f"{{{P}}}presentation":
        raise ValueError("Only transitional OOXML PowerPoint presentations are supported")
    _, relationships = _relationships(parts, part)
    slides = []
    for slide_id in presentation.findall("p:sldIdLst/p:sldId", NS):
        rid = slide_id.get(f"{{{R}}}id")
        relation = relationships.get(rid)
        if relation is None or not relation.get("Type", "").endswith("/slide") or relation.get("TargetMode") == "External":
            raise ValueError(f"Invalid slide relationship: {rid}")
        target = _target(part, relation.get("Target", ""))
        if target not in parts:
            raise ValueError(f"Missing slide part: {target}")
        slides.append({"part": target, "relationship_id": rid, "slide_id": slide_id.get("id")})
    if len({s["part"] for s in slides}) != len(slides):
        raise ValueError("Duplicate slide references are unsupported")
    return part, presentation, slides


def _pages(pages, count):
    if not isinstance(pages, (list, tuple)) or not pages or any(type(p) is not int for p in pages):
        raise ValueError("pages must be a nonempty list of 1-based integers")
    if list(pages) != sorted(set(pages)) or min(pages) < 1 or max(pages) > count:
        raise ValueError("pages must be unique, ascending, 1-based source page numbers within range")
    return list(pages)


def _subset(original, pages):
    parts = dict(original)
    if any(name.startswith("_xmlsignatures/") for name in parts):
        raise ValueError("Digitally signed presentations require explicit signature removal before delivery")
    presentation_part, presentation, slides = _slide_order(parts)
    pages = _pages(pages, len(slides))
    selected = [slides[page - 1] for page in pages]
    selected_parts = {s["part"] for s in selected}
    slide_parts = {s["part"] for s in slides} | {n for n in parts if re.fullmatch(r"ppt/slides/[^/]+\.xml", n)}
    excluded_parts = slide_parts - selected_parts
    selected_rids = {s["relationship_id"] for s in selected}
    slide_list = presentation.find("p:sldIdLst", NS)
    for slide_id in list(slide_list):
        if slide_id.get(f"{{{R}}}id") not in selected_rids:
            slide_list.remove(slide_id)
    # Whole-deck navigation and cached sections cannot keep old page references.
    for node in presentation.xpath('.//p:custShowLst | .//p:showPr | .//*[local-name()="sectionLst"]', namespaces=NS):
        node.getparent().remove(node)
    parts[presentation_part] = _serialize(presentation)
    relroot, relmap = _relationships(parts, presentation_part)
    for relation in list(relroot):
        if relation.get("Type", "").endswith("/slide") and relation.get("Id") not in selected_rids:
            relroot.remove(relation)
    parts[_rels_name(presentation_part)] = _serialize(relroot)
    reachable, pending = set(), [""]
    while pending:
        part = pending.pop()
        if part in reachable:
            continue
        if part:
            if part in excluded_parts:
                raise ValueError(f"Internal relationship points to an excluded slide: {part}")
            if part not in parts:
                raise ValueError(f"Dangling relationship to missing part: {part}")
            reachable.add(part)
        relroot, relmap = _relationships(parts, part)
        if relroot is None:
            continue
        changed = False
        for relation in list(relroot):
            kind = relation.get("Type", "")
            if kind.endswith("/thumbnail"):
                relroot.remove(relation)
                changed = True
                continue
            if relation.get("TargetMode") == "External":
                continue
            target = _target(part, relation.get("Target", ""))
            if target in excluded_parts:
                raise ValueError(f"Internal link from {part} points to an excluded slide: {target}")
            if target not in parts:
                raise ValueError(f"Dangling relationship from {part} to missing part: {target}")
            pending.append(target)
        relname = _rels_name(part)
        reachable.add(relname)
        if changed:
            parts[relname] = _serialize(relroot)
    for part in selected_parts:
        # Dynamic slide jumps could acquire a new destination after extraction.
        root = _xml(parts[part])
        if excluded_parts and root.xpath('//@action[starts-with(., "ppaction://hlinkshowjump")]'):
            raise ValueError(f"Relative slide navigation requires review before removing slides: {part}")
    if "[Content_Types].xml" not in parts:
        raise ValueError("Missing [Content_Types].xml")
    types = _xml(parts["[Content_Types].xml"])
    for node in list(types):
        if node.tag == f"{{{CT}}}Override" and unquote(node.get("PartName", "")).lstrip("/") not in reachable:
            types.remove(node)
    parts["[Content_Types].xml"] = _serialize(types)
    reachable.add("[Content_Types].xml")
    for name in list(reachable):
        if name.endswith(".xml") and _xml(parts[name]).tag == f"{{{EP}}}Properties":
            props = _xml(parts[name])
            notes = sum(bool(re.search(r"/notesSlides/[^/]+\.xml$", p)) for p in reachable)
            hidden = sum(_xml(parts[s["part"]]).get("show", "1") in {"0", "false"} for s in selected)
            for key, value in {"Slides": len(selected), "Notes": notes, "HiddenSlides": hidden}.items():
                node = props.find(f"{{{EP}}}{key}")
                if node is not None:
                    node.text = str(value)
            for key in ("HeadingPairs", "TitlesOfParts", "Words", "Paragraphs", "TotalTime"):
                for node in props.findall(f"{{{EP}}}{key}"):
                    props.remove(node)
            parts[name] = _serialize(props)
    output = {name: data for name, data in parts.items() if name in reachable}
    for part, blob in output.items():
        if not part.endswith((".xml", ".vml")) or part == "[Content_Types].xml":
            continue
        _, relationships = _relationships(output, part)
        for node in _xml(blob).iter():
            for key, value in node.attrib.items():
                if key.startswith("{" + R + "}") and value and value not in relationships:
                    raise ValueError(f"Unresolved relationship {value} in {part}")
    mapping = [{"output_page": i, "source_page": page, **slide}
               for i, (page, slide) in enumerate(zip(pages, selected), 1)]
    return output, mapping, len(slides)


def validate_slide_delivery(source, output, pages):
    """Verify exact selected-slide bytes, expected OPC dependencies and metadata.

    This is structural/package validation; it does not claim PowerPoint rendering.
    Returns a report even when validation fails, so CLI callers can save evidence.
    """
    checks, errors, mapping = {}, [], []
    try:
        original = _read_package(Path(source).read_bytes())
        actual = _read_package(Path(output).read_bytes())
        expected, mapping, source_count = _subset(original, pages)
        _, _, actual_slides = _slide_order(actual)
        checks["slide_order"] = [s["part"] for s in actual_slides] == [s["part"] for s in mapping]
        checks["slide_bytes_preserved"] = all(actual.get(s["part"]) == original[s["part"]] for s in mapping)
        checks["exact_dependency_parts"] = set(actual) == set(expected)
        checks["dependency_and_metadata_bytes"] = all(actual.get(name) == blob for name, blob in expected.items())
        for check, valid in checks.items():
            if not valid:
                errors.append(check)
        if set(actual) != set(expected):
            errors.append({"unexpected_parts": sorted(set(actual) - set(expected)), "missing_parts": sorted(set(expected) - set(actual))})
        return {"valid": not errors, "checks": checks, "errors": errors, "slide_mapping": mapping,
                "source_slide_count": source_count, "output_slide_count": len(actual_slides),
                "validation_scope": "OOXML_structure_and_exact_source_preservation"}
    except (BadZipFile, ValueError, KeyError, OSError, etree.XMLSyntaxError, TypeError) as error:
        return {"valid": False, "checks": checks, "errors": [*errors, str(error)], "slide_mapping": mapping}


def export_selected_slides(source, output, pages, expected_sha256):
    """Write a new PPTX with unique ascending source pages, retaining their bytes.

    The source hash is mandatory. Existing destinations are refused. Preparation
    and validation complete before atomically publishing a new output file.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or (output.exists() and os.path.samefile(source, output)):
        raise ValueError("Cannot overwrite the source presentation")
    if source.suffix.lower() != ".pptx" or output.suffix.lower() != ".pptx":
        raise ValueError("Slide delivery requires .pptx source and output paths")
    blob = source.read_bytes()
    actual_hash = sha256(blob).hexdigest()
    if not isinstance(expected_sha256, str) or expected_sha256.lower() != actual_hash:
        raise ValueError("Source SHA256 does not match the reviewed input")
    if output.exists():
        raise FileExistsError(f"Delivery output already exists: {output}")
    parts, mapping, source_count = _subset(_read_package(blob), pages)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ppt-delivery-", suffix=".pptx", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as package:
            for name, data in parts.items():
                package.writestr(name, data)
        report = validate_slide_delivery(source, temporary, pages)
        if not report["valid"]:
            raise ValueError(f"PPTX delivery validation failed: {report['errors']}")
        if sha256(source.read_bytes()).hexdigest() != actual_hash:
            raise ValueError("Source SHA256 changed during export")
        # link is an atomic create-if-absent on the same filesystem, unlike replace.
        os.link(temporary, output)
        return {**report, "source": str(source), "output": str(output), "pages": list(pages),
                "source_sha256": actual_hash, "output_sha256": sha256(output.read_bytes()).hexdigest(),
                "removed_slide_count": source_count - len(mapping), "retained_parts": len(parts)}
    finally:
        temporary.unlink(missing_ok=True)
