import logging
import time
from functools import lru_cache
from http import HTTPStatus
from pathlib import Path

import requests
from django import forms as django_forms
from django.conf import settings as django_settings
from django.conf.locale import LANG_INFO
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import prefetch_related_objects
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse, translate_url
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.i18n import LANGUAGE_QUERY_PARAMETER

from bookmarks.models import (
    ApiToken,
    Bookmark,
    FeedToken,
    GlobalSettings,
    GlobalSettingsForm,
    UserProfile,
    UserProfileAutoTaggingRulesForm,
    UserProfileCustomCssForm,
    UserProfileCustomDomainRootForm,
    UserProfileQuickSettingsForm,
)
from bookmarks.services import exporter, importer, tasks
from bookmarks.type_defs import HttpRequest
from bookmarks.utils import app_version
from bookmarks.views import access
from bookmarks.views import turbo

logger = logging.getLogger(__name__)
LANGUAGE_OTHER_SENTINEL = "__other__"
LANGUAGE_NONE_SENTINEL = "__none__"


def update_language(request: HttpRequest):
    next_url = request.POST.get("next", request.GET.get("next"))
    if (
        next_url or request.accepts("text/html")
    ) and not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = request.META.get("HTTP_REFERER")
        if not url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = "/"

    response = HttpResponseRedirect(next_url) if next_url else HttpResponse(status=204)
    if request.method != "POST":
        return response

    lang_code = request.POST.get(LANGUAGE_QUERY_PARAMETER)
    if not lang_code:
        return response

    supported_languages = _get_supported_interface_languages()
    lang_code = supported_languages.get(_normalize_language_code(lang_code))
    if not lang_code:
        return response

    if request.user.is_authenticated:
        profile = request.user.profile
        if profile.language != lang_code:
            profile.language = lang_code
            profile.save(update_fields=["language"])

    if next_url:
        next_translated = translate_url(next_url, lang_code)
        if next_translated != next_url:
            response = HttpResponseRedirect(next_translated)

    response.set_cookie(
        django_settings.LANGUAGE_COOKIE_NAME,
        lang_code,
        max_age=django_settings.LANGUAGE_COOKIE_AGE,
        path=django_settings.LANGUAGE_COOKIE_PATH,
        domain=django_settings.LANGUAGE_COOKIE_DOMAIN,
        secure=django_settings.LANGUAGE_COOKIE_SECURE,
        httponly=django_settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=django_settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response


@login_required
def general(request: HttpRequest, status=200, context_overrides=None):
    enable_refresh_favicons = django_settings.LD_ENABLE_REFRESH_FAVICONS
    has_snapshot_support = django_settings.LD_ENABLE_SNAPSHOTS
    success_message = _find_message_with_tag(
        messages.get_messages(request), "settings_success_message"
    )
    error_message = _find_message_with_tag(
        messages.get_messages(request), "settings_error_message"
    )
    version_info = get_version_info(get_ttl_hash())

    profile_quick_form = UserProfileQuickSettingsForm(instance=request.user_profile)
    custom_css_form = UserProfileCustomCssForm(instance=request.user_profile)
    auto_tagging_rules_form = UserProfileAutoTaggingRulesForm(
        instance=request.user_profile
    )
    custom_domain_root_form = UserProfileCustomDomainRootForm(
        instance=request.user_profile
    )
    global_settings_form = None
    if request.user.is_superuser:
        global_settings_form = GlobalSettingsForm(instance=GlobalSettings.get())

    if context_overrides is None:
        context_overrides = {}

    primary_language_choices = _get_primary_language_choices()
    other_language_choices = _get_other_language_choices()
    current_language = _normalize_language_code(
        request.user_profile.language or django_settings.LANGUAGE_CODE
    )
    primary_language_codes = {
        _normalize_language_code(code) for code, _label in primary_language_choices
    }
    other_language_codes = {
        _normalize_language_code(code) for code, _label in other_language_choices
    }
    language_segment_value = (
        current_language
        if current_language in primary_language_codes
        else LANGUAGE_OTHER_SENTINEL
    )

    return render(
        request,
        "settings/general.html",
        {
            "profile_quick_form": profile_quick_form,
            "custom_css_form": custom_css_form,
            "auto_tagging_rules_form": auto_tagging_rules_form,
            "custom_domain_root_form": custom_domain_root_form,
            "global_settings_form": global_settings_form,
            "primary_language_choices": primary_language_choices,
            "other_language_choices": other_language_choices,
            "language_segment_value": language_segment_value,
            "language_other_sentinel": LANGUAGE_OTHER_SENTINEL,
            "language_other_current_code": (
                current_language if current_language in other_language_codes else ""
            ),
            "language_none_sentinel": LANGUAGE_NONE_SENTINEL,
            "settings_general_url": reverse("linkding:settings.general"),
            "enable_refresh_favicons": enable_refresh_favicons,
            "has_snapshot_support": has_snapshot_support,
            "success_message": success_message,
            "error_message": error_message,
            "version_info": version_info,
            **context_overrides,
        },
        status=status,
    )


@login_required
def update(request: HttpRequest):
    if request.method == "POST":
        if "refresh_favicons" in request.POST:
            tasks.schedule_refresh_favicons(request.user)
            messages.success(
                request,
                _("Scheduled favicon update. This may take a while..."),
                "settings_success_message",
            )
        if "create_missing_html_snapshots" in request.POST:
            count = tasks.create_missing_html_snapshots(request.user)
            if count > 0:
                messages.success(
                    request,
                    ngettext(
                        "Queued %(count)s missing snapshot. This may take a while...",
                        "Queued %(count)s missing snapshots. This may take a while...",
                        count,
                    )
                    % {"count": count},
                    "settings_success_message",
                )
            else:
                messages.success(
                    request,
                    _("No missing snapshots found."),
                    "settings_success_message",
                )

    return HttpResponseRedirect(reverse("linkding:settings.general"))


def _schedule_profile_side_effects(
    request: HttpRequest,
    profile_before: dict,
    profile_after,
):
    if profile_after.enable_favicons and not profile_before["enable_favicons"]:
        tasks.schedule_bookmarks_without_favicons(request.user)

    if (
        profile_after.enable_preview_images
        and not profile_before["enable_preview_images"]
    ):
        tasks.schedule_bookmarks_without_previews(request.user)


def _json_form_error_response(form):
    return JsonResponse(
        {
            "status": "error",
            "errors": form.errors.get_json_data(escape_html=True),
        },
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
    )


def _prefers_json_response(request: HttpRequest) -> bool:
    accept_header = request.headers.get("Accept", "")
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or (
        "application/json" in accept_header and "text/html" not in accept_header
    )


def _form_context_key(form_id: str) -> str | None:
    return {
        "profile_quick": "profile_quick_form",
        "profile_custom_css": "custom_css_form",
        "profile_auto_tagging_rules": "auto_tagging_rules_form",
        "profile_custom_domain_root": "custom_domain_root_form",
        "global_quick": "global_settings_form",
    }.get(form_id)


def _build_form_error_response(
    request: HttpRequest, form_id: str, form
) -> JsonResponse | HttpResponse:
    if _prefers_json_response(request):
        return _json_form_error_response(form)

    context_key = _form_context_key(form_id)
    context_overrides = {
        "error_message": _("Settings update failed, check the form below for errors"),
        "error_details": [
            message for field_errors in form.errors.values() for message in field_errors
        ],
    }
    if context_key:
        context_overrides[context_key] = form
    return general(request, 422, context_overrides)


def _build_form_success_response(
    request: HttpRequest, form_id: str
) -> JsonResponse | HttpResponseRedirect:
    if _prefers_json_response(request):
        return JsonResponse({"status": "ok"})

    success_message = (
        _("Global settings updated")
        if form_id == "global_quick"
        else _("Profile updated")
    )
    messages.success(request, success_message, "settings_success_message")
    return HttpResponseRedirect(reverse("linkding:settings.general"))


def _parse_form_fields(raw_value: str | None) -> set[str]:
    if not raw_value:
        return set()
    return {
        field_name.strip() for field_name in raw_value.split(",") if field_name.strip()
    }


def _build_profile_quick_form_data(profile, request_post) -> dict:
    form = UserProfileQuickSettingsForm(instance=profile)
    submitted_fields = _parse_form_fields(request_post.get("form_fields"))
    if not submitted_fields:
        return request_post

    form_data = {}
    for field_name, field in form.fields.items():
        if field_name in submitted_fields:
            if isinstance(field, django_forms.BooleanField):
                if field_name in request_post:
                    form_data[field_name] = request_post.get(field_name)
            else:
                if field_name in request_post:
                    form_data[field_name] = request_post.get(field_name)
                else:
                    value = form[field_name].value()
                    if value not in (None, ""):
                        form_data[field_name] = value
            continue

        value = form[field_name].value()
        if isinstance(field, django_forms.BooleanField):
            if value:
                form_data[field_name] = "on"
        elif value not in (None, ""):
            form_data[field_name] = value

    return form_data


@login_required
def save(request: HttpRequest):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    form_id = request.POST.get("form_id")
    profile = request.user.profile
    profile_before = {
        "enable_favicons": profile.enable_favicons,
        "enable_preview_images": profile.enable_preview_images,
    }

    if form_id == "profile_quick":
        form = UserProfileQuickSettingsForm(
            _build_profile_quick_form_data(profile, request.POST),
            instance=profile,
        )
    elif form_id == "profile_custom_css":
        form = UserProfileCustomCssForm(request.POST, instance=profile)
    elif form_id == "profile_auto_tagging_rules":
        form = UserProfileAutoTaggingRulesForm(request.POST, instance=profile)
    elif form_id == "profile_custom_domain_root":
        form = UserProfileCustomDomainRootForm(request.POST, instance=profile)
    elif form_id == "global_quick":
        if not request.user.is_superuser:
            raise PermissionDenied()
        form = GlobalSettingsForm(request.POST, instance=GlobalSettings.get())
    else:
        return JsonResponse(
            {"status": "error", "message": "Unknown form"},
            status=HTTPStatus.BAD_REQUEST,
        )

    if not form.is_valid():
        return _build_form_error_response(request, form_id, form)

    saved_instance = form.save()
    if form_id == "profile_quick":
        _schedule_profile_side_effects(request, profile_before, saved_instance)

    return _build_form_success_response(request, form_id)


# Cache API call response, for one hour when using get_ttl_hash with default params
@lru_cache(maxsize=1)
def get_version_info(ttl_hash=None):
    latest_version = None
    try:
        latest_version_url = (
            "https://api.github.com/repos/WooHooDai/linkding-cn/releases/latest"
        )
        response = requests.get(latest_version_url, timeout=5)
        json = response.json()
        if response.status_code == 200 and "name" in json:
            latest_version = json["name"][1:]
    except requests.exceptions.RequestException:
        pass

    latest_version_info = ""
    if latest_version == app_version:
        latest_version_info = " (latest)"
    elif latest_version is not None:
        latest_version_info = f" (latest: {latest_version})"

    return f"{app_version}{latest_version_info}"


def get_ttl_hash(seconds=3600):
    """Return the same value within `seconds` time period"""
    return round(time.time() / seconds)


@login_required
def integrations(request):
    application_url = request.build_absolute_uri(reverse("linkding:bookmarks.new"))
    api_tokens = ApiToken.objects.filter(user=request.user).order_by("-created")
    api_token_key = request.session.pop("api_token_key", None)
    api_token_name = request.session.pop("api_token_name", None)
    api_success_message = _find_message_with_tag(
        messages.get_messages(request), "api_success_message"
    )
    feed_token = FeedToken.objects.get_or_create(user=request.user)[0]
    all_feed_url = reverse("linkding:feeds.all", args=[feed_token.key])
    unread_feed_url = reverse("linkding:feeds.unread", args=[feed_token.key])
    shared_feed_url = reverse("linkding:feeds.shared", args=[feed_token.key])
    public_shared_feed_url = reverse("linkding:feeds.public_shared")
    return render(
        request,
        "settings/integrations.html",
        {
            "application_url": application_url,
            "api_tokens": api_tokens,
            "api_token_key": api_token_key,
            "api_token_name": api_token_name,
            "api_success_message": api_success_message,
            "all_feed_url": all_feed_url,
            "unread_feed_url": unread_feed_url,
            "shared_feed_url": shared_feed_url,
            "public_shared_feed_url": public_shared_feed_url,
        },
    )


@login_required
def create_api_token(request: HttpRequest):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            name = "API Token"

        token = ApiToken(user=request.user, name=name)
        token.save()

        request.session["api_token_key"] = token.key
        request.session["api_token_name"] = token.name

        messages.success(
            request,
            _('API token "%(token_name)s" created successfully')
            % {"token_name": token.name},
            "api_success_message",
        )

        if turbo.accept(request) and turbo.is_frame(request, "api-modal"):
            integration_context = {
                "api_tokens": ApiToken.objects.filter(user=request.user).order_by(
                    "-created"
                ),
                "api_token_key": token.key,
                "api_token_name": token.name,
                "api_success_message": _find_message_with_tag(
                    messages.get_messages(request), "api_success_message"
                ),
            }
            api_section_stream = turbo.replace(
                request,
                "api-section",
                "settings/integrations_api_section.html",
                integration_context,
                method="morph",
            )
            api_modal_stream = turbo.replace(
                request,
                "api-modal",
                "settings/empty_modal_frame.html",
                {},
                method="morph",
            )
            return HttpResponse(
                api_section_stream.content.decode() + api_modal_stream.content.decode(),
                content_type="text/vnd.turbo-stream.html",
            )

        return HttpResponseRedirect(reverse("linkding:settings.integrations"))

    if turbo.is_frame(request, "api-modal") and request.GET.get("close") == "1":
        return render(request, "settings/empty_modal_frame.html")

    return render(request, "settings/create_api_token_modal.html")


@login_required
def delete_api_token(request: HttpRequest):
    if request.method == "POST":
        token_id = request.POST.get("token_id")
        token = access.api_token_write(request, token_id)
        token_name = token.name
        token.delete()
        messages.success(
            request,
            _('API token "%(token_name)s" has been deleted.')
            % {"token_name": token_name},
            "api_success_message",
        )

    return HttpResponseRedirect(reverse("linkding:settings.integrations"))


@login_required
def bookmark_import(request: HttpRequest):
    import_file = request.FILES.get("import_file")
    import_options = importer.ImportOptions(
        map_private_flag=request.POST.get("map_private_flag") == "on"
    )

    if import_file is None:
        messages.error(
            request, _("Please select a file to import."), "settings_error_message"
        )
        return HttpResponseRedirect(reverse("linkding:settings.general"))

    try:
        content = import_file.read().decode()
        result = importer.import_netscape_html(content, request.user, import_options)
        success_msg = ngettext(
            "%(count)s bookmark was imported successfully.",
            "%(count)s bookmarks were imported successfully.",
            result.success,
        ) % {"count": result.success}
        messages.success(request, success_msg, "settings_success_message")
        if result.failed > 0:
            err_msg = ngettext(
                "%(count)s bookmark could not be imported. Please check the logs for more details.",
                "%(count)s bookmarks could not be imported. Please check the logs for more details.",
                result.failed,
            ) % {"count": result.failed}
            messages.error(request, err_msg, "settings_error_message")
    except Exception:
        logging.exception("Unexpected error during bookmark import")
        messages.error(
            request,
            _("An error occurred during bookmark import."),
            "settings_error_message",
        )

    return HttpResponseRedirect(reverse("linkding:settings.general"))


@login_required
def bookmark_export(request: HttpRequest):
    # noinspection PyBroadException
    try:
        bookmarks = Bookmark.objects.filter(owner=request.user)
        # Prefetch tags to prevent n+1 queries
        prefetch_related_objects(bookmarks, "tags")
        file_content = exporter.export_netscape_html(list(bookmarks))

        # Generate filename with current date and time
        current_time = timezone.now()
        filename = current_time.strftime("bookmarks_%Y-%m-%d_%H-%M-%S.html")

        response = HttpResponse(content_type="text/plain; charset=UTF-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write(file_content)

        return response
    except Exception:
        return general(
            request,
            context_overrides={
                "export_error": _("An error occurred during bookmark export.")
            },
        )


def _find_message_with_tag(messages, tag):
    for message in messages:
        if message.extra_tags == tag:
            return message
    return None


def _normalize_language_code(language_code: str) -> str:
    return language_code.replace("_", "-").lower()


def _get_primary_language_choices():
    return [
        (UserProfile.LANGUAGE_ZH_HANS, "简体中文"),
        (UserProfile.LANGUAGE_EN, "English"),
    ]


def _get_other_language_choices():
    primary_language_codes = {
        _normalize_language_code(code)
        for code, _label in _get_primary_language_choices()
    }
    discovered_languages = []
    seen_codes = set()

    for po_file in Path(django_settings.BASE_DIR).glob(
        "locale/*/LC_MESSAGES/django.po"
    ):
        language_code = _normalize_language_code(po_file.parent.parent.name)
        if language_code in primary_language_codes or language_code in seen_codes:
            continue

        seen_codes.add(language_code)
        discovered_languages.append(
            (
                language_code,
                _get_language_display_name(language_code, po_file.parent.parent.name),
            )
        )

    return sorted(discovered_languages, key=lambda item: item[1].casefold())


def _get_language_display_name(language_code: str, fallback: str) -> str:
    language_info = LANG_INFO.get(language_code) or LANG_INFO.get(
        language_code.split("-")[0]
    )
    if not language_info:
        return fallback

    return language_info.get("name_local") or language_info.get("name") or fallback


def _get_supported_interface_languages():
    return {
        _normalize_language_code(code): code
        for code, _label in (
            _get_primary_language_choices() + _get_other_language_choices()
        )
    }
