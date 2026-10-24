"""Search retained contract text and bind reviewed extractions to the real source.

The dictionary is extensible vocabulary, not a limit on template requirements.
This module verifies source/version/quotation bindings, not an AI's interpretation
of a clause or the correctness of a calculation or inapplicability judgment.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata


UNREADABLE_QUALITY = {"no_text", "garbled_text", "ocr_unverified"}
UNREADABLE_PARSE = {"ocr_required", "garbled_text", "parse_error", "legacy_pending", "unsupported"}
SOURCE_COORDINATES = {"part", "paragraph", "table", "row", "page", "xpath", "section_path",
                      "paragraph_index_scope", "xml_paragraph_sibling_index", "converted_copy"}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}须为非空字符串")
    return value


def _digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(f"{label}缺少有效SHA256")
    return value.lower()


def _contract(task):
    if not isinstance(task, dict) or not isinstance(task.get("contract"), dict):
        raise ValueError("任务缺少合同原文底稿")
    contract = task["contract"]
    source = contract.get("source")
    if not isinstance(source, dict):
        raise ValueError("任务缺少合同来源版本")
    return contract, source


def _file_digest(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_state(source):
    pairs = [(source.get("path"), source.get("sha256"))]
    if source.get("converted_path"):
        pairs.append((source["converted_path"], source.get("converted_sha256")))
    for path, expected in pairs:
        try:
            _text(path, "合同来源路径")
            expected = _digest(expected, "合同来源")
        except ValueError as error:
            return "invalid", str(error)
        try:
            actual = _file_digest(path)
        except (OSError, ValueError) as error:
            return "unavailable", f"合同原文件或转换副本不可读取：{type(error).__name__}"
        if actual != expected:
            return "changed", "合同原文件或转换副本已变化，须重新分析后回查。"
    return "verified", "当前合同原文件及已登记转换副本的哈希与底稿一致。"


def _block_index(blocks):
    if not isinstance(blocks, list):
        raise ValueError("合同原文块须为列表")
    result = {}
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("合同原文块格式无效")
        identity = _text(block.get("block_id"), "合同block_id")
        if identity in result:
            raise ValueError("合同原文block_id重复，不能确定唯一引用位置")
        if not isinstance(block.get("text"), str) or not isinstance(block.get("location"), dict):
            raise ValueError("合同原文块缺少text或location")
        result[identity] = block
    return result


def _field_registry(task, review):
    existing = task.get("field_dictionary")
    if existing is None:
        # Legacy tasks have no dictionary snapshot. Resolve bundled config from
        # this module, never from cwd or another computer's absolute directory.
        existing = []
        root = Path(__file__).resolve().parents[1] / "config"
        for name in ("field_dictionary.json", "contract_field_extensions.json"):
            data = json.loads((root / name).read_text(encoding="utf-8-sig"))
            existing.extend(data if isinstance(data, list) else data.get("fields", []))
    if not isinstance(existing, list):
        raise ValueError("任务字段字典快照须为列表")
    fields = {}
    for field in existing:
        if not isinstance(field, dict):
            raise ValueError("字段字典条目格式无效")
        identity = _text(field.get("field_id"), "field_id")
        fields.setdefault(identity, deepcopy(field))
    additions = review.get("new_fields", [])
    if not isinstance(additions, list):
        raise ValueError("new_fields须为列表")
    for field in additions:
        if not isinstance(field, dict):
            raise ValueError("new_fields条目须为字段定义")
        identity = _text(field.get("field_id"), "新field_id")
        if identity != identity.strip() or identity in fields:
            raise ValueError("new_fields不可重定义已有字段，field_id须唯一且无首尾空格")
        _text(field.get("name"), "新字段名称")
        _text(field.get("definition"), "新字段定义")
        aliases = field.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() for a in aliases):
            raise ValueError("新字段aliases须为非空名称列表")
        if len(aliases) != len(set(aliases)):
            raise ValueError("新字段aliases不得重复")
        fields[identity] = deepcopy(field)
    return fields


def _source_blocks(source):
    """Parse the source once for all records, without repeating field extraction."""
    state, message = _source_state(source)
    if state != "verified":
        raise ValueError(message)
    source_hash = _digest(source.get("sha256"), "合同来源")
    path = Path(source["path"])
    parse_path = Path(source["converted_path"]) if path.suffix.lower() == ".doc" and source.get("converted_path") else path
    if parse_path.suffix.lower() not in {".docx", ".pdf"}:
        raise ValueError("合同原文不可直接读取；DOC需先登记可信DOCX转换副本。")
    import corpus
    parsed = corpus.parse_document(parse_path, "contract-" + source_hash[:16])
    blocks = parsed.get("blocks", [])
    if not blocks or parsed.get("parse_status") in {"ocr_required", "parse_error", "legacy_pending", "unsupported"}:
        raise ValueError("合同原文不可读，不能把快照中的文字直接确认为本次合同证据。")
    if parse_path.suffix.lower() == ".docx":
        from contract_facts import _docx_metadata
        _docx_metadata(parse_path, blocks)
    if path.suffix.lower() == ".doc":
        for block in blocks:
            block["location"]["converted_copy"] = str(parse_path)
    state, message = _source_state(source)
    if state != "verified":
        raise ValueError(message)
    return _block_index(blocks)


def _validated_citation(block, quote, source, originals):
    original = originals.get(block["block_id"])
    if original is None or original["text"] != block["text"]:
        raise ValueError("回查块与实际源合同原文不一致，须重新分析；不能修改底稿制造引用。")
    expected = _digest(source.get("sha256"), "合同来源")
    if _digest(block.get("source_sha256"), "原文块来源") != expected:
        raise ValueError("引用原文块不属于本合同来源版本")
    location = block["location"]
    if location.get("source_sha256") and _digest(location["source_sha256"], "引用位置来源") != expected:
        raise ValueError("引用位置的合同来源版本不一致")
    for candidate_path in [block.get("source_path"), location.get("source_path")]:
        if candidate_path and Path(candidate_path).resolve() != Path(source["path"]).resolve():
            raise ValueError("引用原文块来源路径不属于本合同")
    for coordinate in SOURCE_COORDINATES:
        if coordinate in location or coordinate in original["location"]:
            if location.get(coordinate) != original["location"].get(coordinate):
                raise ValueError("回查引用位置与实际源合同不一致")
    if block.get("quality") in UNREADABLE_QUALITY or original.get("quality") in UNREADABLE_QUALITY:
        raise ValueError("引用块原文不可读或OCR尚未确认，不能作为已回查合同证据")
    if quote not in original["text"]:
        raise ValueError("引用quote必须逐字存在于对应合同原文块，不接受改写或伪造引文")
    return {"block_id": block["block_id"], "quote": quote,
            "location": deepcopy(location), "source_sha256": expected,
            "text_sha256": sha256(original["text"].encode("utf-8")).hexdigest(),
            "quote_sha256": sha256(quote.encode("utf-8")).hexdigest()}


def validate_rechecks(task, review):
    """Return validated contract facts keyed by recheck_id; never user supplies.

    Every citation must match the current original's retained block and exact
    quote. ``reviewed=True`` records the reviewer's semantic decision; this code
    cannot prove that a summary, derived number or applicability is correct.
    """
    if not isinstance(review, dict):
        raise ValueError("审阅记录须为对象")
    fields = _field_registry(task, review)
    records = review.get("contract_rechecks", [])
    if not isinstance(records, list):
        raise ValueError("contract_rechecks须为列表")
    if not records:
        return {}
    contract, source = _contract(task)
    source_hash = _digest(source.get("sha256"), "合同来源")
    blocks = _block_index(contract.get("blocks", []))
    originals = _source_blocks(source)
    validated = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("合同回查记录须为对象")
        identity = _text(record.get("recheck_id"), "recheck_id")
        if identity in validated:
            raise ValueError("recheck_id重复")
        field_id = _text(record.get("field_id"), "回查字段field_id")
        if field_id not in fields:
            raise ValueError("回查字段未定义：请先在new_fields登记名称和定义，字典不是需求白名单。")
        if record.get("reviewed") is not True:
            raise ValueError("合同回查须明确reviewed=true")
        reason = _text(record.get("reason"), "回查审阅理由")
        if _digest(record.get("source_sha256"), "回查来源") != source_hash:
            raise ValueError("回查来源SHA256与本任务合同不一致")
        value = record.get("value")
        if value is None or value == "" or value == [] or value == {}:
            raise ValueError("回查须提供值或明确的不适用状态，不能用空值代替缺项核实")
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("回查value须为可保存的JSON值") from error
        citations = record.get("citations")
        if not isinstance(citations, list) or not citations:
            raise ValueError("合同回查必须引用非空原文证据，搜索未命中不能证明不存在")
        checked, seen, source_ids = [], set(), []
        for citation in citations:
            if not isinstance(citation, dict):
                raise ValueError("回查引用须为block_id与quote对象")
            block_id = _text(citation.get("block_id"), "引用block_id")
            quote = _text(citation.get("quote"), "引用quote")
            if block_id not in blocks:
                raise ValueError("引用block_id不存在于本任务合同原文")
            if (block_id, quote) in seen:
                raise ValueError("同一回查记录重复引用相同原文")
            seen.add((block_id, quote))
            checked.append(_validated_citation(blocks[block_id], quote, source, originals))
            if block_id not in source_ids:
                source_ids.append(block_id)
        validated[identity] = {"fact_id": identity, "recheck_id": identity, "field_id": field_id,
            "name": fields[field_id].get("name", field_id), "value": deepcopy(value),
            "raw_text": "\n\n".join(c["quote"] for c in checked), "citations": checked,
            "status": "reviewed_contract_recheck", "source_kind": "contract_recheck", "provenance": "contract",
            "source_locations": [deepcopy(blocks[bid]["location"]) for bid in source_ids],
            "source_block_ids": source_ids, "source_path": source["path"], "source_sha256": source_hash,
            "reason": reason, "reviewed": True, "semantic_verification_performed": False,
            "validation_scope": "current_source_exact_quotes_and_explicit_review_only"}
    state, message = _source_state(source)
    if state != "verified":
        raise ValueError(message)
    return validated


def _normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _search_block(block, source_hash):
    if block is None:
        return None
    return {"block_id": block["block_id"], "text": block["text"], "location": deepcopy(block["location"]),
            "source_sha256": source_hash, "text_sha256": sha256(block["text"].encode("utf-8")).hexdigest(),
            "section_path": deepcopy(block.get("section_path", [])), "kind": block.get("kind"),
            "role": block.get("role", "body"), "quality": block.get("quality")}


def search_contract(task, queries, limit=20):
    """Search snapshot text and heading context, retaining exact source strings.

    Queries are literal alternatives, normalized only for retrieval. Returned
    text/quotes stay exact. No match, incomplete text, or attachment warnings
    must never be converted into certified absence or inapplicability.
    """
    if isinstance(queries, str):
        queries = [queries]
    if not isinstance(queries, list) or not queries:
        raise ValueError("queries须为非空字符串或字符串列表")
    queries = list(dict.fromkeys(_text(query, "检索词").strip() for query in queries))
    if type(limit) is not int or limit < 1:
        raise ValueError("limit须为正整数")
    contract, source = _contract(task)
    blocks = list(_block_index(contract.get("blocks", [])).values())
    source_hash = _digest(source.get("sha256"), "合同来源")
    source_state, source_message = _source_state(source)
    coverage = deepcopy(contract.get("coverage", {}))
    parse_status = coverage.get("parse_status", source.get("parse_status"))
    unreadable = not blocks or parse_status in UNREADABLE_PARSE or bool(coverage.get("unreadable_pages"))
    warnings = []
    if source_state != "verified":
        warnings.append({"code": "source_" + source_state, "message": source_message})
    if unreadable:
        warnings.append({"code": "unreadable_source_content", "message": "合同部分或全部原文不可读；检索空结果不证明条款不存在，须回看源文件或可读转换。"})
    if coverage.get("all_source_content_parsed") is False:
        warnings.append({"code": "source_content_incomplete", "message": "图片、嵌入对象或附件可能未完整解析，不能把当前文本检索当成完整合同核实。"})
    for issue in contract.get("issues", []):
        if isinstance(issue, dict) and issue.get("code") in {"unparsed_embedded_content", "unresolved_cross_reference"}:
            warnings.append(deepcopy(issue))
    normalized = [(query, _normalized(query)) for query in queries]
    hits = []
    for position, block in enumerate(blocks):
        body = _normalized(block["text"])
        context = _normalized(" / ".join(block.get("section_path", []) or block["location"].get("section_path", [])))
        body_hits = [query for query, pattern in normalized if pattern in body]
        context_hits = [query for query, pattern in normalized if pattern in context]
        if not body_hits and not context_hits:
            continue
        match = _search_block(block, source_hash)
        match.update(matched_queries=list(dict.fromkeys([*body_hits, *context_hits])),
                     match_basis=(["text"] if body_hits else []) + (["heading_context"] if context_hits else []),
                     previous_block=_search_block(blocks[position - 1] if position else None, source_hash),
                     next_block=_search_block(blocks[position + 1] if position + 1 < len(blocks) else None, source_hash))
        hits.append(((-len(body_hits), -len(context_hits), position), match))
    hits.sort(key=lambda item: item[0])
    matches = [match for _, match in hits[:limit]]
    status = "matches_found" if matches else "source_unreadable" if unreadable else "no_match"
    return {"queries": queries, "source": deepcopy(source), "source_status": source_state,
            "source_status_message": source_message, "status": status, "matches": matches,
            "match_count": len(matches), "total_match_count": len(hits), "truncated": len(hits) > limit,
            "coverage": coverage, "warnings": warnings, "absence_proven": False,
            "interpretation": "检索本次分析保留的原文块及标题上下文；未命中不证明合同没有该条款。命中不证明适用或摘要语义正确，须查看上下文并提交来源绑定回查。"}
