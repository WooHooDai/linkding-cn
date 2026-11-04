import urllib.parse
from collections import OrderedDict
from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count as DbCount, Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from bookmarks import queries, utils
from bookmarks.models import Annotation, UserProfile
from bookmarks.type_defs import HttpRequest
from bookmarks.views import turbo
from bookmarks.views.contexts import (
    HighlightDomainsContext,
    HighlightTagCloudContext,
)
from bookmarks.views.sidebar_content import (
    HIGHLIGHTS_SIDEBAR_MODULE_TEMPLATES,
    SIDEBAR_SCOPE_HIGHLIGHTS,
    sidebar_module_state,
)

PAGE_SIZE = 50  # fallback, actual value from user_profile.highlights_per_page

SORT_CHOICES = [
    ("date_created", _("Created ↓")),
    ("date_created_asc", _("Created ↑")),
    ("date_modified", _("Modified ↓")),
    ("date_modified_asc", _("Modified ↑")),
    ("random", _("Random")),
]

GROUP_CHOICES = [
    ("none", _("None")),
    ("bookmark", _("Bookmark")),
    ("domain", _("Domain")),
    ("color", _("Color")),
]

NOTE_FILTER_CHOICES = [
    ("", _("Any")),
    ("yes", _("Has")),
    ("no", _("None")),
]

DATE_FILTER_CHOICES = [
    ("", _("Any")),
    ("bookmark_added", _("Bookmark added")),
    ("bookmark_modified", _("Bookmark modified")),
    ("highlight_created", _("Highlight created")),
    ("highlight_modified", _("Highlight modified")),
]

COLOR_CSS_SOLID = {
    "yellow": "rgba(255,235,0,0.7)",
    "green": "rgba(0,200,83,0.7)",
    "blue": "rgba(66,165,245,0.7)",
    "pink": "rgba(236,64,122,0.7)",
    "primary": "var(--primary-color)",
}
COLOR_LABELS = dict(Annotation.COLOR_CHOICES)
# Sort tiebreak order for colors with equal count
COLOR_PRIORITY = {"yellow": 0, "green": 1, "blue": 2, "pink": 3, "primary": 4}

# Sidebar module templates for highlights page



class HighlightSearch:
    """Encapsulates highlight search/filter state, similar to BookmarkSearch."""

    # All filter parameters
    params = [
        "q",
        "sort",
        "group_by",
        "note_filter",
        "colors",
        "date_filter_by",
        "date_filter_start",
        "date_filter_end",
        "bookmark_id",
    ]

    # Parameters that can be saved as preferences
    preferences = ["sort", "group_by", "note_filter", "colors", "date_filter_by"]

    # System defaults
    defaults = {
        "q": "",
        "sort": "date_created",
        "group_by": "none",
        "note_filter": "",
        "colors": "",
        "date_filter_by": "",
        "date_filter_start": "",
        "date_filter_end": "",
        "bookmark_id": "",
    }

    # Parameters NOT persisted to preferences (transient)
    transient_params = ["q", "date_filter_start", "date_filter_end", "bookmark_id", "page"]

    def __init__(self, **kwargs):
        self._values = {}
        for param in self.params:
            value = kwargs.get(param, self.defaults[param])
            # Normalize: strip whitespace for strings
            if isinstance(value, str):
                value = value.strip()
            self._values[param] = value

        # Parse page separately (not a filter param, but needed for pagination)
        self.page = kwargs.get("page", "1")
        if isinstance(self.page, str):
            self.page = self.page.strip()

        # Parse colors list from comma-separated string
        # "all" is a special value meaning "no color filter" (show all)
        colors_raw = self._values["colors"]
        if not colors_raw or colors_raw == "all":
            self.colors_list = []
        else:
            self.colors_list = [c.strip() for c in colors_raw.split(",") if c.strip()]

    def __repr__(self):
        return f"<HighlightSearch {self.query_params}>"

    def __getattr__(self, name):
        # Prevent infinite recursion if _values is not yet initialized
        if name.startswith("_") or "_values" not in self.__dict__:
            raise AttributeError(name)
        if name in self._values:
            return self._values[name]
        raise AttributeError(f"'{self.__class__.__name__}' has no attribute '{name}'")

    @classmethod
    def from_request(cls, request, preferences=None):
        """Create HighlightSearch from HTTP request, merging with saved preferences.

        Priority: URL params > saved preferences > system defaults.
        Transient params (q, date range, bookmark_id, page) are always read from URL.
        """
        if not preferences:
            preferences = {}

        initial = {}

        # For preference keys: URL param takes precedence over saved preference
        for key in cls.preferences:
            if key in request.GET:
                initial[key] = request.GET[key]
            elif key in preferences:
                initial[key] = preferences[key]

        # Transient params: always from URL (no preference fallback)
        for key in cls.transient_params:
            if key != "page":  # page handled separately
                initial[key] = request.GET.get(key, "")

        # Page
        initial["page"] = request.GET.get("page", "1")

        return cls(**initial)

    @classmethod
    def from_post(cls, request):
        """Create HighlightSearch from POST data (for save/apply actions)."""
        kwargs = {}
        for param in cls.params:
            if param == "colors":
                kwargs["colors"] = ",".join(request.POST.getlist("colors"))
            else:
                kwargs[param] = request.POST.get(param, "")
        kwargs["page"] = request.POST.get("page", "1")
        return cls(**kwargs)

    def is_modified(self, param, reference=None):
        """Check if a parameter differs from its default (or a custom reference).

        Args:
            param: Parameter name to check.
            reference: Dict to compare against. If None, uses system defaults.
                       If a key is missing from reference, falls back to system default.

        Returns:
            True if the parameter's current value differs from the reference value.
        """
        if reference is None:
            reference = self.defaults
        value = self._values.get(param)
        ref_value = reference.get(param, self.defaults.get(param, ""))

        # Special handling for colors: compare as comma-joined string
        if param == "colors":
            return ",".join(self.colors_list) != ref_value

        return value != ref_value

    @property
    def has_modifications(self):
        """Check if any filter differs from system defaults."""
        return any(self.is_modified(p) for p in self.params)

    def has_modifications_vs_prefs(self, preferences):
        """Check if any filter differs from saved preferences."""
        if not preferences:
            return self.has_modifications
        for param in self.params:
            if param in self.transient_params:
                # Transient params: modified if non-empty
                value = self._values[param]
                if value:
                    return True
            else:
                if self.is_modified(param, reference=preferences):
                    return True
        return False

    @property
    def query_params(self):
        """Build query params dict (only non-default values) for URL generation."""
        params = {}
        for param in self.params:
            value = self._values[param]
            if param == "colors":
                if value and value != "all":
                    params["colors"] = value
            elif param == "q":
                if value:
                    params["q"] = value
            elif value and value != self.defaults[param]:
                params[param] = value
        return params

    @property
    def query_string(self):
        """Return URL-encoded query string of non-default params."""
        return urllib.parse.urlencode(self.query_params)

    @property
    def preferences_dict(self):
        """Extract preference dict for saving to user profile."""
        return {key: self._values[key] for key in self.preferences}

    @property
    def sort_orm(self):
        """Convert sort value to ORM ordering."""
        return {
            "date_created": "-date_created",
            "date_created_asc": "date_created",
            "date_modified": "-date_modified",
            "date_modified_asc": "date_modified",
            "random": "random",
        }.get(self._values["sort"], "-date_created")

    @property
    def bookmark_id_int(self):
        """Parse bookmark_id to int, return None if invalid."""
        if self._values["bookmark_id"]:
            try:
                return int(self._values["bookmark_id"])
            except ValueError:
                pass
        return None


def _group_annotations(annotations, group_by):
    if group_by == "none":
        return [("", None, [], annotations)]
    groups = OrderedDict()
    for ann in annotations:
        if group_by == "bookmark":
            key = ann.bookmark_id
            label = ann.bookmark.resolved_title
        elif group_by == "domain":
            parsed = urlparse(ann.bookmark.url)
            key = parsed.netloc or ann.bookmark.url
            label = key
        elif group_by == "color":
            key = ann.color
            label = COLOR_LABELS.get(ann.color, ann.color)
        else:
            key = "__all__"
            label = ""
        if key not in groups:
            groups[key] = {
                "label": label,
                "color_key": ann.color if group_by == "color" else None,
                "bookmark_id": ann.bookmark_id if group_by == "bookmark" else None,
                "annotations": [],
            }
        groups[key]["annotations"].append(ann)
    return [(g["label"], g.get("color_key"), g.get("bookmark_id"), g["annotations"]) for g in groups.values()]


def _build_color_filter_urls(request, search):
    """基于当前 URL 为每个颜色预算 toggle URL（参照标签云 AddTagItem 模式）。"""
    base = reverse("linkding:bookmarks.highlights")
    params = request.GET.copy()
    params.pop("page", None)  # 切换颜色时重置分页

    urls = {}

    # "All" — 不含 colors 参数（默认状态）
    all_params = params.copy()
    all_params.pop("colors", None)
    qs = utils.clean_query_params(all_params)
    urls["all"] = f"{base}?{qs}" if qs else base

    # 各颜色 — toggle
    current = set(search.colors_list)
    for color_key, _ in Annotation.COLOR_CHOICES:
        p = params.copy()
        if color_key in current:
            next_colors = current - {color_key}
        else:
            next_colors = current | {color_key}
        if next_colors:
            p["colors"] = ",".join(sorted(next_colors))
        else:
            p.pop("colors", None)
        urls[color_key] = f"{base}?{utils.clean_query_params(p)}"

    return urls


_HIGHLIGHTS_MODULE_WRAPPER_IDS = {
    UserProfile.SIDEBAR_MODULE_COLORS: "hl-colors-container",
    UserProfile.SIDEBAR_MODULE_DOMAINS: "hl-domains-container",
    UserProfile.SIDEBAR_MODULE_TAGS: "hl-tags-container",
}


def _build_highlights_sidebar_modules(request: HttpRequest, context: dict) -> list[dict]:
    """Build sidebar modules for the highlights page using independent settings."""
    available = {
        UserProfile.SIDEBAR_MODULE_COLORS: True,
        UserProfile.SIDEBAR_MODULE_DOMAINS: context.get("domains") is not None,
        UserProfile.SIDEBAR_MODULE_TAGS: context.get("tag_cloud") is not None,
    }

    modules = []
    for item in request.user_profile.get_highlights_sidebar_modules():
        key = item["key"]
        if not item["enabled"] or not available.get(key):
            continue
        modules.append({
            "key": key,
            "template_name": HIGHLIGHTS_SIDEBAR_MODULE_TEMPLATES[key],
            "wrapper_id": _HIGHLIGHTS_MODULE_WRAPPER_IDS.get(key),
        })

    return modules


@login_required
def index(request: HttpRequest):
    if request.method == "POST":
        # Preference toggle actions (sidebar module settings)
        if "pref_action" in request.POST:
            return _handle_preference_toggle(request)
        # Filter form actions (save / apply)
        if "save" in request.POST:
            return _handle_save(request)
        if "apply" in request.POST:
            return _handle_apply(request)
        # Other actions (delete, update_note, etc.)
        return _handle_action(request)

    # GET: merge saved preferences with URL params (URL params take precedence)
    prefs = request.user_profile.highlights_search_preferences or {}
    search = HighlightSearch.from_request(request, preferences=prefs)

    page_size = getattr(request.user_profile, "highlights_per_page", PAGE_SIZE) or PAGE_SIZE
    return _render_list(request, search, prefs, page_size)


def _render_list(request, search: HighlightSearch, prefs=None, page_size=PAGE_SIZE):
    annotations = queries.query_annotations(
        user=request.user,
        search_q=search.q,
        colors=search.colors_list or None,
        note_filter=search.note_filter,
        sort=search.sort_orm,
        group_by=search.group_by,
        date_filter_by=search.date_filter_by,
        date_filter_start=search.date_filter_start,
        date_filter_end=search.date_filter_end,
        bookmark_id=search.bookmark_id_int,
    )

    paginator = Paginator(annotations, page_size)
    page = paginator.get_page(search.page)
    groups = _group_annotations(page.object_list, search.group_by)

    summary = annotations.aggregate(
        total_annotations=DbCount("id"),
        total_notes=DbCount("id", filter=Q(note_content__gt="")),
        total_bookmarks=DbCount("bookmark", distinct=True),
    )

    # Color stats: current list counts WITHOUT color filter
    # with_related=False avoids JOINs that break values().annotate() grouping
    color_stats_qs = queries.query_annotations(
        user=request.user,
        search_q=search.q,
        note_filter=search.note_filter,
        bookmark_id=search.bookmark_id_int,
        date_filter_by=search.date_filter_by,
        date_filter_start=search.date_filter_start,
        date_filter_end=search.date_filter_end,
        sort="",  # 纯聚合查询，不需要排序
        # colors intentionally omitted
        with_related=False,
    )
    color_count_map = {
        item["color"]: item["count"]
        for item in color_stats_qs.values("color").annotate(count=DbCount("id"))
    }
    color_total = sum(color_count_map.values())

    # Sort by count descending; tiebreak: yellow, green, blue, pink, primary
    color_stats = []
    for color_key, color_label in Annotation.COLOR_CHOICES:
        color_stats.append({
            "key": color_key,
            "label": color_label,
            "count": color_count_map.get(color_key, 0),
            "css_color": COLOR_CSS_SOLID.get(color_key, "var(--primary-color)"),
            "selected": color_key in search.colors_list,
        })
    color_stats.sort(key=lambda s: (-s["count"], COLOR_PRIORITY.get(s["key"], 99)))

    # Build filter URLs for each color (sidebar pure-link navigation)
    color_filter_urls = _build_color_filter_urls(request, search)
    for stat in color_stats:
        stat["url"] = color_filter_urls.get(stat["key"], "#")

    # Build sidebar contexts (filtered by current search)
    domains = HighlightDomainsContext(request, search)
    tag_cloud = HighlightTagCloudContext(request, search)

    context = {
        "page_title": _("Highlights & Annotations - Linkding"),
        "annotations_page": page,
        "groups": groups,
        "search_q": search.q,
        "colors": search.colors_list,
        "colors_raw": ",".join(search.colors_list),
        "note_filter": search.note_filter,
        "sort": search.sort,
        "group_by": search.group_by,
        "date_filter_by": search.date_filter_by,
        "date_filter_start": search.date_filter_start,
        "date_filter_end": search.date_filter_end,
        "bookmark_id": search.bookmark_id,
        "summary": summary,
        "color_stats": color_stats,
        "color_total": color_total,
        "color_all_url": color_filter_urls["all"],
        "sort_choices": SORT_CHOICES,
        "group_choices": GROUP_CHOICES,
        "note_filter_choices": NOTE_FILTER_CHOICES,
        "date_filter_choices": DATE_FILTER_CHOICES,
        "color_choices": Annotation.COLOR_CHOICES,
        "color_css_solid": COLOR_CSS_SOLID,
        "color_labels": COLOR_LABELS,
        "query_string": search.query_string,
        "has_modified_filters": search.has_modifications_vs_prefs(prefs),
        # Sidebar
        "domains": domains,
        "tag_cloud": tag_cloud,
        "show_sidebar": request.user_profile.show_highlights_sidebar,
        "sticky_header_controls": request.user_profile.highlights_sticky_header_controls,
        "sticky_side_panel": request.user_profile.highlights_sticky_side_panel,
        "sticky_pagination": request.user_profile.highlights_sticky_pagination,
        "highlight_copy_format": request.user_profile.highlight_copy_format,
        "hl_copy_item_format": (request.user_profile.highlight_copy_format or {}).get("item_format", ""),
        "hl_copy_separator": (request.user_profile.highlight_copy_format or {}).get("separator", ""),
        "highlight_copy_default_action": request.user_profile.highlight_copy_default_action,
    }
    context["sidebar_modules"] = _build_highlights_sidebar_modules(request, context)
    context["sidebar_module_state"] = sidebar_module_state(
        request.user_profile, SIDEBAR_SCOPE_HIGHLIGHTS
    )
    return render(request, "bookmarks/highlights/index.html", context)


def _handle_save(request):
    """Save current filter preferences to user profile, then redirect to base URL."""
    search = HighlightSearch.from_post(request)
    request.user_profile.highlights_search_preferences = search.preferences_dict
    request.user_profile.save()
    return HttpResponseRedirect(reverse("linkding:bookmarks.highlights"))


def _handle_preference_toggle(request: HttpRequest):
    """Handle sidebar preference toggle actions (highlight-specific).

    Returns a Turbo Stream response that updates only the affected sidebar module,
    avoiding a full page reload (and drawer flicker).
    """
    action = request.POST.get("pref_action", "")
    profile = request.user_profile

    field = None
    if action == "hl_toggle_domain_view_mode":
        profile.highlights_domain_view_mode = request.POST["value"]
        field = "highlights_domain_view_mode"
    elif action == "hl_toggle_domain_compact_mode":
        profile.highlights_domain_compact_mode = request.POST["value"] == "1"
        field = "highlights_domain_compact_mode"
    elif action == "hl_toggle_tag_grouping":
        profile.highlights_tag_grouping = request.POST["value"]
        field = "highlights_tag_grouping"
    else:
        return HttpResponseRedirect(reverse("linkding:bookmarks.highlights"))

    profile.save(update_fields=[field])

    # Rebuild search from current GET params (preserves filters)
    search = HighlightSearch.from_request(
        request, preferences=profile.highlights_search_preferences or {}
    )

    # Return Turbo Stream updating only the affected module
    if action in ("hl_toggle_domain_view_mode", "hl_toggle_domain_compact_mode"):
        domains = HighlightDomainsContext(request, search)
        return turbo.update(
            request,
            "hl-domains-container",
            HIGHLIGHTS_SIDEBAR_MODULE_TEMPLATES[UserProfile.SIDEBAR_MODULE_DOMAINS],
            {"domains": domains},
        )
    elif action == "hl_toggle_tag_grouping":
        tag_cloud = HighlightTagCloudContext(request, search)
        return turbo.update(
            request,
            "hl-tags-container",
            HIGHLIGHTS_SIDEBAR_MODULE_TEMPLATES[UserProfile.SIDEBAR_MODULE_TAGS],
            {"tag_cloud": tag_cloud},
        )


def _handle_apply(request):
    """Apply filters: redirect to URL with only non-default params."""
    search = HighlightSearch.from_post(request)
    base_url = reverse("linkding:bookmarks.highlights")
    query_string = search.query_string
    return HttpResponseRedirect(base_url + ("?" + query_string if query_string else ""))


def _handle_action(request: HttpRequest):
    action = request.POST.get("action", "")
    if action == "delete":
        return _action_delete_single(request)
    if action == "update_note":
        return _action_update_note(request)
    if action == "change_color":
        return _action_change_color_single(request)
    if action == "bulk_execute":
        return _action_bulk(request)
    return _redirect_back(request)


def _action_delete_single(request):
    ann_id = request.POST.get("annotation_id")
    ann = Annotation.objects.filter(pk=ann_id, bookmark__owner=request.user).first()
    if ann:
        ann.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    return _redirect_back(request)


def _action_update_note(request):
    ann_id = request.POST.get("annotation_id")
    note = request.POST.get("note_content", "")
    ann = Annotation.objects.filter(pk=ann_id, bookmark__owner=request.user).first()
    if ann:
        ann.note_content = note
        ann.save(update_fields=["note_content", "date_modified"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    return _redirect_back(request)


def _action_change_color_single(request):
    ann_id = request.POST.get("annotation_id")
    new_color = request.POST.get("new_color")
    ann = Annotation.objects.filter(pk=ann_id, bookmark__owner=request.user).first()
    if ann and new_color in dict(Annotation.COLOR_CHOICES):
        ann.color = new_color
        ann.save(update_fields=["color", "date_modified"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    return _redirect_back(request)


def _action_bulk(request):
    bulk_action = request.POST.get("bulk_action", "")
    annotation_ids = request.POST.getlist("annotation_id")
    # Cross-page IDs from sessionStorage
    session_ids = request.POST.getlist("bulk_session_ids")
    if session_ids:
        annotation_ids = list(set(annotation_ids) | set(session_ids))
    select_across = request.POST.get("bulk_select_across") == "on"

    if select_across:
        search = HighlightSearch.from_post(request)
        qs = queries.query_annotations(
            user=request.user,
            search_q=search.q,
            colors=search.colors_list or None,
            note_filter=search.note_filter,
            sort=search.sort_orm,
            group_by="none",
            date_filter_by=search.date_filter_by,
            date_filter_start=search.date_filter_start,
            date_filter_end=search.date_filter_end,
        )
        annotation_ids = list(qs.values_list("id", flat=True))

    if not annotation_ids:
        return _redirect_back(request)

    annotations = Annotation.objects.filter(
        pk__in=annotation_ids, bookmark__owner=request.user
    )

    if bulk_action == "bulk_delete":
        annotations.delete()
    elif bulk_action == "bulk_clear_note":
        annotations.update(note_content="")
    elif bulk_action == "bulk_change_color":
        new_color = request.POST.get("bulk_new_color", "")
        if new_color in dict(Annotation.COLOR_CHOICES):
            annotations.update(color=new_color)

    return _redirect_back(request)


def _redirect_back(request):
    return_url = request.POST.get("return_url", reverse("linkding:bookmarks.highlights"))
    return HttpResponseRedirect(return_url)
