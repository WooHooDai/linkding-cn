# ruff: noqa: F401,F403
from . import bundles, health_check, highlights, reader, tags
from .assets import *
from .auth import *
from .bookmarks import *
from .custom_css import custom_css
from .health import health
from .manifest import manifest
from .opensearch import opensearch
from .reader import *
from .root import root
from .settings import *
from site_adapters.views import (
    action as site_adapters_action,
    all_domains,
    local_domain_toggle,
    subscription_domain_read,
    subscription_domain_toggle,
    domain_create,
    domain_delete,
    domain_read,
    domain_rename,
    domain_save,
    save_cookie,
    site_adapters_page as site_adapters,
    subscription_manage,
    adapters_page,
    user_credentials,
    shared_credential_list,
    shared_credential_save,
    shared_credential_delete,
    match_domain_config,
    snapshot_toggles,
    view_snapshot,
    view_reader,
    preview_image_proxy,
)
from .toasts import *
