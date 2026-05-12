import time
import urllib.parse

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from bookmarks import queries, utils
from bookmarks.forms import BookmarkForm
from bookmarks.middlewares import (
    PREF_COOKIE_DOMAIN_COMPACT_MODE,
    PREF_COOKIE_DOMAIN_VIEW_MODE,
    PREF_COOKIE_TAG_GROUPING,
)
from bookmarks.models import (
    Bookmark,
    BookmarkSearch,
    UserProfile,
)
from bookmarks.services import assets as asset_actions
from bookmarks.services import (
    favicon_loader,
    preview_image_loader,
    tasks,
    website_loader,
)
from bookmarks.services.bookmarks import (
    archive_bookmark,
    archive_bookmarks,
    create_html_snapshots,
    delete_bookmarks,
    mark_bookmarks_as_read,
    mark_bookmarks_as_unread,
    refresh_bookmarks_metadata,
    remove_all_html_snapshots,
    restore_bookmark,
    restore_bookmarks,
    share_bookmarks,
    tag_bookmarks,
    trash_bookmark,
    trash_bookmarks,
    unarchive_bookmark,
    unarchive_bookmarks,
    unshare_bookmarks,
    untag_bookmarks,
)
from bookmarks.type_defs import HttpRequest
from bookmarks.utils import get_safe_return_url
from bookmarks.views import access, contexts, partials, turbo

SIDEBAR_MODULE_TEMPLATES = {
    UserProfile.SIDEBAR_MODULE_SUMMARY: "bookmarks/sidebar/modules/summary/index.html",
    UserProfile.SIDEBAR_MODULE_BUNDLES: "bookmarks/sidebar/modules/bundles/index.html",
    UserProfile.SIDEBAR_MODULE_DOMAINS: "bookmarks/sidebar/modules/domains/index.html",
    UserProfile.SIDEBAR_MODULE_TAGS: "bookmarks/sidebar/modules/tags/index.html",
}


def _build_sidebar_modules(request: HttpRequest, context: dict) -> list[dict]:
    available_modules = {
        UserProfile.SIDEBAR_MODULE_SUMMARY: bool(context.get("sidebar_summary")),
        UserProfile.SIDEBAR_MODULE_BUNDLES: context.get("bundles") is not None,
        UserProfile.SIDEBAR_MODULE_DOMAINS: context.get("domains") is not None,
        UserProfile.SIDEBAR_MODULE_TAGS: context.get("tag_cloud") is not None,
    }
    wrapper_ids = {
        UserProfile.SIDEBAR_MODULE_SUMMARY: "sidebar-user-summary-container",
    }

    modules = []
    for item in request.user_profile.get_sidebar_modules():
        key = item["key"]
        if not item["enabled"] or not available_modules.get(key):
            continue
        modules.append(
            {
                "key": key,
                "template_name": SIDEBAR_MODULE_TEMPLATES[key],
                "wrapper_id": wrapper_ids.get(key),
            }
        )

    return modules


SUMMARY_ACTIONS = {"toggle_mode", "toggle_show_weekdays", "toggle_show_details", "nav_month", "nav_week"}
DOMAIN_ACTIONS = {"toggle_domain_view_mode", "toggle_domain_compact_mode"}
TAG_ACTIONS = {"toggle_tag_grouping"}

# Maps preference actions to cookie names for anonymous users
_ANONYMOUS_PREF_COOKIE_MAP = {
    "toggle_domain_view_mode": PREF_COOKIE_DOMAIN_VIEW_MODE,
    "toggle_domain_compact_mode": PREF_COOKIE_DOMAIN_COMPACT_MODE,
    "toggle_tag_grouping": PREF_COOKIE_TAG_GROUPING,
}
_PREF_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year


def _set_anonymous_pref_cookies(response, action: str, value: str):
    """Set preference cookies on the response for anonymous users."""
    cookie_name = _ANONYMOUS_PREF_COOKIE_MAP.get(action)
    if cookie_name:
        response.set_cookie(cookie_name, value, max_age=_PREF_COOKIE_MAX_AGE, httponly=True)


def _get_domain_tag_contexts(request: HttpRequest):
    """Return the appropriate (DomainsContext, TagCloudContext) classes for the current page."""
    path = request.path
    if "/shared" in path:
        return contexts.SharedDomainsContext, contexts.SharedTagCloudContext
    if "/archived" in path:
        return contexts.ArchivedDomainsContext, contexts.ArchivedTagCloudContext
    if "/trash" in path:
        return contexts.TrashedDomainsContext, contexts.TrashedTagCloudContext
    return contexts.ActiveDomainsContext, contexts.ActiveTagCloudContext


def _handle_preference_toggle(request: HttpRequest):
    action = request.POST["pref_action"]
    profile = request.user_profile
    is_anonymous = not request.user.is_authenticated

    if action == "toggle_mode":
        profile.sum_mode = request.POST["value"]
    elif action == "toggle_show_weekdays":
        profile.sum_show_weekdays = request.POST["value"] == "1"
    elif action == "toggle_show_details":
        profile.sum_show_details = request.POST["value"] == "1"
    elif action == "toggle_domain_view_mode":
        profile.domain_view_mode = request.POST["value"]
    elif action == "toggle_domain_compact_mode":
        profile.domain_compact_mode = request.POST["value"] == "1"
    elif action == "toggle_tag_grouping":
        profile.tag_grouping = request.POST["value"]

    if is_anonymous:
        request.user_profile = profile
    else:
        profile.save()

    # For nav_month/nav_week, inject the target value into GET so the context picks it up
    if action == "nav_month":
        request.GET = request.GET.copy()
        request.GET["sum_month"] = request.POST["value"]
    elif action == "nav_week":
        request.GET = request.GET.copy()
        request.GET["sum_week"] = request.POST["value"]

    search = BookmarkSearch.from_request(
        request, request.GET, profile.search_preferences
    )

    if action in SUMMARY_ACTIONS:
        sidebar_summary = contexts.SidebarUserSummaryContext(request, search)
        response = turbo.update(
            request,
            "sidebar-user-summary-container",
            "bookmarks/sidebar/modules/summary/index.html",
            {"sidebar_summary": sidebar_summary},
        )
    elif action in DOMAIN_ACTIONS:
        DomainsCtx, _ = _get_domain_tag_contexts(request)
        domains = DomainsCtx(request, search)
        response = turbo.update(
            request,
            "domain-section-container",
            "bookmarks/sidebar/modules/domains/index.html",
            {"domains": domains},
        )
    elif action in TAG_ACTIONS:
        _, TagCloudCtx = _get_domain_tag_contexts(request)
        tag_cloud = TagCloudCtx(request, search)
        response = turbo.update(
            request,
            "tag-section-container",
            "bookmarks/sidebar/modules/tags/index.html",
            {"tag_cloud": tag_cloud},
        )
    else:
        return HttpResponseRedirect(reverse("linkding:bookmarks.index"))

    if is_anonymous:
        _set_anonymous_pref_cookies(response, action, request.POST["value"])

    return response


@login_required
def index(request: HttpRequest):
    if request.method == "POST":
        if "pref_action" in request.POST:
            return _handle_preference_toggle(request)
        return search_action(request)

    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    create_bundle_query_string = _get_create_bundle_query_string(search)
    bookmark_list = contexts.ActiveBookmarkListContext(request, search)
    sidebar_summary = contexts.SidebarUserSummaryContext(request, search)
    bundles = contexts.BundlesContext(request)
    domains = contexts.ActiveDomainsContext(request, search)
    tag_cloud = contexts.ActiveTagCloudContext(request, search)
    bookmark_details = contexts.get_details_context(
        request, contexts.ActiveBookmarkDetailsContext
    )

    return render_bookmarks_view(
        request,
        "bookmarks/index.html",
        {
            "page_title": _("Bookmarks - Linkding"),
            "bookmark_list": bookmark_list,
            "sidebar_summary": sidebar_summary,
            "bundles": bundles,
            "domains": domains,
            "tag_cloud": tag_cloud,
            "details": bookmark_details,
            "create_bundle_query_string": create_bundle_query_string,
        },
    )


@login_required
def archived(request: HttpRequest):
    if request.method == "POST":
        if "pref_action" in request.POST:
            return _handle_preference_toggle(request)
        return search_action(request)

    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    create_bundle_query_string = _get_create_bundle_query_string(search)
    bookmark_list = contexts.ArchivedBookmarkListContext(request, search)
    bundles = contexts.BundlesContext(request)
    domains = contexts.ArchivedDomainsContext(request, search)
    tag_cloud = contexts.ArchivedTagCloudContext(request, search)
    bookmark_details = contexts.get_details_context(
        request, contexts.ArchivedBookmarkDetailsContext
    )

    return render_bookmarks_view(
        request,
        "bookmarks/archive.html",
        {
            "page_title": _("Archived bookmarks - Linkding"),
            "bookmark_list": bookmark_list,
            "bundles": bundles,
            "domains": domains,
            "tag_cloud": tag_cloud,
            "details": bookmark_details,
            "create_bundle_query_string": create_bundle_query_string,
        },
    )


def shared(request: HttpRequest):
    if request.method == "POST":
        if "pref_action" in request.POST:
            return _handle_preference_toggle(request)
        return search_action(request)

    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    create_bundle_query_string = _get_create_bundle_query_string(search)
    bookmark_list = contexts.SharedBookmarkListContext(request, search)
    domains = contexts.SharedDomainsContext(request, search)
    tag_cloud = contexts.SharedTagCloudContext(request, search)
    bookmark_details = contexts.get_details_context(
        request, contexts.SharedBookmarkDetailsContext
    )
    public_only = not request.user.is_authenticated
    users = queries.query_shared_bookmark_users(
        request.user_profile, bookmark_list.search, public_only
    )
    return render_bookmarks_view(
        request,
        "bookmarks/shared.html",
        {
            "page_title": _("Shared bookmarks - Linkding"),
            "bookmark_list": bookmark_list,
            "domains": domains,
            "tag_cloud": tag_cloud,
            "details": bookmark_details,
            "users": users,
            "rss_feed_url": reverse("linkding:feeds.public_shared"),
            "create_bundle_query_string": create_bundle_query_string,
        },
    )


@login_required
def trashed(request: HttpRequest):
    if request.method == "POST":
        if "pref_action" in request.POST:
            return _handle_preference_toggle(request)
        return search_action(request)

    # 如果用户的回收站搜索偏好为空，设置默认的删除时间降序
    if not request.user_profile.trash_search_preferences:
        request.user_profile.trash_search_preferences = {
            "sort": BookmarkSearch.SORT_DELETED_DESC
        }
        request.user_profile.save()

    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.trash_search_preferences
    )
    create_bundle_query_string = _get_create_bundle_query_string(search)
    bookmark_list = contexts.TrashedBookmarkListContext(request, search)
    bundles = contexts.BundlesContext(request)
    domains = contexts.TrashedDomainsContext(request, search)
    tag_cloud = contexts.TrashedTagCloudContext(request, search)
    bookmark_details = contexts.get_details_context(
        request, contexts.TrashedBookmarkDetailsContext
    )

    return render_bookmarks_view(
        request,
        "bookmarks/trash.html",
        {
            "page_title": _("Trash - Linkding"),
            "bookmark_list": bookmark_list,
            "bundles": bundles,
            "domains": domains,
            "tag_cloud": tag_cloud,
            "details": bookmark_details,
            "create_bundle_query_string": create_bundle_query_string,
        },
    )


def render_bookmarks_view(request: HttpRequest, template_name, context):
    context["sidebar_modules"] = _build_sidebar_modules(request, context)

    if context["details"]:
        context["page_title"] = _("Bookmark details - Linkding")

    if turbo.is_frame(request, "details-modal"):
        return render(
            request,
            "bookmarks/updates/details-modal-frame.html",
            context,
        )

    if turbo.accept_bookmark_page_stream(request):
        turbo_renderers = {
            "bookmarks/index.html": lambda: partials.render_bookmark_update(
                request,
                context["bookmark_list"],
                context["tag_cloud"],
                context["details"],
                context["bundles"],
                context["domains"],
                context.get("sidebar_summary"),
            ),
            "bookmarks/archive.html": lambda: partials.render_bookmark_update(
                request,
                context["bookmark_list"],
                context["tag_cloud"],
                context["details"],
                context["bundles"],
                context["domains"],
            ),
            "bookmarks/shared.html": lambda: partials.render_bookmark_update(
                request,
                context["bookmark_list"],
                context["tag_cloud"],
                context["details"],
                context["bundles"],
                context["domains"],
            ),
            "bookmarks/trash.html": lambda: partials.render_bookmark_update(
                request,
                context["bookmark_list"],
                context["tag_cloud"],
                context["details"],
                context["bundles"],
                context["domains"],
            ),
        }
        renderer = turbo_renderers.get(template_name)
        if renderer:
            return renderer()

    return render(
        request,
        template_name,
        context,
    )


def _get_create_bundle_query_string(search: BookmarkSearch) -> str:
    """
    Generates a URL query string for the 'create bundle' link.
    This includes both explicit query parameters and default preferences.
    """
    params = search.query_params.copy()
    ensure_params = [
        "sort",
        "shared",
        "unread",
        "date_filter_by",
        "date_filter_type",
        "date_filter_relative_string",
    ]

    for param in ensure_params:
        if param not in params:
            value = getattr(search, param)
            if value is not None and value != "":
                params[param] = value

    if search.date_filter_type == "absolute":
        if "date_filter_start" not in params and search.date_filter_start:
            params["date_filter_start"] = search.date_filter_start.isoformat()
        if "date_filter_end" not in params and search.date_filter_end:
            params["date_filter_end"] = search.date_filter_end.isoformat()

    return urllib.parse.urlencode(params)


def search_action(request: HttpRequest):
    if "save" in request.POST:
        if not request.user.is_authenticated:
            return HttpResponseForbidden()
        search = BookmarkSearch.from_request(request, request.POST)

        # 根据当前页面路径决定保存到哪个偏好设置字段
        if request.path.endswith("/trash") or request.path.endswith("/trash/"):
            # 回收站页面，保存到trash_search_preferences
            request.user_profile.trash_search_preferences = search.preferences_dict
        else:
            # 其他页面，保存到search_preferences
            request.user_profile.search_preferences = search.preferences_dict

        request.user_profile.save()

    # Handle random sort request
    if "sort" in request.POST and request.POST["sort"] == "random":
        new_seed = int(time.time())
        request.session["random_sort_seed"] = new_seed

    # redirect to base url including new query params
    search = BookmarkSearch.from_request(
        request, request.POST, request.user_profile.search_preferences
    )
    base_url = request.path
    query_params = search.query_params
    query_string = urllib.parse.urlencode(query_params)
    url = base_url if not query_string else base_url + "?" + query_string
    return HttpResponseRedirect(url)


def convert_tag_string(tag_string: str):
    # Tag strings coming from inputs are space-separated, however services.bookmarks functions expect comma-separated
    # strings
    return tag_string.replace(" ", ",")


@login_required
def new(request: HttpRequest):
    form = BookmarkForm(request)
    if request.method == "POST" and form.is_valid():
        form.save()
        if form.is_auto_close:
            return HttpResponseRedirect(reverse("linkding:bookmarks.close"))
        else:
            return HttpResponseRedirect(reverse("linkding:bookmarks.index"))

    status = 422 if request.method == "POST" and not form.is_valid() else 200
    context = {"form": form, "return_url": reverse("linkding:bookmarks.index")}

    return render(request, "bookmarks/new.html", context, status=status)


@login_required
def edit(request: HttpRequest, bookmark_id: int):
    bookmark = access.bookmark_write(request, bookmark_id)
    form = BookmarkForm(request, instance=bookmark)
    return_url = get_safe_return_url(
        request.GET.get("return_url"), reverse("linkding:bookmarks.index")
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        return HttpResponseRedirect(return_url)

    status = 422 if request.method == "POST" and not form.is_valid() else 200
    context = {
        "form": form,
        "bookmark_id": bookmark_id,
        "return_url": return_url,
        "preview_image_file": bookmark.preview_image_file,
    }

    return render(request, "bookmarks/edit.html", context, status=status)


def remove(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    bookmark.delete()


def trash(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    trash_bookmark(bookmark)


def restore(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    restore_bookmark(bookmark)


def archive(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    archive_bookmark(bookmark)


def unarchive(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    unarchive_bookmark(bookmark)


def unshare(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    bookmark.shared = False
    bookmark.save()


def mark_as_read(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    bookmark.unread = False
    bookmark.save()


def prefetch_favicon(request: HttpRequest):
    if not request.user.profile.enable_favicons:
        return JsonResponse({"status": "disabled"})

    url = request.GET.get("url")
    if not url:
        return JsonResponse({"error": _("URL parameter is missing")}, status=400)

    cached_favicon = favicon_loader.get_cached_favicon(url)
    if cached_favicon:
        return JsonResponse(
            {"status": "success", "favicon_file": cached_favicon.filename}
        )

    favicon_file = favicon_loader.load_favicon(url, timeout=5)

    if favicon_file:
        return JsonResponse({"status": "success", "favicon_file": favicon_file})
    else:
        return JsonResponse(
            {"status": "error", "message": _("Failed to prefetch favicon")}
        )


def load_temporary_preview_image(request: HttpRequest):
    image_url = request.GET.get("url")
    if not image_url:
        return HttpResponseBadRequest(_("URL parameter is missing"))
    try:
        image_name = preview_image_loader.load_temporary_preview_image(image_url)
        image_path = preview_image_loader._get_temporary_image_path(image_name)
        tasks.delete_preview_image_temp_file.schedule(args=(image_path,), delay=600)

        temp_path = settings.STATIC_URL + "tmp" + "/" + image_name
        result = {"temp_path": temp_path}
        print(result)
        print(JsonResponse(result))
        return JsonResponse(result)
    except Exception as e:
        return HttpResponseBadRequest(
            _("Failed to download image: %(error)s") % {"error": e}
        )


def create_html_snapshot(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    tasks.create_html_snapshot(bookmark)


def upload_asset(request: HttpRequest, bookmark_id: int | str):
    if settings.LD_DISABLE_ASSET_UPLOAD:
        return HttpResponseForbidden(_("Asset upload is disabled"))

    bookmark = access.bookmark_write(request, bookmark_id)
    file = request.FILES.get("upload_asset_file")
    if not file:
        return HttpResponseBadRequest(_("No file provided"))

    asset_actions.upload_asset(bookmark, file)


def remove_asset(request: HttpRequest, asset_id: int | str):
    asset = access.asset_write(request, asset_id)
    asset_actions.remove_asset(asset)


def rename_asset(request: HttpRequest, asset_id: int | str):
    asset = access.asset_write(request, asset_id)
    new_display_name = request.POST.get("new_display_name", "").strip()
    asset_actions.rename_asset(asset, new_display_name)


def update_state(request: HttpRequest, bookmark_id: int | str):
    bookmark = access.bookmark_write(request, bookmark_id)
    bookmark.is_archived = request.POST.get("is_archived") == "on"
    bookmark.unread = request.POST.get("unread") == "on"
    bookmark.shared = request.POST.get("shared") == "on"
    bookmark.save()


@login_required
def index_action(request: HttpRequest):
    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    query = queries.query_bookmarks(request.user, request.user_profile, search)

    response = handle_action(request, query)
    if response:
        return response

    if turbo.accept(request):
        return partials.active_bookmark_update(request)

    return utils.redirect_with_query(request, reverse("linkding:bookmarks.index"))


@login_required
def archived_action(request: HttpRequest):
    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    query = queries.query_archived_bookmarks(request.user, request.user_profile, search)

    response = handle_action(request, query)
    if response:
        return response

    if turbo.accept(request):
        return partials.archived_bookmark_update(request)

    return utils.redirect_with_query(request, reverse("linkding:bookmarks.archived"))


@login_required
def shared_action(request: HttpRequest):
    if "bulk_execute" in request.POST:
        return HttpResponseBadRequest(_("View does not support bulk actions"))

    response = handle_action(request)
    if response:
        return response

    if turbo.accept(request):
        return partials.shared_bookmark_update(request)

    return utils.redirect_with_query(request, reverse("linkding:bookmarks.shared"))


@login_required
def trashed_action(request: HttpRequest):
    # 如果用户的回收站搜索偏好为空，设置默认的删除时间降序
    if not request.user_profile.trash_search_preferences:
        request.user_profile.trash_search_preferences = {
            "sort": BookmarkSearch.SORT_DELETED_DESC
        }
        request.user_profile.save()

    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.trash_search_preferences
    )
    query = queries.query_trashed_bookmarks(request.user, request.user_profile, search)

    response = handle_action(request, query)
    if response:
        return response

    if turbo.accept(request):
        return partials.trashed_bookmark_update(request)

    return utils.redirect_with_query(request, reverse("linkding:bookmarks.trashed"))


def handle_action(request: HttpRequest, query: QuerySet[Bookmark] = None):
    # Single bookmark actions
    if "archive" in request.POST:
        return archive(request, request.POST["archive"])
    if "unarchive" in request.POST:
        return unarchive(request, request.POST["unarchive"])
    if "remove" in request.POST:
        return remove(request, request.POST["remove"])
    if "mark_as_read" in request.POST:
        return mark_as_read(request, request.POST["mark_as_read"])
    if "unshare" in request.POST:
        return unshare(request, request.POST["unshare"])
    if "create_html_snapshot" in request.POST:
        return create_html_snapshot(request, request.POST["create_html_snapshot"])
    if "upload_asset" in request.POST:
        return upload_asset(request, request.POST["upload_asset"])
    if "remove_asset" in request.POST:
        return remove_asset(request, request.POST["remove_asset"])
    if "rename_asset" in request.POST:
        return rename_asset(request, request.POST["rename_asset"])
    if "trash" in request.POST:
        return trash(request, request.POST["trash"])
    if "restore" in request.POST:
        return restore(request, request.POST["restore"])

    # State updates
    if "update_state" in request.POST:
        return update_state(request, request.POST["update_state"])

    # Bulk actions
    if "bulk_execute" in request.POST:
        if query is None:
            raise ValueError("Query must be provided for bulk actions")

        bulk_action = request.POST["bulk_action"]

        # Determine set of bookmarks
        if request.POST.get("bulk_select_across") == "on":
            # Query full list of bookmarks across all pages
            bookmark_ids = query.only("id").values_list("id", flat=True)
        else:
            # Use only selected bookmarks
            bookmark_ids = request.POST.getlist("bookmark_id")

        if bulk_action == "bulk_archive":
            return archive_bookmarks(bookmark_ids, request.user)
        if bulk_action == "bulk_unarchive":
            return unarchive_bookmarks(bookmark_ids, request.user)
        if bulk_action == "bulk_delete":
            return delete_bookmarks(bookmark_ids, request.user)
        if bulk_action == "bulk_tag":
            tag_string = convert_tag_string(request.POST["bulk_tag_string"])
            return tag_bookmarks(bookmark_ids, tag_string, request.user)
        if bulk_action == "bulk_untag":
            tag_string = convert_tag_string(request.POST["bulk_tag_string"])
            return untag_bookmarks(bookmark_ids, tag_string, request.user)
        if bulk_action == "bulk_read":
            return mark_bookmarks_as_read(bookmark_ids, request.user)
        if bulk_action == "bulk_unread":
            return mark_bookmarks_as_unread(bookmark_ids, request.user)
        if bulk_action == "bulk_share":
            return share_bookmarks(bookmark_ids, request.user)
        if bulk_action == "bulk_unshare":
            return unshare_bookmarks(bookmark_ids, request.user)
        if bulk_action == "bulk_refresh":
            return refresh_bookmarks_metadata(bookmark_ids, request.user)
        if bulk_action == "bulk_trash":
            return trash_bookmarks(bookmark_ids, request.user)
        if bulk_action == "bulk_restore":
            return restore_bookmarks(bookmark_ids, request.user)
        if bulk_action == "bulk_snapshot":
            return create_html_snapshots(bookmark_ids, request.user)
        if bulk_action == "bulk_remove_snapshot":
            return remove_all_html_snapshots(bookmark_ids, request.user)


@login_required
def close(request: HttpRequest):
    return render(request, "bookmarks/close.html")


@login_required
def read(request: HttpRequest, bookmark_id: int):
    bookmark = access.bookmark_read(request, bookmark_id)
    try:
        content = website_loader.load_full_page(bookmark.url)
    except Exception as e:
        content = f"<html><body><p>{_('Unable to load page content: %(error)s') % {'error': str(e)}}</p></body></html>"

    return render(
        request,
        "bookmarks/read.html",
        {
            "content": content,
        },
    )
