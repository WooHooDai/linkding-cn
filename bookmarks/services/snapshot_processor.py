import importlib
import json
import logging
import os
import shlex
import signal
import subprocess
from urllib.parse import urlparse

from django.conf import settings
from bookmarks.utils import get_domain, search_config_for_domain, load_settings, load_module
from bookmarks.services import singlefile


logger = logging.getLogger(__name__)


# 缓存规则设置与解析规则（function）
_settings_cache = None
_processors_module_cache = {}  # {loader_path: (module, mtime)}

# 创建快照： 快照总调度
def create_snapshot(url: str, filepath: str):
    settings_path = settings.LD_CUSTOM_SNAPSHOT_PROCESSOR_SETTINGS
    domain = get_domain(url)
    config = None
    if os.path.exists(settings_path):
        domain_map = load_settings(settings_path,_settings_cache)
        if domain_map == "__JSON_ERROR__":
            logger.error("【错误】快照 settings.json 解析失败，请检查文件格式")
            return
        config = search_config_for_domain(domain, domain_map)
        if config:
            processor_file = config.get("processor")
            if processor_file:
                processor_path = os.path.join(os.path.dirname(settings_path), processor_file) if processor_file else None
                if processor_path and os.path.exists(processor_path):
                    module = load_module(processor_path, _processors_module_cache)
                    func = getattr(module, "_create_snapshot")
                    return func(url, filepath, config)
            elif config:
                return _create_snapshot(url, filepath, config)
    return _create_snapshot(url, filepath)


# 创建快照： 默认方法（兜底方法）
def _create_snapshot(url: str, filepath, config: dict = None):
    return singlefile.create_snapshot(url, filepath, config)