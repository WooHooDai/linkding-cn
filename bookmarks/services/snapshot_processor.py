import logging
import os

from django.conf import settings

from bookmarks.services import singlefile
from bookmarks.utils import load_module, search_config_for_domain

logger = logging.getLogger(__name__)


# 缓存规则设置与解析规则（function）
_settings_cache = None
_processors_module_cache = {}  # {loader_path: (module, mtime)}


# 创建快照： 快照总调度
def create_snapshot(url: str, filepath: str):
    settings_path = settings.LD_CUSTOM_SNAPSHOT_PROCESSOR_SETTINGS
    config = search_config_for_domain(url, settings_path, _settings_cache)

    if config:
        processor_file = config.get("processor")
        if processor_file:
            processor_path = (
                os.path.join(os.path.dirname(settings_path), processor_file)
                if processor_file
                else None
            )
            if processor_path and os.path.exists(processor_path):
                module = load_module(processor_path, _processors_module_cache)
                func = module._create_snapshot
                return func(url, filepath, config)
        else:
            return _create_snapshot(url, filepath, config)

    return _create_snapshot(url, filepath)


# 创建快照： 默认方法（兜底方法）
def _create_snapshot(url: str, filepath, config: dict = None):
    return singlefile.create_snapshot(url, filepath, config)
