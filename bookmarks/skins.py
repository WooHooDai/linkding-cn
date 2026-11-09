"""皮肤清单。

皮肤是普通 CSS 文件：内置的在 bookmarks/static/skins/，用户导入的放在
data/skins/（与数据库同卷，容器重建不丢）。文件头的 @skin 注释携带元数据。
此处只做扫描与解析，用户偏好存在 UserProfile.skin。
"""

import json
import re
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext_lazy as _

# 文件头形如：/* @skin { "id": "nord", "name": "Nord", ... } 后续可跟说明文字
META_RE = re.compile(r"/\*\s*@skin\s*(\{.*?\})", re.S)
META_HEAD_BYTES = 2048

# 皮肤 id 同时用作文件名，限定为小写字母数字与连字符
SKIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

MAX_SKIN_BYTES = 256 * 1024

DEFAULT_SKIN = {
    "id": "",
    "name": _("Default"),
    "preview": ["#ffffff", "#5a54dc", "#374151"],
}

# 默认皮肤没有单独的皮肤文件，导出件由 build-skins.js 在构建期合并产出
DEFAULT_SKIN_EXPORT = "skin-default-export.css"

# 目录 mtime 变化即重建，避免每次请求都读文件
_cache = {"stamp": None, "items": ()}


def _builtin_static_dir() -> Path:
    """内置静态产物根。发布镜像删掉了 bookmarks/static，只剩 collectstatic
    收好的 STATIC_ROOT。"""
    source = Path(settings.BASE_DIR, "bookmarks", "static")
    return source if source.is_dir() else Path(settings.STATIC_ROOT)


def builtin_skin_dir() -> Path:
    return _builtin_static_dir() / "skins"


def user_skin_dir() -> Path:
    return Path(settings.BASE_DIR, "data", "skins")


def _read_meta(path: Path) -> dict:
    try:
        head = path.read_text(encoding="utf-8")[:META_HEAD_BYTES]
    except OSError:
        return {}
    match = META_RE.search(head)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except ValueError:
        return {}


def parse_meta(css: str) -> dict:
    """从 CSS 文本里取 @skin 元数据，供导入校验复用。"""
    match = META_RE.search(css[:META_HEAD_BYTES])
    if not match:
        return {}
    try:
        meta = json.loads(match.group(1))
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _stamp(dirs: tuple[Path, ...]):
    return tuple(d.stat().st_mtime_ns if d.is_dir() else 0 for d in dirs)


def skins() -> list[dict]:
    """默认皮肤永远排第一，其后是内置皮肤与用户皮肤，按文件名排序。

    同 id 时内置优先，防止导入的文件顶掉自带皮肤。
    """
    dirs = (builtin_skin_dir(), user_skin_dir())
    stamp = _stamp(dirs)

    if _cache["stamp"] != stamp:
        items = []
        seen = set()
        for root in dirs:
            if not root.is_dir():
                continue
            is_user = root == user_skin_dir()
            for path in sorted(root.glob("*.css")):
                meta = _read_meta(path)
                skin_id = meta.get("id") or path.stem
                if skin_id in seen:
                    continue
                seen.add(skin_id)
                items.append(
                    {
                        "id": skin_id,
                        "name": meta.get("name") or path.stem.title(),
                        "preview": meta.get("preview") or [],
                        "user": is_user,
                    }
                )
        _cache["items"] = tuple(items)
        _cache["stamp"] = stamp

    return [DEFAULT_SKIN, *_cache["items"]]


def skin_ids() -> frozenset[str]:
    return frozenset(item["id"] for item in skins() if item["id"])


def skin_path(skin_id: str) -> Path | None:
    """按 id 找到皮肤文件，用户皮肤优先于内置皮肤。"""
    if not SKIN_ID_RE.match(skin_id):
        return None
    for root in (user_skin_dir(), builtin_skin_dir()):
        path = root / f"{skin_id}.css"
        if path.is_file():
            return path
    return None


def default_skin_css() -> str | None:
    """默认皮肤的导出版本。明暗两份源码怎么合并成带 @skin 头的一份，是
    build-skins.js 的事，这里只读它落下的产物。"""
    export = _builtin_static_dir() / DEFAULT_SKIN_EXPORT
    return export.read_text(encoding="utf-8") if export.is_file() else None


def skin_choices() -> list[tuple[str, str]]:
    return [(item["id"], item["name"]) for item in skins()]
