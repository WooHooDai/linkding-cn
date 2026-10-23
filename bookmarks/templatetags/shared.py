import re

import bleach
import markdown
from bleach.linkifier import DEFAULT_CALLBACKS, Linker
from bleach_allowlist import markdown_attrs, markdown_tags
from django import template
from django.utils.safestring import mark_safe

from bookmarks import queries, utils
from bookmarks.models import UserProfile
from bookmarks.widgets import FormCheckbox

register = template.Library()


@register.simple_tag(takes_context=True)
def update_query_string(context, **kwargs):
    query = context.request.GET.copy()

    # Replace query params with the ones from tag parameters
    for key in kwargs:
        query.__setitem__(key, kwargs[key])

    return query.urlencode()


@register.simple_tag(takes_context=True)
def add_tag_to_query(context, tag_name: str):
    params = context.request.GET.copy()

    query_string = params.get("q", "")
    parsed_query = queries.parse_query_string(query_string)
    selected_tags = parsed_query["tag_names"]

    # Lax tag search also treats unprefixed search terms as selected tags.
    if context.request.user_profile.tag_search == UserProfile.TAG_SEARCH_LAX:
        selected_tags = selected_tags + parsed_query["search_terms"]

    selected_tags = [selected_tag.lower() for selected_tag in selected_tags]
    if tag_name.lower() not in selected_tags:
        query_string = (query_string + " #" + tag_name).strip()

    params.setlist("q", [query_string])

    # Remove details ID and page number
    params.pop("details", None)
    params.pop("page", None)

    return params.urlencode()


@register.simple_tag(takes_context=True)
def add_author_to_query(context, author: str):
    params = context.request.GET.copy()

    # 替换已有的 author 条件（点击另一作者时切换而非叠加），保留其他查询
    query_string = params.get("q", "")
    query_string = queries.replace_field_terms(query_string, "author", [author])

    params.setlist("q", [query_string])

    # Remove details ID and page number
    params.pop("details", None)
    params.pop("page", None)

    return params.urlencode()


@register.simple_tag(takes_context=True)
def remove_tag_from_query(context, tag_name: str):
    params = context.request.GET.copy()
    if params.__contains__("q"):
        # Split query string into parts
        query_string = params.__getitem__("q")
        query_parts = query_string.split()
        # Remove tag with hash
        tag_name_with_hash = "#" + tag_name
        query_parts = [
            part
            for part in query_parts
            if str.lower(part) != str.lower(tag_name_with_hash)
        ]
        # When using lax tag search, also remove tag without hash
        profile = context.request.user_profile
        if profile.tag_search == UserProfile.TAG_SEARCH_LAX:
            query_parts = [
                part for part in query_parts if str.lower(part) != str.lower(tag_name)
            ]
        # Rebuild query string
        query_string = " ".join(query_parts)
        params.__setitem__("q", query_string)

    # Remove details ID and page number
    params.pop("details", None)
    params.pop("page", None)

    return params.urlencode()


@register.simple_tag(takes_context=True)
def replace_query_param(context, **kwargs):
    query = context.request.GET.copy()

    # Create query param or replace existing
    for key in kwargs:
        value = kwargs[key]
        query.__setitem__(key, value)

    return query.urlencode()


@register.filter(name="hash_tag")
def hash_tag(tag_name):
    return "#" + tag_name


@register.filter(name="qt_all_present")
def qt_all_present(qt, tag_names_set):
    """检查 quick tag 的所有标签是否都已存在于书签上"""
    if not isinstance(tag_names_set, (set, frozenset)):
        tag_names_set = set(tag_names_set)
    return all(tn in tag_names_set for tn in qt.get("tag_names", []))


@register.filter(name="toolbar_has_prev_visible")
def toolbar_has_prev_visible(toolbar_items, current_index):
    """检查 toolbar_items 中 current_index 之前是否有任何 has_content=True 的模块。
    跳过 date 模块：其 has_content 是全局设置，实际渲染取决于 per-bookmark 的 display_date，
    由模板层单独处理。
    """
    from bookmarks.models import UserProfile

    return any(
        item["has_content"] and item["key"] != UserProfile.TOOLBAR_MODULE_DATE
        for item in toolbar_items[:current_index]
    )


@register.filter(name="toolbar_any_visible")
def toolbar_any_visible(toolbar_items):
    """检查 toolbar_items 中是否有任何 has_content=True 的模块"""
    return any(item["has_content"] for item in toolbar_items)


@register.filter(name="sanitize_svg")
def sanitize_svg(svg_body):
    """清理 SVG body 中的 XSS 向量，返回 mark_safe 的安全字符串。"""
    return mark_safe(utils.sanitize_svg_body(svg_body or ""))


@register.filter(name="first_char")
def first_char(text):
    return text[0]


@register.filter(name="remaining_chars")
def remaining_chars(text, index):
    return text[index:]


@register.filter(name="humanize_absolute_date")
def humanize_absolute_date(value):
    if value in (None, ""):
        return ""
    return utils.humanize_absolute_date(value)


@register.filter(name="humanize_relative_date")
def humanize_relative_date(value):
    if value in (None, ""):
        return ""
    return utils.humanize_relative_date(value)


@register.filter(name="humanize_absolute_date_short")
def humanize_absolute_date_short(value):
    if value in (None, ""):
        return ""
    return utils.humanize_absolute_date_short(value)


@register.tag
def htmlmin(parser, token):
    nodelist = parser.parse(("endhtmlmin",))
    parser.delete_first_token()
    return HtmlMinNode(nodelist)


class HtmlMinNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        output = self.nodelist.render(context)

        output = re.sub(r"\s+", " ", output)

        return output


def schemeless_urls_to_https(attrs, _new):
    href_key = (None, "href")
    if href_key not in attrs:
        return attrs

    if attrs.get("_text", "").startswith("http://"):
        return attrs

    attrs[href_key] = re.sub(r"^http://", "https://", attrs[href_key])
    return attrs


linker = Linker(callbacks=[*DEFAULT_CALLBACKS, schemeless_urls_to_https])


@register.simple_tag(name="markdown", takes_context=True)
def render_markdown(context, markdown_text):
    # naive approach to reusing the renderer for a single request
    # works for bookmark list for now
    if "markdown_renderer" not in context:
        renderer = markdown.Markdown(extensions=["fenced_code", "nl2br"])
        context["markdown_renderer"] = renderer
    else:
        renderer = context["markdown_renderer"]

    as_html = renderer.convert(markdown_text)
    sanitized_html = bleach.clean(as_html, markdown_tags, markdown_attrs)
    linkified_html = linker.linkify(sanitized_html)

    return mark_safe(linkified_html)


def append_attr(widget, attr, value):
    attrs = widget.attrs
    if attrs.get(attr):
        attrs[attr] += " " + value
    else:
        attrs[attr] = value


@register.simple_tag
def formlabel(field, label_text):
    return mark_safe(
        f'<label for="{field.id_for_label}" class="form-label">{label_text}</label>'
    )


@register.simple_tag
def formfield(field, **kwargs):
    widget = field.field.widget

    label = kwargs.pop("label", None)
    if label and isinstance(widget, FormCheckbox):
        widget.label = label

    if kwargs.pop("has_help", False):
        append_attr(widget, "aria-describedby", field.auto_id + "_help")

    has_errors = hasattr(field, "errors") and field.errors
    if has_errors:
        append_attr(widget, "class", "is-error")
        append_attr(widget, "aria-describedby", field.auto_id + "_error")
    if field.field.required and not has_errors:
        append_attr(widget, "aria-invalid", "false")

    for attr, value in kwargs.items():
        attr = attr.replace("_", "-")
        if attr == "class":
            append_attr(widget, "class", value)
        else:
            widget.attrs[attr] = value

    return field.as_widget()


@register.tag
def formhelp(parser, token):
    try:
        tag_name, field_var = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError(
            f"{token.contents.split()[0]!r} tag requires a single argument (form field)"
        ) from None
    nodelist = parser.parse(("endformhelp",))
    parser.delete_first_token()
    return FormHelpNode(nodelist, field_var)


class FormHelpNode(template.Node):
    def __init__(self, nodelist, field_var):
        self.nodelist = nodelist
        self.field_var = template.Variable(field_var)

    def render(self, context):
        field = self.field_var.resolve(context)
        content = self.nodelist.render(context)
        return f'<div id="{field.auto_id}_help" class="form-input-hint">{content}</div>'


@register.filter
def extract_domain(value, user_profile=None):
    try:
        custom_domain_root = ""
        if (
            user_profile
            and hasattr(user_profile, "custom_domain_root")
            and user_profile.custom_domain_root
        ):
            custom_domain_root = user_profile.custom_domain_root
        return utils.get_sidebar_domain_filter_value(value, custom_domain_root)
    except Exception:
        return ""
