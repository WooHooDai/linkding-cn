from django.http import HttpResponse, HttpResponseBadRequest
from django.template import loader
from django.views.decorators.cache import cache_control

from bookmarks.models import BookmarkSearch
from bookmarks.views import contexts


@cache_control(private=True, max_age=0)
def sidebar_content(request):
    """AJAX endpoint: return rendered HTML for sidebar modules.
    
    Query params:
        modules - comma-separated list of modules to load (domains,tags,bundles,summary)
        ctx     - context key (active|archived|shared|trash|highlights)
    """
    modules_param = request.GET.get("modules", "")
    if not modules_param:
        return HttpResponseBadRequest("missing modules")
    
    requested_modules = set(modules_param.split(","))
    
    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    
    ctx_key = request.GET.get("ctx", "active")
    
    template_context = {}
    
    if "summary" in requested_modules:
        if ctx_key == "active":
            template_context["sidebar_summary"] = contexts.SidebarUserSummaryContext(request, search)
    
    if "bundles" in requested_modules:
        template_context["bundles"] = contexts.BundlesContext(request)
    
    if "domains" in requested_modules:
        domain_ctx_map = {
            "active": contexts.ActiveDomainsContext,
            "archived": contexts.ArchivedDomainsContext,
            "shared": contexts.SharedDomainsContext,
            "trash": contexts.TrashedDomainsContext,
            "highlights": contexts.HighlightDomainsContext,
        }
        domain_ctx_cls = domain_ctx_map.get(ctx_key, contexts.ActiveDomainsContext)
        template_context["domains"] = domain_ctx_cls(request, search)
    
    if "tags" in requested_modules:
        tag_ctx_map = {
            "active": contexts.ActiveTagCloudContext,
            "archived": contexts.ArchivedTagCloudContext,
            "shared": contexts.SharedTagCloudContext,
            "trash": contexts.TrashedTagCloudContext,
            "highlights": contexts.HighlightTagCloudContext,
        }
        tag_ctx_cls = tag_ctx_map.get(ctx_key, contexts.ActiveTagCloudContext)
        template_context["tag_cloud"] = tag_ctx_cls(request, search)

    if "authors" in requested_modules:
        author_ctx_map = {
            "active": contexts.ActiveAuthorCloudContext,
            "archived": contexts.ArchivedAuthorCloudContext,
            "shared": contexts.SharedAuthorCloudContext,
            "trash": contexts.TrashedAuthorCloudContext,
            "highlights": contexts.ActiveAuthorCloudContext,
        }
        author_ctx_cls = author_ctx_map.get(ctx_key, contexts.ActiveAuthorCloudContext)
        template_context["author_cloud"] = author_ctx_cls(request, search)

    module_templates = {
        "summary": "bookmarks/sidebar/modules/summary/index.html",
        "bundles": "bookmarks/sidebar/modules/bundles/index.html",
        "domains": "bookmarks/sidebar/modules/domains/index.html",
        "tags": "bookmarks/sidebar/modules/tags/index.html",
        "authors": "bookmarks/sidebar/modules/authors/index.html",
    }

    html_parts = []
    for module_name in ["summary", "bundles", "domains", "tags", "authors"]:
        if module_name in requested_modules and module_name in module_templates:
            template = loader.get_template(module_templates[module_name])
            html = template.render(template_context, request)
            html_parts.append(f'<div data-sidebar-module="{module_name}">{html}</div>')
    
    return HttpResponse("".join(html_parts))
