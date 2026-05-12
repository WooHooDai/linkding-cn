from django.conf import settings
from django.contrib.auth.middleware import RemoteUserMiddleware
from django.utils import translation

from bookmarks.models import GlobalSettings, UserProfile


class CustomRemoteUserMiddleware(RemoteUserMiddleware):
    header = settings.LD_AUTH_PROXY_USERNAME_HEADER


class UserLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            profile = getattr(user, "profile", None)
            language = getattr(profile, "language", None)
            if language:
                translation.activate(language)
                request.LANGUAGE_CODE = translation.get_language()

        response = self.get_response(request)

        return response


default_global_settings = GlobalSettings()

# Cookie names for anonymous user preferences (stored client-side)
PREF_COOKIE_DOMAIN_VIEW_MODE = "ld_domain_view_mode"
PREF_COOKIE_DOMAIN_COMPACT_MODE = "ld_domain_compact_mode"
PREF_COOKIE_TAG_GROUPING = "ld_tag_grouping"


def _build_anonymous_profile(request) -> UserProfile:
    """Build a UserProfile for anonymous users, applying cookie-stored preferences."""
    profile = UserProfile()
    profile.enable_favicons = True

    domain_view_mode = request.COOKIES.get(
        PREF_COOKIE_DOMAIN_VIEW_MODE, UserProfile.DOMAIN_VIEW_ICON
    )
    if domain_view_mode in dict(UserProfile.DOMAIN_VIEW_CHOICES):
        profile.domain_view_mode = domain_view_mode

    compact_val = request.COOKIES.get(PREF_COOKIE_DOMAIN_COMPACT_MODE, "1")
    profile.domain_compact_mode = compact_val == "1"

    tag_grouping = request.COOKIES.get(
        PREF_COOKIE_TAG_GROUPING, UserProfile.TAG_GROUPING_ALPHABETICAL
    )
    if tag_grouping in dict(UserProfile.TAG_GROUPING_CHOICES):
        profile.tag_grouping = tag_grouping

    return profile


class LinkdingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # add global settings to request
        try:
            global_settings = GlobalSettings.get()
        except Exception:
            global_settings = default_global_settings
        request.global_settings = global_settings

        # add user profile to request
        if request.user.is_authenticated:
            request.user_profile = request.user.profile
        else:
            if global_settings.guest_profile_user:
                request.user_profile = global_settings.guest_profile_user.profile
            else:
                request.user_profile = _build_anonymous_profile(request)

        response = self.get_response(request)

        return response
