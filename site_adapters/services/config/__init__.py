"""
Config utilities — JSONC 解析、深合并、路径解析、URL 重写

纯函数模块，无状态。
"""

import copy
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSONC 解析（委托给 config.jsonc，统一实现）
# ---------------------------------------------------------------------------

def parse_jsonc(text: str):
    from site_adapters.services.config.jsonc import parse as _parse_jsonc
    return _parse_jsonc(text)


def load_jsonc_file(path: str):
    # utf-8-sig: strip a leading UTF-8 BOM if present, otherwise behaves as utf-8
    with open(path, encoding='utf-8-sig') as f:
        return parse_jsonc(f.read())


def load_jsonc_file_with_warnings(path: str, file_label: str | None = None) -> tuple:
    """Load a JSONC file, returning (data, warnings).

    warnings: list of {'key': str, 'count': int, 'file': str} for duplicate keys.
    """
    from site_adapters.services.config.jsonc import parse_with_dups
    label = file_label or path
    # utf-8-sig: strip a leading UTF-8 BOM if present, otherwise behaves as utf-8
    with open(path, encoding='utf-8-sig') as f:
        return parse_with_dups(f.read(), file_label=label)


# ---------------------------------------------------------------------------
# 深合并（null 值表示移除字段）
# ---------------------------------------------------------------------------

def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# 路径解析（相对路径）
# ---------------------------------------------------------------------------


def _resolve_path(path: str, base_dir: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base_dir, path))


def _script_root_for_file_dir(file_dir: str) -> str:
    abs_dir = os.path.abspath(file_dir)
    if False:
        return os.path.dirname(abs_dir)
    return abs_dir


def is_safe_script_path(script_path: str, file_dir: str) -> bool:
    """检查脚本路径是否在允许的站点适配根目录内。"""
    abs_script = os.path.realpath(os.path.abspath(script_path))
    abs_base = os.path.realpath(os.path.abspath(_script_root_for_file_dir(file_dir)))
    try:
        return os.path.commonpath([abs_script, abs_base]) == abs_base
    except ValueError:
        return False


def _resolve_all_paths(node, base_dir: str):
    """递归解析脚本路径字段中的相对路径，校验结果不越出站点适配根目录。

    同时拒绝指向根目录外的绝对路径。

    处理 scripts 数组中的 path 字段，以及旧 script 字段。
    """
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key == 'scripts' and isinstance(value, list):
                resolved_scripts = []
                for item in value:
                    if isinstance(item, dict) and 'path' in item:
                        resolved_path = _resolve_script_path(item['path'], base_dir)
                        if resolved_path:
                            resolved_scripts.append({**item, 'path': resolved_path})
                    else:
                        resolved_scripts.append(_resolve_all_paths(item, base_dir))
                result[key] = resolved_scripts
            elif (key == 'script' or key.endswith('_script')) and isinstance(value, str):
                resolved_path = _resolve_script_path(value, base_dir)
                result[key] = resolved_path if resolved_path else value
            else:
                result[key] = _resolve_all_paths(value, base_dir)
        return result
    if isinstance(node, list):
        return [_resolve_all_paths(item, base_dir) for item in node]
    return node


def _resolve_script_path(path: str, base_dir: str) -> str | None:
    """解析单个脚本路径。文件名形式自动补全 scripts/ 前缀。

    返回解析后的绝对路径，如果路径不安全则返回 None（记录警告）。
    """
    if os.path.isabs(path):
        if not is_safe_script_path(path, base_dir):
            logger.warning('Script path outside allowed dir, skipping: %s', path)
            return None
        return path
    if not path.startswith('./') and not path.startswith('../') and not os.path.isabs(path):
        # Plain path (no explicit directory prefix): auto-prefix scripts/ directory
        path = os.path.join('scripts', path)
    resolved = _resolve_path(path, base_dir)
    if not is_safe_script_path(resolved, base_dir):
        logger.warning('Script path escapes allowed dir, skipping: %s', path)
        return None
    return resolved


# ---------------------------------------------------------------------------
# URL 重写
# ---------------------------------------------------------------------------

def apply_rewrite(value: str | None, rules) -> str | None:
    """
    对字符串逐条应用 rewrite 规则。

    rules: [pattern, replacement] 或 [[pattern, replacement], ...]
    None 输入视为空字符串进行 rewrite，最终空字符串返回 None。
    """
    if not rules:
        return value
    if isinstance(rules[0], str):
        rules = [rules]
    result = value or ""
    for rule in rules:
        if not isinstance(rule, list) or len(rule) < 2:
            continue
        pattern, replacement = rule[0], rule[1]
        try:
            result = re.sub(pattern, replacement, result)
        except re.error as exc:
            logger.warning("Invalid rewrite pattern %r: %s", pattern, exc)

            continue
    return result if result else None

def apply_rewrite_url(url: str, rules) -> str | None:
    """
    rewrite_url: [pattern, replacement] 或 [[pattern, replacement], ...]
    返回重写后的 URL，无匹配返回 None。
    """
    if not rules:
        return None
    # 规范化为列表
    if isinstance(rules[0], str):
        rules = [rules]
    for rule in rules:
        if not isinstance(rule, list) or len(rule) < 2:
            continue
        new_url = re.sub(rule[0], rule[1], url)
        if new_url != url:
            return new_url
    return None


def apply_request_url(url: str, rules) -> str | None:
    """
    request_url: [pattern, replacement] 或 [[pattern, replacement], ...]
    返回用于获取数据的 URL，无匹配返回 None。
    """
    return apply_rewrite_url(url, rules)
