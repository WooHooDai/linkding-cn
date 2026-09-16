"""
Test settings for linkding webapp.
Optimized for speed: in-memory database, synchronous tasks, minimal logging.
"""

# ruff: noqa

import os

from .base import *

DEBUG = False

# 测试环境固定使用 requests 引擎：避免 curl_cffi 发起真实网络请求，
# 破坏依赖 mock requests.get 的既有测试语义（防御性设置：默认引擎本就是 requests）
os.environ["LD_HTTP_ENGINE"] = "requests"

# In-memory database, eliminates file I/O
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Huey tasks execute synchronously in tests
HUEY = {
    **HUEY,
    "immediate": True,
}

# Task unit tests opt in explicitly. CRUD tests must not run the production
# snapshot retry loop synchronously or contact external sites.
LD_ENABLE_SNAPSHOTS = False
LD_DISABLE_BACKGROUND_TASKS = False

# Suppress logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "WARNING"},
}

# Static files (needed for template rendering)
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "bookmarks", "styles"),
]
