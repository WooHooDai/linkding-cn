"""
Test settings for linkding webapp.
Optimized for speed: in-memory database, synchronous tasks, minimal logging.
"""

# ruff: noqa

from .base import *

DEBUG = False

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

# Disable background tasks
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
