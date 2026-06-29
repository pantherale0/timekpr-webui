#!/usr/bin/env python3
"""Validate translation key references and flag hardcoded user-visible strings."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import (  # noqa: E402
    DEFAULT_LOCALE,
    I18N_ROOT,
    REPO_ROOT,
    android_string_name,
    discover_locales,
    iter_leaf_strings,
    load_catalog,
    validate_catalogs,
)

COMMENT_MARKER = '<!-- guardian-i18n-check -->'

# ── Key extraction patterns ───────────────────────────────────────────────────

SERVER_KEY_RE = re.compile(
    r"""(?:\bt|flash_t|api_message)\(\s*['"]([^'"]+)['"]""",
)
SERVER_DICT_KEY_RE = re.compile(
    r"""['"](?:message_key|label_key)['"]\s*:\s*['"]([^'"]+)['"]""",
)
TEMPLATE_T_RE = re.compile(
    r"""\bt\(\s*['"]([^'"]+)['"]""",
)
JS_I18N_RE = re.compile(
    r"""(?:i18n|guardianI18n|guardianExtI18n|fmt)\(\s*['"]([^'"]+)['"]""",
)
JS_I18N_KEY_OBJECT_RE = re.compile(
    r"""(?:const|let)\s+\w*(?:KEYS|Keys|keys)\w*\s*=\s*\{([^}]+)\}""",
    re.DOTALL,
)
EXT_I18N_RE = re.compile(
    r"""guardianExtI18n\(\s*['"]([^'"]+)['"]""",
)

# Android agent (Kotlin + layout XML)
ANDROID_R_STRING_RE = re.compile(r'\bR\.string\.([A-Za-z0-9_]+)')
ANDROID_XML_STRING_RE = re.compile(r'@string/([A-Za-z0-9_]+)')
ANDROID_XML_TEXT_ATTR_RE = re.compile(
    r'android:(?:text|hint|contentDescription|title|summary)\s*=\s*"([^"@][^"]*)"',
    re.IGNORECASE,
)
KOTLIN_HARDCODED_UI_RE = re.compile(
    r'(?:\.text|setText\(|setContentTitle\(|setContentText\(|Snackbar\.make\([^,]+,\s*)\s*"([^"]{4,})"',
)

ANDROID_SKIP_PATH_RE = re.compile(
    r'(?:^|/)values(?:-[\w-]+)?/strings\.xml$|/uniffi/|/build/|/src/test/',
)


def _android_root() -> Path:
    return REPO_ROOT / 'android-agent' / 'app' / 'src' / 'main'

# ── Hardcoded string patterns ─────────────────────────────────────────────────

HTML_ATTR_RE = re.compile(
    r"""(?:placeholder|title|aria-label|alt)\s*=\s*["']([^"']{3,})["']""",
    re.IGNORECASE,
)
HTML_TEXT_RE = re.compile(r'>([^<>{}\n]+)<')
JINJA_BLOCK_RE = re.compile(r'\{%.*?%\}', re.DOTALL)
JINJA_EXPR_RE = re.compile(r'\{\{.*?\}\}', re.DOTALL)
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)
SCRIPT_STYLE_RE = re.compile(r'<(script|style)\b[^>]*>.*?</\1>', re.DOTALL | re.IGNORECASE)
JS_UI_STRING_RE = re.compile(
    r"""(?:textContent|innerHTML|placeholder|title|alert|confirm)\s*(?:=\s*|)\(\s*['"]([^'"]{4,})['"]""",
)
JS_OBJECT_UI_RE = re.compile(
    r"""^\s+[a-z_][a-z0-9_]*:\s*['"]([^'"]{8,})['"]""",
)

TECHNICAL_STRING_RE = re.compile(
    r'^(?:'
    r'https?://|'
    r'#[0-9a-fA-F]{3,8}|'
    r'[\d\s\W]*$|'
    r'^[a-z0-9_\-./]+$|'
    r'^\s*$|'
    r'true|false|null|undefined|none|auto|inherit|'
    r'var\(--|'
    r'fas fa-|fab fa-|btn-|col-|form-|text-|bg-|d-|'
    r'gap-|flex-|shadow-|rounded|font-|'
    r'application/json|text/html|GET|POST|PUT|DELETE|'
    r'__COUNT__|__PLACEHOLDER__|'
    r'[\d.]+\s*(?:px|rem|em|vh|vw|%)\s*$'
    r')',
    re.IGNORECASE,
)

SKIP_LINE_RE = re.compile(r'i18n-skip|noqa:\s*i18n', re.IGNORECASE)


@dataclass
class KeyReference:
    key: str
    file: str
    line: int
    service: str
    catalog_key: str


@dataclass
class HardcodedString:
    file: str
    line: int
    text: str
    kind: str


@dataclass
class CheckReport:
    missing_keys: list[KeyReference] = field(default_factory=list)
    hardcoded: list[HardcodedString] = field(default_factory=list)
    catalog_errors: list[str] = field(default_factory=list)
    keys_checked: int = 0
    diff_mode: bool = False

    @property
    def failed(self) -> bool:
        return bool(self.missing_keys or self.catalog_errors)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _line_number(content: str, index: int) -> int:
    return content.count('\n', 0, index) + 1


def _add_key_ref(
    refs: list[KeyReference],
    seen: set[tuple[str, str, int]],
    *,
    key: str,
    path: Path,
    line: int,
    service: str,
    catalog_key: str | None = None,
) -> None:
    token = (service, key, line, _rel(path))
    if token in seen:
        return
    seen.add(token)
    refs.append(
        KeyReference(
            key=key,
            file=_rel(path),
            line=line,
            service=service,
            catalog_key=catalog_key or key,
        )
    )


def _normalize_api_key(key: str) -> str:
    return key if key.startswith('api.') else f'api.{key}'


def _normalize_js_key(key: str) -> str:
    return key if key.startswith('js.') else f'js.{key}'


def _extension_message_keys(catalog: dict) -> set[str]:
    messages = catalog.get('messages') or {}
    if not isinstance(messages, dict):
        return set()
    return {name for name in messages if isinstance(name, str)}


def _load_server_keys() -> set[str]:
    catalog = load_catalog(DEFAULT_LOCALE, 'server')
    return {key for key, _ in iter_leaf_strings(catalog)}


def _load_extension_keys() -> set[str]:
    catalog = load_catalog(DEFAULT_LOCALE, 'extension')
    leaves = {key for key, _ in iter_leaf_strings(catalog)}
    leaves.update(_extension_message_keys(catalog))
    return leaves


def _load_agent_string_keys() -> set[str]:
    catalog = load_catalog(DEFAULT_LOCALE, 'agent')
    strings = catalog.get('strings')
    if not isinstance(strings, dict):
        return set()
    return {key for key, value in strings.items() if isinstance(value, str)}


def _agent_catalog_key(resource_name: str) -> str:
    """Map an Android @string / R.string resource name to agent.yaml strings: key."""
    keys = _load_agent_string_keys()
    if resource_name in keys:
        return resource_name
    for key in keys:
        if android_string_name(key) == resource_name:
            return key
    return resource_name


def _should_skip_android_path(path: Path) -> bool:
    rel = _rel(path)
    return bool(ANDROID_SKIP_PATH_RE.search(rel))


def _is_user_visible(text: str) -> bool:
    cleaned = ' '.join(text.split())
    if len(cleaned) < 4:
        return False
    if not re.search(r'[A-Za-z]{2,}', cleaned):
        return False
    if TECHNICAL_STRING_RE.match(cleaned):
        return False
    if cleaned.startswith('{{') or cleaned.startswith('{%'):
        return False
    if re.fullmatch(r'[\d\s,./:\\\-_]+', cleaned):
        return False
    return True


def _scan_python_keys(path: Path, content: str, refs: list[KeyReference], seen: set) -> None:
    for match in SERVER_KEY_RE.finditer(content):
        raw_key = match.group(1)
        line = _line_number(content, match.start())
        if 'api_message' in match.group(0):
            catalog_key = _normalize_api_key(raw_key)
        else:
            catalog_key = raw_key
        _add_key_ref(
            refs, seen,
            key=raw_key,
            path=path,
            line=line,
            service='server',
            catalog_key=catalog_key,
        )

    for match in SERVER_DICT_KEY_RE.finditer(content):
        key = match.group(1)
        _add_key_ref(
            refs, seen,
            key=key,
            path=path,
            line=_line_number(content, match.start()),
            service='server',
            catalog_key=key,
        )


def _scan_template_keys(path: Path, content: str, refs: list[KeyReference], seen: set) -> None:
    for match in TEMPLATE_T_RE.finditer(content):
        key = match.group(1)
        if key.endswith('_'):
            continue
        _add_key_ref(
            refs, seen,
            key=key,
            path=path,
            line=_line_number(content, match.start()),
            service='server',
        )


def _scan_js_keys(path: Path, content: str, refs: list[KeyReference], seen: set, *, service: str) -> None:
    pattern = EXT_I18N_RE if service == 'extension' else JS_I18N_RE
    for match in pattern.finditer(content):
        raw_key = match.group(1)
        catalog_key = raw_key if service == 'extension' else _normalize_js_key(raw_key)
        _add_key_ref(
            refs, seen,
            key=raw_key,
            path=path,
            line=_line_number(content, match.start()),
            service=service,
            catalog_key=catalog_key,
        )

    if service != 'server':
        return

    for match in JS_I18N_KEY_OBJECT_RE.finditer(content):
        for key_match in re.finditer(r""":\s*['"]([a-z][a-z0-9_]+)['"]""", match.group(1)):
            candidate = key_match.group(1)
            if '_' not in candidate:
                continue
            _add_key_ref(
                refs, seen,
                key=candidate,
                path=path,
                line=_line_number(content, match.start()),
                service='server',
                catalog_key=_normalize_js_key(candidate),
            )


def _scan_android_kotlin_keys(path: Path, content: str, refs: list[KeyReference], seen: set) -> None:
    for match in ANDROID_R_STRING_RE.finditer(content):
        resource = match.group(1)
        _add_key_ref(
            refs, seen,
            key=resource,
            path=path,
            line=_line_number(content, match.start()),
            service='agent',
            catalog_key=_agent_catalog_key(resource),
        )


def _scan_android_xml_keys(path: Path, content: str, refs: list[KeyReference], seen: set) -> None:
    for match in ANDROID_XML_STRING_RE.finditer(content):
        resource = match.group(1)
        _add_key_ref(
            refs, seen,
            key=resource,
            path=path,
            line=_line_number(content, match.start()),
            service='agent',
            catalog_key=_agent_catalog_key(resource),
        )


def _scan_hardcoded_android_xml(path: Path, content: str, findings: list[HardcodedString]) -> None:
    lines = content.splitlines()
    for line_no, line in enumerate(lines, start=1):
        if SKIP_LINE_RE.search(line):
            continue
        for match in ANDROID_XML_TEXT_ATTR_RE.finditer(line):
            if '@string/' in match.group(0):
                continue
            text = match.group(1).strip()
            if _is_user_visible(text):
                findings.append(HardcodedString(_rel(path), line_no, text, 'android-xml'))


def _scan_hardcoded_android_kotlin(path: Path, content: str, findings: list[HardcodedString]) -> None:
    lines = content.splitlines()
    for line_no, line in enumerate(lines, start=1):
        if SKIP_LINE_RE.search(line):
            continue
        if 'R.string.' in line:
            continue
        for match in KOTLIN_HARDCODED_UI_RE.finditer(line):
            text = match.group(1).strip()
            if _is_user_visible(text):
                findings.append(HardcodedString(_rel(path), line_no, text, 'android-kotlin'))


def _scan_hardcoded_template(path: Path, content: str, findings: list[HardcodedString]) -> None:
    lines = content.splitlines()
    in_script = False
    in_style = False

    for line_no, line in enumerate(lines, start=1):
        if SKIP_LINE_RE.search(line):
            continue
        stripped = line.strip().lower()
        if stripped.startswith('<script'):
            in_script = True
        if stripped.startswith('</script>'):
            in_script = False
            continue
        if stripped.startswith('<style'):
            in_style = True
        if stripped.startswith('</style>'):
            in_style = False
            continue
        if in_script or in_style:
            continue
        if 't(' in line or '{{' in line and 't(' in line:
            pass

        for match in HTML_ATTR_RE.finditer(line):
            if '{{' in match.group(0) or 't(' in match.group(0):
                continue
            text = match.group(1).strip()
            if _is_user_visible(text):
                findings.append(HardcodedString(_rel(path), line_no, text, 'attribute'))

        scrubbed = JINJA_BLOCK_RE.sub(' ', line)
        scrubbed = JINJA_EXPR_RE.sub(' ', scrubbed)
        scrubbed = HTML_COMMENT_RE.sub(' ', scrubbed)
        for match in HTML_TEXT_RE.finditer(scrubbed):
            text = match.group(1).strip()
            if not text or text.startswith('{%') or text.startswith('{{'):
                continue
            if _is_user_visible(text):
                findings.append(HardcodedString(_rel(path), line_no, text, 'text'))


def _scan_hardcoded_js(path: Path, content: str, findings: list[HardcodedString]) -> None:
    lines = content.splitlines()
    for line_no, line in enumerate(lines, start=1):
        if SKIP_LINE_RE.search(line):
            continue
        if 'i18n(' in line or 'guardianI18n(' in line or 'guardianExtI18n(' in line:
            continue
        for match in JS_UI_STRING_RE.finditer(line):
            text = match.group(1).strip()
            if _is_user_visible(text):
                findings.append(HardcodedString(_rel(path), line_no, text, 'javascript'))
        for match in JS_OBJECT_UI_RE.finditer(line):
            text = match.group(1).strip()
            if _is_user_visible(text) and ' ' in text:
                findings.append(HardcodedString(_rel(path), line_no, text, 'javascript-map'))


def _iter_files(patterns: tuple[str, ...], roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in patterns:
            files.extend(sorted(root.rglob(pattern)))
    return files


def _filter_paths(paths: list[Path], changed_only: set[str] | None) -> list[Path]:
    if not changed_only:
        return paths
    return [path for path in paths if _rel(path) in changed_only]


def collect_key_references(changed_only: set[str] | None = None) -> list[KeyReference]:
    refs: list[KeyReference] = []
    seen: set[tuple[str, str, int]] = set()

    for path in _filter_paths(_iter_files(('*.py',), (REPO_ROOT / 'server',)), changed_only):
        if path.name.startswith('test_'):
            continue
        content = path.read_text(encoding='utf-8')
        _scan_python_keys(path, content, refs, seen)

    for path in _filter_paths(_iter_files(('*.html',), (REPO_ROOT / 'server' / 'templates',)), changed_only):
        content = path.read_text(encoding='utf-8')
        _scan_template_keys(path, content, refs, seen)

    for path in _filter_paths(_iter_files(('*.js',), (REPO_ROOT / 'server' / 'static' / 'js',)), changed_only):
        content = path.read_text(encoding='utf-8')
        _scan_js_keys(path, content, refs, seen, service='server')

    for path in _filter_paths(_iter_files(('*.js',), (REPO_ROOT / 'extension',)), changed_only):
        if path.name.startswith('overlay-i18n.'):
            continue
        content = path.read_text(encoding='utf-8')
        _scan_js_keys(path, content, refs, seen, service='extension')

    if _android_root().is_dir():
        kotlin_paths = _filter_paths(
            _iter_files(('*.kt',), (_android_root() / 'java',)),
            changed_only,
        )
        for path in kotlin_paths:
            if _should_skip_android_path(path):
                continue
            _scan_android_kotlin_keys(path, path.read_text(encoding='utf-8'), refs, seen)

        xml_paths = _filter_paths(
            _iter_files(('*.xml',), (_android_root() / 'res',)),
            changed_only,
        )
        for path in xml_paths:
            if _should_skip_android_path(path):
                continue
            _scan_android_xml_keys(path, path.read_text(encoding='utf-8'), refs, seen)

    return refs


def collect_hardcoded_strings(changed_only: set[str] | None = None) -> list[HardcodedString]:
    findings: list[HardcodedString] = []

    template_paths = _filter_paths(
        _iter_files(('*.html',), (REPO_ROOT / 'server' / 'templates',)),
        changed_only,
    )
    for path in template_paths:
        _scan_hardcoded_template(path, path.read_text(encoding='utf-8'), findings)

    js_paths = _filter_paths(
        _iter_files(('*.js',), (REPO_ROOT / 'server' / 'static' / 'js', REPO_ROOT / 'extension',)),
        changed_only,
    )
    for path in js_paths:
        if path.name.startswith('overlay-i18n.'):
            continue
        _scan_hardcoded_js(path, path.read_text(encoding='utf-8'), findings)

    if _android_root().is_dir():
        kotlin_paths = _filter_paths(
            _iter_files(('*.kt',), (_android_root() / 'java',)),
            changed_only,
        )
        for path in kotlin_paths:
            if _should_skip_android_path(path):
                continue
            _scan_hardcoded_android_kotlin(path, path.read_text(encoding='utf-8'), findings)

        xml_paths = _filter_paths(
            _iter_files(('*.xml',), (_android_root() / 'res',)),
            changed_only,
        )
        for path in xml_paths:
            if _should_skip_android_path(path):
                continue
            _scan_hardcoded_android_xml(path, path.read_text(encoding='utf-8'), findings)

    deduped: list[HardcodedString] = []
    seen: set[tuple[str, int, str]] = set()
    for item in findings:
        token = (item.file, item.line, item.text)
        if token in seen:
            continue
        seen.add(token)
        deduped.append(item)
    return sorted(deduped, key=lambda item: (item.file, item.line, item.text))


def validate_references(refs: list[KeyReference]) -> list[KeyReference]:
    server_keys = _load_server_keys()
    extension_keys = _load_extension_keys()
    agent_keys = _load_agent_string_keys()
    missing: list[KeyReference] = []

    for ref in refs:
        if ref.service == 'server':
            exists = ref.catalog_key in server_keys
        elif ref.service == 'extension':
            exists = ref.key in extension_keys or f'messages.{ref.key}.message' in extension_keys
        elif ref.service == 'agent':
            exists = ref.catalog_key in agent_keys
        else:
            exists = False
        if not exists:
            missing.append(ref)

    return missing


def run_check(*, changed_files: set[str] | None = None, warn_hardcoded: bool = True) -> CheckReport:
    report = CheckReport()
    report.diff_mode = changed_files is not None
    report.catalog_errors = validate_catalogs()

    scope = changed_files
    refs = collect_key_references(changed_only=scope)
    report.keys_checked = len(refs)
    report.missing_keys = validate_references(refs)
    report.hardcoded = (
        collect_hardcoded_strings(changed_only=scope)
        if warn_hardcoded
        else []
    )
    return report


def _truncate(text: str, limit: int = 72) -> str:
    cleaned = ' '.join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + '…'


def format_markdown(report: CheckReport, *, changed_files: set[str] | None = None) -> str:
    lines = [COMMENT_MARKER, '## 🌐 Translation check', '']
    if report.diff_mode:
        lines.append('_Scanning translation usage in changed files only (catalog structure is always validated)._')
        lines.append('')

    if report.catalog_errors:
        lines.append('### ❌ Catalog structure errors')
        lines.append('')
        for error in report.catalog_errors:
            lines.append(f'- {error}')
        lines.append('')

    if report.missing_keys:
        lines.append('### ❌ Missing translation keys')
        lines.append('')
        lines.append('These keys are referenced in code but not defined in `i18n/en/`.')
        lines.append('')
        lines.append('| Key | Location |')
        lines.append('| --- | --- |')
        for ref in sorted(report.missing_keys, key=lambda item: (item.catalog_key, item.file, item.line)):
            lines.append(f'| `{ref.catalog_key}` | `{ref.file}:{ref.line}` |')
        lines.append('')
    else:
        scope = 'changed files in this diff' if report.diff_mode else 'the project'
        lines.append(
            f'✅ All **{report.keys_checked}** translation key reference(s) in {scope} '
            'exist in English catalogs.'
        )
        lines.append('')

    if report.hardcoded:
        scope = 'changed files in this diff' if report.diff_mode else 'scanned files'
        lines.append(f'### ⚠️ Hardcoded user-visible strings ({len(report.hardcoded)} in {scope})')
        lines.append('')
        lines.append(
            'Consider moving these to `i18n/en/server.yaml`, `i18n/en/agent.yaml` '
            '(under `strings:`), or the relevant service catalog, and referencing them '
            'with `t()` / `i18n()` / `@string` / `R.string`.'
        )
        lines.append('')
        lines.append('| Location | Kind | Text |')
        lines.append('| --- | --- | --- |')
        for item in report.hardcoded[:100]:
            lines.append(
                f'| `{item.file}:{item.line}` | {item.kind} | {_truncate(item.text)} |'
            )
        if len(report.hardcoded) > 100:
            lines.append('')
            lines.append(f'_…and {len(report.hardcoded) - 100} more._')
        lines.append('')
    elif report.diff_mode:
        lines.append('✅ No hardcoded user-visible strings detected in changed files.')
        lines.append('')

    if report.failed:
        lines.append('**Result: failed** — add the missing keys before merging.')
    elif report.hardcoded:
        lines.append('**Result: passed with warnings** — missing keys block merges; hardcoded strings are advisory.')
    else:
        lines.append('**Result: passed**')
    return '\n'.join(lines)


def format_text(report: CheckReport) -> str:
    lines: list[str] = []
    if report.diff_mode:
        lines.append('Diff mode: checking changed files only.')
    if report.catalog_errors:
        lines.append('Catalog errors:')
        lines.extend(f'  - {error}' for error in report.catalog_errors)
    if report.missing_keys:
        lines.append('Missing translation keys:')
        for ref in report.missing_keys:
            lines.append(f'  - {ref.catalog_key} ({ref.file}:{ref.line})')
    if report.hardcoded:
        lines.append(f'Hardcoded strings ({len(report.hardcoded)} warning(s)):')
        for item in report.hardcoded[:20]:
            lines.append(f'  - {item.file}:{item.line} [{item.kind}] {_truncate(item.text, 60)}')
        if len(report.hardcoded) > 20:
            lines.append(f'  …and {len(report.hardcoded) - 20} more')
    if not report.catalog_errors and not report.missing_keys and not report.hardcoded:
        scope = 'changed files' if report.diff_mode else 'project'
        lines.append(f'OK — {report.keys_checked} translation key reference(s) validated in {scope}.')
    return '\n'.join(lines)


def load_changed_files(path: Path | None, raw: list[str] | None) -> set[str] | None:
    """Return None for a full-repo scan, or a (possibly empty) set for diff mode."""
    if raw:
        return {line.strip() for line in raw if line.strip()}
    if path is not None:
        if not path.is_file():
            return set()
        return {line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip()}
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Validate i18n key usage across Guardian services.')
    parser.add_argument('--json', type=Path, help='Write machine-readable report JSON')
    parser.add_argument('--report-md', type=Path, help='Write Markdown report (for PR comments)')
    parser.add_argument(
        '--changed-files',
        type=Path,
        help='Newline-separated repo-relative paths; limits checks to these files (CI diff mode)',
    )
    parser.add_argument(
        '--changed-file',
        action='append',
        dest='changed_files_list',
        default=[],
        help='Repo-relative changed file (repeatable)',
    )
    parser.add_argument('--warn-hardcoded', action='store_true', default=True)
    parser.add_argument('--no-warn-hardcoded', action='store_false', dest='warn_hardcoded')
    args = parser.parse_args(argv)

    changed_files = load_changed_files(args.changed_files, args.changed_files_list or None)
    report = run_check(
        changed_files=changed_files,
        warn_hardcoded=args.warn_hardcoded,
    )

    text = format_text(report)
    print(text)

    if args.json:
        payload = {
            'failed': report.failed,
            'keys_checked': report.keys_checked,
            'diff_mode': report.diff_mode,
            'catalog_errors': report.catalog_errors,
            'missing_keys': [
                {
                    'key': ref.catalog_key,
                    'file': ref.file,
                    'line': ref.line,
                    'service': ref.service,
                }
                for ref in report.missing_keys
            ],
            'hardcoded': [
                {
                    'file': item.file,
                    'line': item.line,
                    'text': item.text,
                    'kind': item.kind,
                }
                for item in report.hardcoded
            ],
        }
        args.json.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    if args.report_md:
        markdown = format_markdown(report, changed_files=changed_files)
        args.report_md.write_text(markdown + '\n', encoding='utf-8')

    if report.failed:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
