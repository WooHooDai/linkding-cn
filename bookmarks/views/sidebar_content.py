import json

from django.http import HttpResponse, HttpResponseBadRequest
from django.template import loader
from django.views.decorators.cache import cache_control

from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.views import contexts

SIDEBAR_SCOPE_BOOKMARKS = "bookmarks"
SIDEBAR_SCOPE_HIGHLIGHTS = "highlights"

SIDEBAR_MODULE_TEMPLATES = {
    UserProfile.SIDEBAR_MODULE_SUMMARY: "bookmarks/sidebar/modules/summary/index.html",
    UserProfile.SIDEBAR_MODULE_BUNDLES: "bookmarks/sidebar/modules/bundles/index.html",
    UserProfile.SIDEBAR_MODULE_DOMAINS: "bookmarks/sidebar/modules/domains/index.html",
    UserProfile.SIDEBAR_MODULE_TAGS: "bookmarks/sidebar/modules/tags/index.html",
    UserProfile.SIDEBAR_MODULE_AUTHORS: "bookmarks/sidebar/modules/authors/index.html",
}

HIGHLIGHTS_SIDEBAR_MODULE_TEMPLATES = {
    UserProfile.SIDEBAR_MODULE_COLORS: "bookmarks/highlights/sidebar/modules/colors/index.html",
    UserProfile.SIDEBAR_MODULE_DOMAINS: "bookmarks/highlights/sidebar/modules/domains/index.html",
    UserProfile.SIDEBAR_MODULE_TAGS: "bookmarks/highlights/sidebar/modules/tags/index.html",
    UserProfile.SIDEBAR_MODULE_AUTHORS: "bookmarks/highlights/sidebar/modules/authors/index.html",
}


def module_templates_for(scope: str) -> dict:
    if scope == SIDEBAR_SCOPE_HIGHLIGHTS:
        return HIGHLIGHTS_SIDEBAR_MODULE_TEMPLATES
    return SIDEBAR_MODULE_TEMPLATES


def enabled_module_keys(profile: UserProfile, scope: str = SIDEBAR_SCOPE_BOOKMARKS) -> list[str]:
    """启用且本页有模板的模块，保持用户排序；懒加载占位据此拉取内容。"""
    templates = module_templates_for(scope)
    if scope == SIDEBAR_SCOPE_HIGHLIGHTS:
        modules = profile.get_highlights_sidebar_modules()
    else:
        modules = profile.get_sidebar_modules()
    return [
        item["key"]
        for item in modules
        if item["enabled"] and item["key"] in templates
    ]


def sidebar_module_state(profile: UserProfile, scope: str = SIDEBAR_SCOPE_BOOKMARKS):
    """侧边栏当前模块状态（key + enabled，按用户排序）。

    拖拽排序只改顺序，保存时要把没渲染出来的模块原样带回去，
    所以由服务端把完整状态一次性交给前端。
    """
    templates = module_templates_for(scope)
    if scope == SIDEBAR_SCOPE_HIGHLIGHTS:
        modules = profile.get_highlights_sidebar_modules()
    else:
        modules = profile.get_sidebar_modules()
    return json.dumps(
        [item for item in modules if item["key"] in templates],
        ensure_ascii=False,
    )


@cache_control(private=True, max_age=0)
def sidebar_content(request):
    """AJAX endpoint: return rendered HTML for sidebar modules.

    Query params:
        modules - comma-separated module keys in display order
        ctx     - context key (active|archived|shared|trash|highlights)
    """
    modules_param = request.GET.get("modules", "")
    if not modules_param:
        return HttpResponseBadRequest("missing modules")

    template_context = {}

    requested_modules = []
    for name in modules_param.split(","):
        if name and name not in requested_modules:
            requested_modules.append(name)

    ctx_key = request.GET.get("ctx", "active")
    is_highlights = ctx_key == SIDEBAR_SCOPE_HIGHLIGHTS
    templates = module_templates_for(ctx_key)

    if is_highlights:
        # 高亮页的聚合口径是注记，与书签页的 BookmarkSearch 不通用
        from bookmarks.views.highlights import HighlightSearch

        search = HighlightSearch.from_request(
            request, request.user_profile.highlights_search_preferences or {}
        )
    else:
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )

    if "summary" in requested_modules:
        if ctx_key == "active":
            template_context["sidebar_summary"] = contexts.SidebarUserSummaryContext(
                request, search
            )

    if "bundles" in requested_modules:
        template_context["bundles"] = contexts.BundlesContext(request)

    if "domains" in requested_modules:
        if is_highlights:
            template_context["domains"] = contexts.HighlightDomainsContext(
                request, search
            )
        else:
            domain_ctx_map = {
                "active": contexts.ActiveDomainsContext,
                "archived": contexts.ArchivedDomainsContext,
                "shared": contexts.SharedDomainsContext,
                "trash": contexts.TrashedDomainsContext,
            }
            domain_ctx_cls = domain_ctx_map.get(ctx_key, contexts.ActiveDomainsContext)
            template_context["domains"] = domain_ctx_cls(request, search)

    if "tags" in requested_modules:
        if is_highlights:
            template_context["tag_cloud"] = contexts.HighlightTagCloudContext(
                request, search
            )
        else:
            tag_ctx_map = {
                "active": contexts.ActiveTagCloudContext,
                "archived": contexts.ArchivedTagCloudContext,
                "shared": contexts.SharedTagCloudContext,
                "trash": contexts.TrashedTagCloudContext,
            }
            tag_ctx_cls = tag_ctx_map.get(ctx_key, contexts.ActiveTagCloudContext)
            template_context["tag_cloud"] = tag_ctx_cls(request, search)

    if "authors" in requested_modules:
        if is_highlights:
            template_context["author_cloud"] = contexts.HighlightAuthorCloudContext(
                request, search
            )
        else:
            author_ctx_map = {
                "active": contexts.ActiveAuthorCloudContext,
                "archived": contexts.ArchivedAuthorCloudContext,
                "shared": contexts.SharedAuthorCloudContext,
                "trash": contexts.TrashedAuthorCloudContext,
            }
            author_ctx_cls = author_ctx_map.get(
                ctx_key, contexts.ActiveAuthorCloudContext
            )
            template_context["author_cloud"] = author_ctx_cls(request, search)

    html_parts = []
    for module_name in requested_modules:
        template_name = templates.get(module_name)
        if not template_name:
            continue
        template = loader.get_template(template_name)
        html = template.render(template_context, request)
        html_parts.append(f'<div data-sidebar-module="{module_name}">{html}</div>')

    return HttpResponse("".join(html_parts))
