"""
Generate annotated JSONC field reference from fields.py.

Usage:
    python scripts/generate-adapters-reference.py              # English + Chinese
    python scripts/generate-adapters-reference.py --lang en
    python scripts/generate-adapters-reference.py --lang zh

Output:
    docs/reference/adapters.jsonc
    docs/reference/adapters-zh.jsonc
"""

import json
import os
import re
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmarks.settings")

import django
django.setup()

from site_adapters.services.config.fields import (
    ADAPTER_META_FIELDS,
    ALIAS_EXAMPLES,
    AUTH_BASIC_FIELDS,
    AUTH_COOKIE_FIELDS,
    AUTH_HEADERS_OBJECT_INFO,
    AUTH_OAUTH2_FIELDS,
    DEFAULT_FIELDS,
    DOMAIN_EXAMPLE_KEY,
    DEFUDDLE_ARG_FIELDS,
    HEALTH_FIELDS,
    METADATA_FIELDS,
    PRIORITY_NOTES,
    ROUTES_FIELD,
    READER_FIELDS,
    REFERENCE_META,
    SECTION_TITLES,
    SNAPSHOT_FIELDS,
)
from site_adapters.services.config.jsonc import parse as parse_jsonc

LANG = "en"
_B = "  "


def t(key):
    return SECTION_TITLES.get(key, {}).get(LANG, key)


def m(key):
    return REFERENCE_META.get(LANG, {}).get(key, REFERENCE_META["en"].get(key, key))


def _desc(info):
    return info.get(LANG, info.get("en", ""))


def _tag(info):
    if info.get("required"):
        return m("required")
    if info.get("optional"):
        return m("optional")
    if info.get("reserved"):
        return m("reserved")
    if "required" in info or "common" in info:
        return ""
    return ""


def _example(info):
    if "example" in info:
        return json.dumps(info["example"], ensure_ascii=False)

    typ = info.get("type", "")
    if "int" in typ:
        return "30"
    if typ == "rewrite":
        return "[]"
    if typ == "bool":
        return "true"
    if "array" in typ:
        return "[]"
    if "object" in typ or typ == "verify_config":
        return "{}"
    if typ == "str|null":
        return "null"
    if typ == "auth":
        return "null"

    quoted = re.search(r'"([^"]*)"', typ)
    if quoted:
        return json.dumps(quoted.group(1))
    return '""'


def _comment(info):
    tag = _tag(info)
    description = _desc(info)
    return f"{tag} {description}".strip() if tag else description


def _field_line(key, info, indent):
    sp = " " * indent
    raw = f'{sp}"{key}": {_example(info)},'
    return raw.ljust(58) + f" // {_comment(info)}"


def _raw_field_line(key, value, comment, indent):
    sp = " " * indent
    raw = f'{sp}"{key}": {value},'
    return raw.ljust(58) + f" // {comment}"


def _build_section(fields, indent, skip=()):
    lines = []
    for key, info in fields.items():
        if key in skip:
            continue
        if key == "scripts" and info.get("example_items"):
            lines.extend(_render_scripts_field(key, info, indent))
        else:
            lines.append(_field_line(key, info, indent))
    return lines


def _render_scripts_field(key, info, indent):
    sp = " " * indent
    item_sp = " " * (indent + 2)
    lines = [f'{sp}"{key}": [']
    examples = info.get("example_items") or []
    for index, item in enumerate(examples):
        trailing = "," if index < len(examples) - 1 else ""
        lines.append(f"// {item_sp}{json.dumps(item, ensure_ascii=False)}{trailing}")
    raw = f"{sp}],"
    lines.append(raw.ljust(58) + f" // {_comment(info)}")
    return lines


def _section_header(key):
    return [
        f"{_B}// " + "=" * 70,
        f"{_B}//  {t(key)}",
        f"{_B}// " + "=" * 70,
    ]


def _comment_out(lines):
    result = []
    for line in lines:
        result.append(f"// {line}" if line.strip() else line)
    return result


def _unflatten(flat):
    result = {}
    for key, info in flat.items():
        parts = key.split(".")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = info
    return result


def _is_field_info(value):
    return isinstance(value, dict) and "type" in value and "en" in value


def _render_nested(node, indent):
    sp = " " * indent
    lines = []
    for key, value in node.items():
        if isinstance(value, dict) and not _is_field_info(value):
            lines.append(f'{sp}"{key}": {{')
            lines.extend(_render_nested(value, indent + 2))
            lines.append(f'{sp}}},')
        else:
            lines.append(_field_line(key, value, indent))
    return lines


def _auth_block(indent):
    sp = " " * indent
    inner = " " * (indent + 2)
    lines = []

    lines.append(f'{sp}// {t("cookie_title")}')
    lines.append(f'{sp}"cookie": {{')
    lines.extend(_render_nested(_unflatten(AUTH_COOKIE_FIELDS), indent + 2))
    lines.append(f'{sp}}},')
    lines.append("")

    lines.append(f'{sp}// {t("oauth2_title")}')
    lines.append(f'{sp}"oauth2": {{')
    for key, info in AUTH_OAUTH2_FIELDS.items():
        lines.append(_field_line(key, info, indent + 2))
    lines.append(f'{sp}}},')
    lines.append("")

    lines.append(f'{sp}// {t("headers_title")}')
    lines.append(f'{sp}"headers": {{')
    key = AUTH_HEADERS_OBJECT_INFO["example_key"]
    value = json.dumps(AUTH_HEADERS_OBJECT_INFO["example_value"], ensure_ascii=False)
    lines.append(_raw_field_line(key, value, _desc(AUTH_HEADERS_OBJECT_INFO), indent + 2))
    lines.append(f'{sp}}},')
    lines.append("")

    lines.append(f'{sp}// {t("basic_title")}')
    lines.append(f'{sp}"basic_auth": {{')
    for key, info in AUTH_BASIC_FIELDS.items():
        lines.append(_field_line(key, info, indent + 2))
    lines.append(f'{sp}}}')

    return lines


def _render_defuddle_args(indent):
    sp = " " * indent
    inner = " " * (indent + 2)
    lines = [f'{sp}"defuddle_args": {{']
    for key, info in DEFUDDLE_ARG_FIELDS.items():
        line = _field_line(key, info, indent + 2)
        if info.get("common"):
            lines.append(line)
        else:
            lines.append(f"// {line}")
    lines.append(f'{sp}}},')
    return lines


def _meta_block():
    lines = []
    lines.extend(_section_header("sec_meta"))
    lines.append(f'{_B}"_meta": {{')
    for required in (True, False):
        title_key = "meta_required" if required else "meta_optional"
        fields = {
            key: info for key, info in ADAPTER_META_FIELDS.items()
            if info.get("required", False) is required
        }
        lines.append(f'{_B*2}// {t(title_key)}')
        for key, info in fields.items():
            lines.append(_field_line(key, info, 4))
    lines.append(f'{_B}}},')
    return lines


def _builtin_block():
    lines = [_B + '"_builtin": {']
    lines.append(_B * 2 + '"defaults": {')
    lines.extend(_build_section(DEFAULT_FIELDS, 6))
    lines.append(_B * 2 + "},")
    lines.append(_B * 2 + "// " + t("builtin_note"))
    lines.append(_B + "},")
    return _comment_out(lines)


def _builtin_overrides_block():
    lines = [_B + '"_builtin_overrides": {']
    lines.append(_B * 2 + "// " + t("builtin_overrides_note"))
    lines.append(_B + "},")
    return _comment_out(lines)


def generate(lang="en"):
    global LANG
    LANG = lang
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = []

    out.append("// " + "=" * 74)
    out.append(f"//  {m('header')}")
    out.append(f"//  {m('auto_gen')}  |  {now}")
    out.append("//")
    out.append(f"//  Re-generate: {m('regen')}")
    out.append(f"//  Guide:       {m('guide')}")
    out.append("// " + "=" * 74)
    out.append("")
    out.append(f"// {t('priority_title')}")
    for line in PRIORITY_NOTES[LANG]:
        out.append(f"//  - {line}")
    out.append("")
    out.append("{")
    out.append("")

    out.extend(_meta_block())
    out.append("")

    out.extend(_section_header("sec_builtin"))
    out.extend(_builtin_block())
    out.append("")

    out.extend(_section_header("sec_builtin_overrides"))
    out.extend(_builtin_overrides_block())
    out.append("")

    out.extend(_section_header("sec_defaults"))
    out.append(f'{_B}"defaults": {{')
    out.extend(_build_section(DEFAULT_FIELDS, 4))
    out.append(f'{_B}}},')
    out.append("")

    out.extend(_section_header("sec_domains"))
    out.append(f'{_B}"domains": {{')
    out.append("")
    out.append(f'{_B*2}// {t("alias_intro")}')
    for alias in ALIAS_EXAMPLES:
        out.append(
            f'{_B*2}"{alias["key"]}": {{ "type": "alias", "target": "{alias["target"]}" }},'
        )
    out.append("")
    out.append(f'{_B*2}// {t("full_intro")}')
    out.append(f'{_B*2}"{DOMAIN_EXAMPLE_KEY}": {{')
    out.append("")

    sec4 = _B * 3
    out.append(f'{sec4}// {t("sec_auth")}')
    out.append(f'{sec4}"auth": {{')
    out.extend(_auth_block(8))
    out.append(f'{sec4}}},')
    out.append("")

    out.append(f'{sec4}// {t("sec_default")}')
    out.append(f'{sec4}"defaults": {{')
    out.extend(_build_section(DEFAULT_FIELDS, 8))
    out.append(f'{sec4}}},')
    out.append("")

    out.append(f'{sec4}// {t("sec_metadata")}')
    out.append(f'{sec4}"metadata": {{')
    out.extend(_build_section(METADATA_FIELDS, 8))
    out.append(f'{sec4}}},')
    out.append("")

    out.append(f'{sec4}// {t("sec_snapshot")}')
    out.append(f'{sec4}// {t("snapshot_mutex")}')
    out.append(f'{sec4}"snapshot": {{')
    out.extend(_build_section(SNAPSHOT_FIELDS, 8))
    out.append(f'{sec4}}},')
    out.append("")

    out.append(f'{sec4}// {t("sec_reader")}')
    out.append(f'{sec4}"reader": {{')
    out.extend(_build_section(READER_FIELDS, 8, skip={"defuddle_args"}))
    out.append(f'{sec4}// {t("defuddle_title")}')
    out.extend(_render_defuddle_args(8))
    out.append(f'{sec4}}},')
    out.append("")

    out.append(f'{sec4}// {t("sec_routes")}')
    out.append(f'{sec4}"routes": {{')
    out.append(f'{sec4*2}"^/article/": {{')
    out.append(f'{sec4*3}"metadata": {{ "select_title": [".article-title"] }},')
    out.append(f'{sec4*2}}},')
    out.append(f'{sec4*2}"^/video/": {{')
    out.append(f'{sec4*3}"snapshot": {{ "keep_elements": [".video-player"] }}')
    out.append(f'{sec4*2}}}')
    out.append(f'{sec4}}},')
    out.append("")

    out.append(f'{sec4}// {t("sec_health")}')
    out.append(f'{sec4}"health": {{')
    out.extend(_render_nested(_unflatten(HEALTH_FIELDS), 8))
    out.append(f'{sec4}}},')
    out.append("")

    out.append(f'{_B*2}}}')
    out.append(f'{_B}}}')
    out.append("}")

    text = "\n".join(out) + "\n"
    suffix = "-zh" if LANG == "zh" else ""
    out_dir = os.path.join(PROJECT_ROOT, "docs", "reference")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"adapters{suffix}.jsonc")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)

    try:
        parse_jsonc(text)
        print(f"OK  {out_path}")
    except json.JSONDecodeError as e:
        print(f"FAIL {out_path}: {e}")
        raise


if __name__ == "__main__":
    if "--lang" in sys.argv:
        idx = sys.argv.index("--lang")
        if idx + 1 >= len(sys.argv):
            print("Missing language after --lang", file=sys.stderr)
            sys.exit(1)
        lang = sys.argv[idx + 1]
        if lang not in ("en", "zh"):
            print(f"Unsupported language: {lang}", file=sys.stderr)
            sys.exit(1)
        generate(lang)
    else:
        generate("en")
        generate("zh")
