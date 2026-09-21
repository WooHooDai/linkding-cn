from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.i18n import JavaScriptCatalog, set_language

from bookmarks import feeds, views
from bookmarks.views import tag_tree, domain_tree, sidebar_content
from bookmarks.admin import linkding_admin_site
from bookmarks.api import routes as api_routes

urlpatterns = [
    # Root view handling redirection based on user authentication
    re_path(r"^$", views.root, name="root"),
    # Bookmarks
    path("bookmarks", views.bookmarks.index, name="bookmarks.index"),
    path(
        "bookmarks/action", views.bookmarks.index_action, name="bookmarks.index.action"
    ),
    path("bookmarks/archived", views.bookmarks.archived, name="bookmarks.archived"),
    path(
        "bookmarks/archived/action",
        views.bookmarks.archived_action,
        name="bookmarks.archived.action",
    ),
    path("bookmarks/shared", views.bookmarks.shared, name="bookmarks.shared"),
    path(
        "bookmarks/shared/action",
        views.bookmarks.shared_action,
        name="bookmarks.shared.action",
    ),
    path("bookmarks/new", views.bookmarks.new, name="bookmarks.new"),
    path("bookmarks/close", views.bookmarks.close, name="bookmarks.close"),
    path(
        "bookmarks/<int:bookmark_id>/edit", views.bookmarks.edit, name="bookmarks.edit"
    ),
    path(
        "bookmarks/<int:bookmark_id>/read", views.reader.read, name="bookmarks.read"
    ),
    path(
        "bookmarks/<int:bookmark_id>/export", views.reader.export, name="bookmarks.export"
    ),
    path(
        "bookmarks/<int:bookmark_id>/trash",
        views.bookmarks.trashed,
        name="bookmarks.trash",
    ),
    path("bookmarks/trash", views.bookmarks.trashed, name="bookmarks.trashed"),
    path(
        "bookmarks/trash/action",
        views.bookmarks.trashed_action,
        name="bookmarks.trashed.action",
    ),
    path(
        "favicon/<str:domain>",
        views.bookmarks.favicon_image,
        name="favicon_image",
    ),
    path(
        "bookmarks/load_temporary_preview_image",
        views.bookmarks.load_temporary_preview_image,
        name="bookmarks.load_temporary_preview_image",
    ),
    # Assets
    path(
        "assets/<int:asset_id>",
        views.assets.view,
        name="assets.view",
    ),
    # Bundles
    path("bundles", views.bundles.index, name="bundles.index"),
    path("bundles/action", views.bundles.action, name="bundles.action"),
    path("bundles/new", views.bundles.new, name="bundles.new"),
    path("bundles/<int:bundle_id>/edit", views.bundles.edit, name="bundles.edit"),
    path("bundles/preview", views.bundles.preview, name="bundles.preview"),
    # Tags
    path("tags", views.tags.tags_index, name="tags.index"),
    # Tag tree AJAX
    path("tag-tree/children", tag_tree.tag_tree_children, name="tag_tree.children"),
    path("domain-tree/children", domain_tree.domain_tree_children, name="domain_tree.children"),
    path("sidebar-content", sidebar_content.sidebar_content, name="sidebar.content"),
    # Highlights
    path(
        "bookmarks/highlights",
        views.highlights.index,
        name="bookmarks.highlights",
    ),
    # Bookmark health check（任务进度与单条检查 API；页面/入口已并入书签列表与 bundle 列表）
    path(
        "bookmarks/health/<int:job_id>/status",
        views.health_check.job_status,
        name="health.status",
    ),
    path(
        "bookmarks/health/<int:job_id>/control",
        views.health_check.job_control,
        name="health.job_control",
    ),
    path(
        "bookmarks/health/<int:bookmark_id>/check",
        views.health_check.check_single,
        name="health.check",
    ),
    path("tags/new", views.tags.tag_new, name="tags.new"),
    path("tags/<int:tag_id>/edit", views.tags.tag_edit, name="tags.edit"),
    path("tags/merge", views.tags.tag_merge, name="tags.merge"),
    # Settings
    path("settings", views.settings.general, name="settings.index"),
    path("settings/general", views.settings.general, name="settings.general"),
    path("settings/save", views.settings.save, name="settings.save"),
    path("settings/update", views.settings.update, name="settings.update"),
    path(
        "settings/integrations",
        views.settings.integrations,
        name="settings.integrations",
    ),
    path(
        "settings/integrations/create-api-token",
        views.settings.create_api_token,
        name="settings.integrations.create_api_token",
    ),
    path(
        "settings/integrations/delete-api-token",
        views.settings.delete_api_token,
        name="settings.integrations.delete_api_token",
    ),
    path("settings/import", views.settings.bookmark_import, name="settings.import"),
    path("settings/export", views.settings.bookmark_export, name="settings.export"),

    # Site Adapters
    path("admin/site-adapters", views.site_adapters, name="settings.site_adapters"),
    path("admin/site-adapters/action", views.site_adapters_action, name="settings.site_adapters.action"),
    path("admin/site-adapters/save-cookie", views.save_cookie, name="settings.site_adapters.save_cookie"),
    path("admin/site-adapters/credentials/list", views.shared_credential_list, name="settings.site_adapters.shared_credential_list"),
    path("admin/site-adapters/credentials/save", views.shared_credential_save, name="settings.site_adapters.shared_credential_save"),
    path("admin/site-adapters/credentials/delete", views.shared_credential_delete, name="settings.site_adapters.shared_credential_delete"),
    path("admin/site-adapters/domain/read", views.domain_read, name="settings.site_adapters.domain_read"),
    path("admin/site-adapters/domain/save", views.domain_save, name="settings.site_adapters.domain_save"),
    path("admin/site-adapters/domain/create", views.domain_create, name="settings.site_adapters.domain_create"),
    path("admin/site-adapters/domain/rename", views.domain_rename, name="settings.site_adapters.domain_rename"),
    path("admin/site-adapters/domain/delete", views.domain_delete, name="settings.site_adapters.domain_delete"),
    path("admin/site-adapters/domains/all", views.all_domains, name="settings.site_adapters.all_domains"),
    path("admin/site-adapters/local-domain-toggle", views.local_domain_toggle, name="settings.site_adapters.local_domain_toggle"),
    path("admin/site-adapters/preview-image", views.preview_image_proxy, name="settings.site_adapters.preview_image"),
    path("admin/site-adapters/subscription/domain-read", views.subscription_domain_read, name="settings.site_adapters.subscription_domain_read"),
    path("admin/site-adapters/subscription/domain-toggle", views.subscription_domain_toggle, name="settings.site_adapters.subscription_domain_toggle"),
    path("admin/site-adapters/subscription/manage", views.subscription_manage, name="settings.site_adapters.subscription_manage"),
    path("admin/site-adapters/view-snapshot", views.view_snapshot, name="settings.site_adapters.view_snapshot"),
    path("admin/site-adapters/view-reader", views.view_reader, name="settings.site_adapters.view_reader"),
    path("settings/adapters", views.adapters_page, name="settings.adapters"),
    path("settings/adapters/credentials/api", views.user_credentials, name="settings.credentials_api"),
    path("settings/adapters/credentials/match-domain", views.match_domain_config, name="settings.match_domain_config"),
    path("settings/adapters/snapshot_toggles", views.snapshot_toggles, name="settings.snapshot_toggles"),
    # Toasts
    path("toasts/acknowledge", views.toasts.acknowledge, name="toasts.acknowledge"),
    # API
    path("api/", include(api_routes.default_router.urls)),
    path("api/bookmarks/", include(api_routes.bookmark_router.urls)),
    path(
        "api/bookmarks/<int:bookmark_id>/assets/",
        include(api_routes.bookmark_asset_router.urls),
    ),
    path(
        "api/bookmarks/<int:bookmark_id>/annotations/",
        include(api_routes.bookmark_annotation_router.urls),
    ),
    # 阅读进度 API（GET 获取 / PATCH 保存，支持 sendBeacon POST）
    path(
        "api/bookmarks/<int:bookmark_id>/reading-progress/",
        api_routes.ReadingProgressView.as_view(),
        name="bookmark_reading_progress",
    ),
    path("api/annotations/", include(api_routes.annotation_router.urls)),
    path("api/tags/", include(api_routes.tag_router.urls)),
    path("api/bundles/", include(api_routes.bundle_router.urls)),
    path("api/user/", include(api_routes.user_router.urls)),
    path(
        "api/render-markdown/",
        api_routes.render_markdown_api,
        name="api.render_markdown",
    ),
    # Feeds
    path("feeds/<str:feed_key>/all", feeds.AllBookmarksFeed(), name="feeds.all"),
    path(
        "feeds/<str:feed_key>/unread", feeds.UnreadBookmarksFeed(), name="feeds.unread"
    ),
    path(
        "feeds/<str:feed_key>/shared", feeds.SharedBookmarksFeed(), name="feeds.shared"
    ),
    path("feeds/shared", feeds.PublicSharedBookmarksFeed(), name="feeds.public_shared"),
    # Health check
    path("health", views.health, name="health"),
    # Manifest
    path("manifest.json", views.manifest, name="manifest"),
    # Custom CSS
    path("custom_css", views.custom_css, name="custom_css"),
    # OpenSearch
    path("opensearch.xml", views.opensearch, name="opensearch"),
]

# Put all linkding URLs into a linkding namespace
urlpatterns = [path("", include((urlpatterns, "linkding")))]

# Auth
urlpatterns += [
    path(
        "login/",
        views.auth.LinkdingLoginView.as_view(redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "change-password/",
        views.auth.LinkdingPasswordChangeView.as_view(),
        name="change_password",
    ),
    path(
        "password-change-done/",
        auth_views.PasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    path("i18n/language/", views.settings.update_language, name="language-update"),
    path("i18n/setlang/", set_language, name="set_language"),
    path(
        "jsi18n/",
        JavaScriptCatalog.as_view(packages=["bookmarks"]),
        name="javascript-catalog",
    ),
]

# Admin
urlpatterns.append(path("admin/", linkding_admin_site.urls))

# OIDC
if settings.LD_ENABLE_OIDC:
    urlpatterns.append(path("oidc/", include("mozilla_django_oidc.urls")))

# Debug toolbar
if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns.append(path("__debug__/", include(debug_toolbar.urls)))

# Context path
if settings.LD_CONTEXT_PATH:
    urlpatterns = [path(settings.LD_CONTEXT_PATH, include(urlpatterns))]
