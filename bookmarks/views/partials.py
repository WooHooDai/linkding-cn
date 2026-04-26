from bookmarks.models import BookmarkSearch
from bookmarks.views import contexts, turbo


def render_bookmark_update(request, bookmark_list, tag_cloud, details, bundles, domains, sidebar_summary=None):
    return turbo.stream(
        request,
        "bookmarks/updates/bookmark_view_stream.html",
        {
            "bookmark_list": bookmark_list,
            "tag_cloud": tag_cloud,
            "details": details,
            "bundles": bundles,
            "domains": domains,
            "sidebar_summary": sidebar_summary,
        },
    )


def active_bookmark_update(request):
    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    bookmark_list = contexts.ActiveBookmarkListContext(request, search)
    tag_cloud = contexts.ActiveTagCloudContext(request, search)
    details = contexts.get_details_context(
        request, contexts.ActiveBookmarkDetailsContext
    )
    bundles = contexts.BundlesContext(request)
    domains = contexts.ActiveDomainsContext(request, search)
    sidebar_summary = contexts.SidebarUserSummaryContext(request, search)
    return render_bookmark_update(
        request, bookmark_list, tag_cloud, details, bundles, domains, sidebar_summary
    )


def archived_bookmark_update(request):
    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    bookmark_list = contexts.ArchivedBookmarkListContext(request, search)
    tag_cloud = contexts.ArchivedTagCloudContext(request, search)
    details = contexts.get_details_context(
        request, contexts.ArchivedBookmarkDetailsContext
    )
    bundles = contexts.BundlesContext(request)
    domains = contexts.ArchivedDomainsContext(request, search)
    return render_bookmark_update(
        request, bookmark_list, tag_cloud, details, bundles, domains
    )


def shared_bookmark_update(request):
    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    bookmark_list = contexts.SharedBookmarkListContext(request, search)
    tag_cloud = contexts.SharedTagCloudContext(request, search)
    details = contexts.get_details_context(
        request, contexts.SharedBookmarkDetailsContext
    )
    bundles = contexts.BundlesContext(request)
    domains = contexts.SharedDomainsContext(request, search)
    return render_bookmark_update(
        request, bookmark_list, tag_cloud, details, bundles, domains
    )


def trashed_bookmark_update(request):
    search = BookmarkSearch.from_request(
        request, request.GET, request.user_profile.search_preferences
    )
    bookmark_list = contexts.TrashedBookmarkListContext(request, search)
    tag_cloud = contexts.TrashedTagCloudContext(request, search)
    details = contexts.get_details_context(
        request, contexts.TrashedBookmarkDetailsContext
    )
    bundles = contexts.BundlesContext(request)
    domains = contexts.TrashedDomainsContext(request, search)
    return render_bookmark_update(
        request, bookmark_list, tag_cloud, details, bundles, domains
    )
