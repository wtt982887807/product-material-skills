"""Create a clean Word baseline by accepting historical revisions with Word itself.

The uploaded document is never opened for editing. Pure-Python handling only
inspects OOXML and copies revision-free documents; it does not emulate Word's
accept semantics for paragraphs, tables, moves or formatting revisions.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
STRICT_W = 'http://purl.oclc.org/ooxml/wordprocessingml/main'
REVISION_NAMES = {
    'ins', 'del', 'moveFrom', 'moveTo',
    'moveFromRangeStart', 'moveFromRangeEnd', 'moveToRangeStart', 'moveToRangeEnd',
    'customXmlInsRangeStart', 'customXmlInsRangeEnd', 'customXmlDelRangeStart', 'customXmlDelRangeEnd',
    'customXmlMoveFromRangeStart', 'customXmlMoveFromRangeEnd',
    'customXmlMoveToRangeStart', 'customXmlMoveToRangeEnd',
    'rPrChange', 'pPrChange', 'tblPrChange', 'tblPrExChange', 'tblGridChange',
    'trPrChange', 'tcPrChange', 'sectPrChange', 'numberingChange',
    'cellIns', 'cellDel', 'cellMerge', 'conflictIns', 'conflictDel',
    'customXmlConflictInsertionRangeStart', 'customXmlConflictInsertionRangeEnd',
    'customXmlConflictDeletionRangeStart', 'customXmlConflictDeletionRangeEnd',
}


def _name(tag):
    if not isinstance(tag, str) or not tag.startswith('{'):
        return '', tag
    return tag[1:].split('}', 1)


def _word_namespace(namespace):
    return namespace in {W, STRICT_W} or namespace.startswith('http://schemas.microsoft.com/office/word/')


def _xml(data, part):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Word XML DTD/entity不受支持：' + part)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError('Word XML无法读取：' + part) from exc


def _comment_text(comment):
    paragraphs = []
    for paragraph in comment.iter():
        ns, local = _name(paragraph.tag)
        if local != 'p' or not _word_namespace(ns):
            continue
        text = []
        for node in paragraph.iter():
            child_ns, child_name = _name(node.tag)
            if not _word_namespace(child_ns):
                continue
            if child_name == 't': text.append(node.text or '')
            elif child_name == 'tab': text.append('\t')
            elif child_name in {'br', 'cr'}: text.append('\n')
        paragraphs.append(''.join(text))
    return '\n'.join(paragraphs)


def _inspect_blob(blob):
    revisions, comments = Counter(), []
    try:
        with ZipFile(BytesIO(blob)) as package:
            names = package.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Word包包含重复成员，不能安全预处理')
            if 'word/document.xml' not in names:
                raise ValueError('不是可读取的DOCX文档')
            for part in names:
                if not part.startswith('word/') or not part.lower().endswith('.xml'):
                    continue
                root = _xml(package.read(part), part)
                for node in root.iter():
                    namespace, local = _name(node.tag)
                    if _word_namespace(namespace) and local in REVISION_NAMES:
                        revisions[local] += 1
                namespace, local = _name(root.tag)
                if local == 'comments' and _word_namespace(namespace):
                    for comment in root:
                        cns, cname = _name(comment.tag)
                        if cname != 'comment' or not _word_namespace(cns):
                            continue
                        attrs = {_name(key)[1]: value for key, value in comment.attrib.items()}
                        comments.append({'author':attrs.get('author',''), 'initials':attrs.get('initials',''),
                                         'text':_comment_text(comment)})
    except BadZipFile as exc:
        raise ValueError('无法读取DOCX包') from exc
    return {'revision_count':sum(revisions.values()), 'comments_count':len(comments),
            'revision_tags':dict(sorted(revisions.items())), 'comments':comments}


def inspect_word_revisions(source):
    """Inspect every Word XML story, including headers, footnotes and properties."""
    source = Path(source)
    if source.suffix.lower() != '.docx':
        raise ValueError('Word修订预处理仅支持.docx')
    return _inspect_blob(source.read_bytes())


def has_tracked_revisions(source):
    return inspect_word_revisions(source)['revision_count'] > 0


_ACCEPT_SCRIPT = r'''
param([Parameter(Mandatory=$true)][string]$WorkingPath,
      [Parameter(Mandatory=$true)][string]$ReportPath)
$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
$ownedEmptyInstance = $false
$success = $false
try {
    $word = New-Object -ComObject Word.Application
    $ownedEmptyInstance = ($word.Documents.Count -eq 0)
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $doc = $word.Documents.Open($WorkingPath, $false, $false, $false)
    if ($doc.ReadOnly) { throw 'Working copy opened read-only' }
    $before = [int]$doc.Revisions.Count
    $commentsBefore = [int]$doc.Comments.Count
    $doc.AcceptAllRevisions()
    $doc.TrackRevisions = $false
    $remaining = [int]$doc.Revisions.Count
    $commentsAfter = [int]$doc.Comments.Count
    if ($remaining -ne 0) { throw "Word still reports $remaining revisions" }
    $doc.Save()
    [ordered]@{
        revision_count_before = $before
        remaining_revision_count = $remaining
        comments_before = $commentsBefore
        comments_after = $commentsAfter
    } | ConvertTo-Json | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $success = $true
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
} finally {
    if ($null -ne $doc) {
        try { $doc.Close(0) } catch {}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc)
    }
    if ($null -ne $word) {
        if ($ownedEmptyInstance) {
            try { if ($word.Documents.Count -eq 0) { $word.Quit(0) } } catch {}
        }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
if (-not $success) { exit 1 }
'''


def _run_word_accept_all(working_path):
    """Native boundary; only the private working copy is passed to Word."""
    if os.name != 'nt':
        raise RuntimeError('含历史修订的Word模板需要Windows Microsoft Word；不能用XML模拟接受修订')
    executable = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not executable:
        raise RuntimeError('接受Word历史修订需要Windows PowerShell及Microsoft Word')
    with tempfile.TemporaryDirectory(prefix='word-accept-script-') as directory:
        folder = Path(directory)
        script, report = folder/'accept_revisions.ps1', folder/'result.json'
        script.write_text(_ACCEPT_SCRIPT, encoding='utf-8-sig')
        args = [executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', str(script), '-WorkingPath', str(Path(working_path).resolve()), '-ReportPath', str(report)]
        try:
            process = subprocess.run(args, capture_output=True, timeout=180,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Microsoft Word接受历史修订超时；没有生成最终基线') from exc
        if process.returncode or not report.is_file():
            detail = (process.stderr or process.stdout or b'Word COM operation failed').decode('utf-8', errors='replace').strip()
            raise RuntimeError('需要可用的Windows Microsoft Word完成历史修订预处理：' + detail[-2000:])
        try:
            return json.loads(report.read_text(encoding='utf-8-sig'))
        except (ValueError, OSError) as exc:
            raise RuntimeError('Microsoft Word未返回有效的历史修订处理报告') from exc


def _comment_fingerprints(report):
    return Counter((c['author'], c['initials'], c['text']) for c in report['comments'])


def prepare_word(source, output, *, expected_source_sha256=None):
    """Accept all historical revisions into a new baseline; never overwrite inputs."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if source.suffix.lower() != '.docx' or output.suffix.lower() != '.docx':
        raise ValueError('Word修订预处理的来源和输出均须为.docx')
    if source == output:
        raise ValueError('禁止覆盖上传的Word源文件')
    if output.exists():
        raise FileExistsError('Word基线输出已存在：' + str(output))
    original = source.read_bytes()
    original_hash = sha256(original).hexdigest()
    if expected_source_sha256 is not None and (not isinstance(expected_source_sha256, str)
                                               or expected_source_sha256.lower() != original_hash):
        raise ValueError('Word来源SHA256哈希与预期不一致')
    before = _inspect_blob(original)
    output.parent.mkdir(parents=True, exist_ok=True)
    native = None
    with tempfile.TemporaryDirectory(prefix='.word-preparation-', dir=output.parent) as directory:
        working = Path(directory)/'working.docx'
        working.write_bytes(original)
        if before['revision_count']:
            native = _run_word_accept_all(working)
            if not isinstance(native, dict) or native.get('remaining_revision_count') != 0:
                raise RuntimeError('Microsoft Word报告仍有历史修订，禁止发布基线')
        baseline = working.read_bytes()
        after = _inspect_blob(baseline)
        if after['revision_count']:
            raise RuntimeError('Word基线OOXML仍有历史修订：' + str(after['revision_tags']))
        if _comment_fingerprints(before) != _comment_fingerprints(after):
            raise RuntimeError('接受历史修订后原批注内容未完整保留，禁止发布基线')
        if sha256(source.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError('Word源文件在预处理期间变化，禁止发布基线')
        # Same-volume create-if-absent: an existing destination is never replaced.
        os.link(working, output)
        try:
            if sha256(source.read_bytes()).hexdigest() != original_hash:
                raise RuntimeError('Word源文件在发布基线期间变化')
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    record = {'policy':'accept_all_existing_revisions', 'original_path':str(source),
              'original_sha256':original_hash, 'baseline_path':str(output),
              'baseline_sha256':sha256(baseline).hexdigest(),
              'revision_count_before':before['revision_count'], 'remaining_revision_count':0,
              'revision_count_unit':'OOXML revision elements',
              'comments_before':before['comments_count'], 'comments_after':after['comments_count'],
              'method':'microsoft_word_accept_all' if before['revision_count'] else 'copy_no_revisions'}
    if native is not None:
        record['native_word_report'] = native
    return record
