from bookmarks.models import BookmarkSearch
from bookmarks.views import contexts, turbo


def render_bookmark_update(request, bookmark_list, tag_cloud, details, bundles):
    return turbo.stream(
        request,
        "bookmarks/updates/bookmark_view_stream.html",
        {
            "bookmark_list": bookmark_list,
            "tag_cloud": tag_cloud,
            "details": details,
            "bundles": bundles,
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
    return render_bookmark_update(request, bookmark_list, tag_cloud, details, bundles)


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
    return render_bookmark_update(request, bookmark_list, tag_cloud, details, bundles)


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
    return render_bookmark_update(request, bookmark_list, tag_cloud, details, bundles)


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
    return render_bookmark_update(request, bookmark_list, tag_cloud, details, bundles)
