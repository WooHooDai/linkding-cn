"""
Production settings for linkding webapp
"""

# ruff: noqa

# Start from development settings
# noinspection PyUnresolvedReferences
import os

from django.core.management.utils import get_random_secret_key
from .base import *

# Turn of debug mode
DEBUG = False

# Try read secret key from file
try:
    with open(os.path.join(BASE_DIR, "data", "secretkey.txt")) as f:
        SECRET_KEY = f.read().strip()
except:
    SECRET_KEY = get_random_secret_key()

# Set ALLOWED_HOSTS
# By default look in the HOST_NAME environment variable, if that is not set then allow all hosts
host_name = os.environ.get("HOST_NAME")
if host_name:
    ALLOWED_HOSTS = [host_name]
else:
    ALLOWED_HOSTS = ["*"]

# uWSGI 请求行缓冲上限（字节），与 uwsgi.ini 的 buffer-size 保持一致。
# bookmarklet 按该值动态计算放入 URL 的元数据预算，避免超长 URL 返回 502。
# 仅生产环境（uWSGI）存在该限制，开发环境 runserver 不受 buffer-size 约束。
LD_BUFFER_SIZE = int(os.getenv("LD_BUFFER_SIZE", 8192))

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "{asctime} {levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {
        "handlers": ["console"],
        "level": "WARN",
    },
    "loggers": {
        "bookmarks": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
        "huey": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}

# Import custom settings
# noinspection PyUnresolvedReferences
from .custom import *
