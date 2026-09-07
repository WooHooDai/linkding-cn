import contextlib
import datetime
import random
import time

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import (
    Case,
    CharField,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    When,
)
from django.db.models.functions import Collate, Lower

from bookmarks.models import (
    Annotation,
    Bookmark,
    BookmarkBundle,
    BookmarkSearch,
    Tag,
    UserProfile,
    parse_tag_string,
)
from bookmarks.services.search_query_parser import (
    AndExpression,
    FieldTermExpression,
    NotExpression,
    OrExpression,
    SearchExpression,
    SearchQueryParseError,
    SpecialKeywordExpression,
    TagExpression,
    TermExpression,
    extract_tag_names_from_query,
    parse_search_query,
)
from bookmarks.utils import unique


def query_bookmarks(
    user: User,
    profile: UserProfile,
    search: BookmarkSearch,
) -> QuerySet:
    return _base_bookmarks_query(user, profile, search).filter(
        is_archived=False, is_deleted=False
    )


def query_archived_bookmarks(
    user: User, profile: UserProfile, search: BookmarkSearch
) -> QuerySet:
    return _base_bookmarks_query(user, profile, search).filter(
        is_archived=True, is_deleted=False
    )


def query_shared_bookmarks(
    user: User | None,
    profile: UserProfile,
    search: BookmarkSearch,
    public_only: bool,
) -> QuerySet:
    conditions = (
        Q(shared=True) & Q(owner__profile__enable_sharing=True) & Q(is_deleted=False)
    )
    if public_only:
        conditions = conditions & Q(owner__profile__enable_public_sharing=True)

    return _base_bookmarks_query(user, profile, search).filter(conditions)


def query_trashed_bookmarks(
    user: User,
    profile: UserProfile,
    search: BookmarkSearch,
) -> QuerySet:
    return _base_bookmarks_query(user, profile, search).filter(is_deleted=True)


def _build_term_search_condition(term: str, profile: UserProfile) -> Q:
    conditions = (
        Q(title__icontains=term)
        | Q(description__icontains=term)
        | Q(notes__icontains=term)
        | Q(url__icontains=term)
    )

    if profile.tag_search == UserProfile.TAG_SEARCH_LAX:
        conditions = conditions | Exists(
            Bookmark.objects.filter(id=OuterRef("id"), tags__name__iexact=term)
        )

    return conditions


def _parse_date(value):
    """解析日期字符串为 date 对象。"""
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def _build_domain_group_condition(raw_group: str) -> Q:
    parts = [p.strip().lower() for p in raw_group.split("|")]
    parts = [p for p in parts if p]
    if not parts:
        return Q()

    group_condition = Q()
    for part in parts:
        if part.startswith("."):
            # Subdomain match: domain:(.a.com) matches *.a.com, but excludes a.com itself.
            base = part[1:]
            if not base:
                continue
            http_sub = (
                Q(url__istartswith="http://")
                & (
                    Q(url__icontains=f".{base}/")
                    | Q(url__icontains=f".{base}:")
                    | Q(url__icontains=f".{base}?")
                    | Q(url__icontains=f".{base}#")
                    | Q(url__iendswith=f".{base}")
                )
                & ~(
                    Q(url__iexact=f"http://{base}")
                    | Q(url__istartswith=f"http://{base}/")
                    | Q(url__istartswith=f"http://{base}:")
                    | Q(url__istartswith=f"http://{base}?")
                    | Q(url__istartswith=f"http://{base}#")
                )
            )
            https_sub = (
                Q(url__istartswith="https://")
                & (
                    Q(url__icontains=f".{base}/")
                    | Q(url__icontains=f".{base}:")
                    | Q(url__icontains=f".{base}?")
                    | Q(url__icontains=f".{base}#")
                    | Q(url__iendswith=f".{base}")
                )
                & ~(
                    Q(url__iexact=f"https://{base}")
                    | Q(url__istartswith=f"https://{base}/")
                    | Q(url__istartswith=f"https://{base}:")
                    | Q(url__istartswith=f"https://{base}?")
                    | Q(url__istartswith=f"https://{base}#")
                )
            )
            group_condition |= http_sub | https_sub
        else:
            # Exact host match.
            http_prefix = f"http://{part}"
            https_prefix = f"https://{part}"
            http_exact = (
                Q(url__iexact=http_prefix)
                | Q(url__istartswith=http_prefix + "/")
                | Q(url__istartswith=http_prefix + ":")
                | Q(url__istartswith=http_prefix + "?")
                | Q(url__istartswith=http_prefix + "#")
            )
            https_exact = (
                Q(url__iexact=https_prefix)
                | Q(url__istartswith=https_prefix + "/")
                | Q(url__istartswith=https_prefix + ":")
                | Q(url__istartswith=https_prefix + "?")
                | Q(url__istartswith=https_prefix + "#")
            )
            group_condition |= http_exact | https_exact

    return group_condition


def _field_term_expression_to_q(field_name: str, term: str) -> Q:
    if field_name == "title":
        # Match resolved_title logic: fall back to url when title is empty
        return Q(title__icontains=term) | (
            (Q(title__isnull=True) | Q(title="")) & Q(url__icontains=term)
        )
    if field_name == "desc":
        return Q(description__icontains=term)
    if field_name == "notes":
        return Q(notes__icontains=term)
    if field_name == "url":
        return Q(url__icontains=term)
    if field_name == "domain":
        return _build_domain_group_condition(term)
    if field_name == "hl":
        return Q(annotations__selected_text__icontains=term)
    if field_name == "ann":
        return Q(annotations__note_content__icontains=term)
    return Q()


def _annotation_field_term_expression_to_q(field_name: str, term: str) -> Q:
    """将搜索字段转换为 Annotation 模型的 Q 对象"""
    if field_name == "title":
        # Match resolved_title logic: fall back to url when title is empty
        return Q(bookmark__title__icontains=term) | (
            (Q(bookmark__title__isnull=True) | Q(bookmark__title="")) & Q(bookmark__url__icontains=term)
        )
    if field_name == "desc":
        return Q(bookmark__description__icontains=term)
    if field_name == "notes":
        return Q(bookmark__notes__icontains=term)
    if field_name == "url":
        return Q(bookmark__url__icontains=term)
    if field_name == "domain":
        # domain 搜索书签的 URL，通过子查询匹配
        bookmark_ids = Bookmark.objects.filter(
            _build_domain_group_condition(term)
        ).values_list("id", flat=True)
        return Q(bookmark_id__in=bookmark_ids)
    if field_name == "hl":
        return Q(selected_text__icontains=term)
    if field_name == "ann":
        return Q(note_content__icontains=term)
    return Q()


def _convert_ast_to_q_object(ast_node: SearchExpression, profile: UserProfile) -> Q:
    if isinstance(ast_node, TermExpression):
        return _build_term_search_condition(ast_node.term, profile)

    elif isinstance(ast_node, FieldTermExpression):
        return _field_term_expression_to_q(ast_node.field, ast_node.term)

    elif isinstance(ast_node, TagExpression):
        # Use Exists() to avoid reusing the same join when combining multiple tag expressions with and
        return Q(
            Exists(
                Bookmark.objects.filter(
                    id=OuterRef("id"), tags__name__iexact=ast_node.tag
                )
            )
        )

    elif isinstance(ast_node, SpecialKeywordExpression):
        # Handle special keywords
        if ast_node.keyword.lower() == "unread":
            return Q(unread=True)
        elif ast_node.keyword.lower() == "untagged":
            return Q(tags=None)
        else:
            # Unknown keyword, return empty Q object (matches all)
            return Q()

    elif isinstance(ast_node, AndExpression):
        # Combine left and right with AND
        left_q = _convert_ast_to_q_object(ast_node.left, profile)
        right_q = _convert_ast_to_q_object(ast_node.right, profile)
        return left_q & right_q

    elif isinstance(ast_node, OrExpression):
        # Combine left and right with OR
        left_q = _convert_ast_to_q_object(ast_node.left, profile)
        right_q = _convert_ast_to_q_object(ast_node.right, profile)
        return left_q | right_q

    elif isinstance(ast_node, NotExpression):
        # Negate the operand
        operand_q = _convert_ast_to_q_object(ast_node.operand, profile)
        return ~operand_q

    else:
        # Fallback for unknown node types
        return Q()


def _convert_annotation_ast_to_q_object(ast_node: SearchExpression, profile: UserProfile) -> Q:
    """将搜索 AST 转换为 Annotation 模型的 Q 对象"""
    if isinstance(ast_node, TermExpression):
        # 默认搜高亮文本和批注内容
        return Q(selected_text__icontains=ast_node.term) | Q(note_content__icontains=ast_node.term)

    elif isinstance(ast_node, FieldTermExpression):
        return _annotation_field_term_expression_to_q(ast_node.field, ast_node.term)

    elif isinstance(ast_node, TagExpression):
        # 通过 bookmark__ 跨表查询标签
        return Q(bookmark__tags__name__iexact=ast_node.tag)

    elif isinstance(ast_node, SpecialKeywordExpression):
        # 对于 Annotation，!unread 和 !untagged 不适用，返回空 Q
        return Q()

    elif isinstance(ast_node, AndExpression):
        left_q = _convert_annotation_ast_to_q_object(ast_node.left, profile)
        right_q = _convert_annotation_ast_to_q_object(ast_node.right, profile)
        return left_q & right_q

    elif isinstance(ast_node, OrExpression):
        left_q = _convert_annotation_ast_to_q_object(ast_node.left, profile)
        right_q = _convert_annotation_ast_to_q_object(ast_node.right, profile)
        return left_q | right_q

    elif isinstance(ast_node, NotExpression):
        operand_q = _convert_annotation_ast_to_q_object(ast_node.operand, profile)
        return ~operand_q

    else:
        return Q()


def _filter_search_query(
    query_set: QuerySet, query_string: str, profile: UserProfile
) -> QuerySet:
    """New search filtering logic using logical expressions."""

    try:
        ast = parse_search_query(query_string)
        if ast:
            search_query = _convert_ast_to_q_object(ast, profile)
            query_set = query_set.filter(search_query)
    except SearchQueryParseError:
        # If the query cannot be parsed, return zero results
        return query_set.none()

    return query_set


def _filter_annotation_search_query(
    query_set: QuerySet, query_string: str, profile: UserProfile
) -> QuerySet:
    """为 Annotation 模型过滤搜索查询"""

    try:
        ast = parse_search_query(query_string)
        if ast:
            search_query = _convert_annotation_ast_to_q_object(ast, profile)
            query_set = query_set.filter(search_query)
    except SearchQueryParseError:
        return query_set.none()

    return query_set


def _filter_search_query_legacy(
    query_set: QuerySet, query_string: str, profile: UserProfile
) -> QuerySet:
    """Legacy search filtering logic where everything is just combined with AND."""

    # Split query into search terms and tags
    query = parse_query_string(query_string)

    # Filter for search terms and tags
    for term in query["search_terms"]:
        conditions = (
            Q(title__icontains=term)
            | Q(description__icontains=term)
            | Q(notes__icontains=term)
            | Q(url__icontains=term)
        )

        if profile.tag_search == UserProfile.TAG_SEARCH_LAX:
            conditions = conditions | Exists(
                Bookmark.objects.filter(id=OuterRef("id"), tags__name__iexact=term)
            )

        query_set = query_set.filter(conditions)

    for tag_name in query["tag_names"]:
        query_set = query_set.filter(tags__name__iexact=tag_name)

    # Untagged bookmarks
    if query["untagged"]:
        query_set = query_set.filter(tags=None)
    # Legacy unread bookmarks filter from query
    if query["unread"]:
        query_set = query_set.filter(unread=True)

    return query_set


def _filter_bundle(query_set: QuerySet, bundle: BookmarkBundle) -> QuerySet:
    parsed = parse_query_string(bundle.search)
    search_terms = parsed["search_terms"]
    field_terms = parsed.get("field_terms", {})

    # 在所有位置查找关键词 (title/description/notes/url)
    for term in search_terms:
        conditions = (
            Q(title__icontains=term)
            | Q(description__icontains=term)
            | Q(notes__icontains=term)
            | Q(url__icontains=term)
        )
        query_set = query_set.filter(conditions)

    # 筛选field_term
    query_set = _apply_field_terms_filters(query_set, field_terms)

    # Any tags - at least one tag must match
    any_tags = parse_tag_string(bundle.any_tags, " ")
    if len(any_tags) > 0:
        tag_conditions = Q()
        for tag in any_tags:
            tag_conditions |= Q(tags__name__iexact=tag)

        query_set = query_set.filter(
            Exists(Bookmark.objects.filter(tag_conditions, id=OuterRef("id")))
        )

    # All tags - all tags must match
    all_tags = parse_tag_string(bundle.all_tags, " ")
    for tag in all_tags:
        query_set = query_set.filter(tags__name__iexact=tag)

    # Excluded tags - no tags must match
    exclude_tags = parse_tag_string(bundle.excluded_tags, " ")
    if len(exclude_tags) > 0:
        tag_conditions = Q()
        for tag in exclude_tags:
            tag_conditions |= Q(tags__name__iexact=tag)
        query_set = query_set.exclude(
            Exists(Bookmark.objects.filter(tag_conditions, id=OuterRef("id")))
        )

    return query_set


def _apply_filters(
    query_set: QuerySet, user: User | None, profile: UserProfile, search: BookmarkSearch
) -> QuerySet:
    # Filter by modified_since if provided
    if search.modified_since:
        with contextlib.suppress(ValidationError):
            query_set = query_set.filter(date_modified__gt=search.modified_since)

    # Filter by added_since if provided
    if search.added_since:
        with contextlib.suppress(ValidationError):
            query_set = query_set.filter(date_added__gt=search.added_since)

    # Filter by deleted_since if provided
    if search.deleted_since:
        with contextlib.suppress(ValidationError):
            query_set = query_set.filter(date_deleted__gt=search.deleted_since)

    # Filter by search query
    if profile.legacy_search:
        parsed_query = parse_query_string(search.q)
        query_set = _filter_search_query_legacy(query_set, search.q, profile)
        # 在 legacy 模式下保留 field-term 查询能力
        query_set = _apply_field_terms_filters(
            query_set, parsed_query.get("field_terms", {})
        )
    else:
        query_set = _filter_search_query(query_set, search.q, profile)

    # Unread filter from bookmark search
    if search.unread == BookmarkSearch.FILTER_UNREAD_YES:
        query_set = query_set.filter(unread=True)
    elif search.unread == BookmarkSearch.FILTER_UNREAD_NO:
        query_set = query_set.filter(unread=False)

    # Shared filter
    if search.shared == BookmarkSearch.FILTER_SHARED_SHARED:
        query_set = query_set.filter(shared=True)
    elif search.shared == BookmarkSearch.FILTER_SHARED_UNSHARED:
        query_set = query_set.filter(shared=False)

    # Tagged filter
    if search.tagged == BookmarkSearch.FILTER_TAGGED_TAGGED:
        query_set = query_set.filter(tags__isnull=False).distinct()
    elif search.tagged == BookmarkSearch.FILTER_TAGGED_UNTAGGED:
        query_set = query_set.filter(tags__isnull=True)

    # Asset presence filters
    if search.html_snapshot == BookmarkSearch.FILTER_ASSET_YES:
        query_set = query_set.filter(latest_snapshot__isnull=False)
    elif search.html_snapshot == BookmarkSearch.FILTER_ASSET_NO:
        query_set = query_set.filter(latest_snapshot__isnull=True)

    if search.preview_image == BookmarkSearch.FILTER_ASSET_YES:
        query_set = query_set.exclude(
            preview_image_file="",
            preview_image_remote_url="",
        )
    elif search.preview_image == BookmarkSearch.FILTER_ASSET_NO:
        query_set = query_set.filter(
            preview_image_file="",
            preview_image_remote_url="",
        )

    if search.favicon in (BookmarkSearch.FILTER_ASSET_YES, BookmarkSearch.FILTER_ASSET_NO):
        from bookmarks.models import FaviconCache
        from bookmarks.utils import get_alias_domains_for_root, parse_domain_roots

        # 收集所有有 favicon 的域名，加上别名展开（双向）
        raw_domains = set(
            FaviconCache.objects.filter(status="success", favicon_file__gt="")
            .values_list("domain", flat=True)
        )
        domain_config = parse_domain_roots(profile.custom_domain_root)
        match_domains = set(raw_domains)
        from bookmarks.utils import resolve_favicon_domain
        for d in raw_domains:
            # 正向：d 是目标域名，展开其所有别名
            match_domains.update(get_alias_domains_for_root(d, domain_config))
            # 反向：d 是别名域名，也加入其归一化目标（兼容旧数据）
            match_domains.add(resolve_favicon_domain(d, config=domain_config))

        if match_domains:
            domain_q = Q()
            for d in match_domains:
                domain_q |= Q(url__startswith=f"https://{d}") | Q(url__startswith=f"http://{d}")
            if search.favicon == BookmarkSearch.FILTER_ASSET_YES:
                query_set = query_set.filter(domain_q)
            else:
                query_set = query_set.exclude(domain_q)
        elif search.favicon == BookmarkSearch.FILTER_ASSET_YES:
            query_set = query_set.none()

    # Highlight filter
    if search.highlight == BookmarkSearch.FILTER_HIGHLIGHT_YES:
        query_set = query_set.filter(
            Exists(Annotation.objects.filter(bookmark=OuterRef("id")))
        )
    elif search.highlight == BookmarkSearch.FILTER_HIGHLIGHT_NO:
        query_set = query_set.filter(
            ~Exists(Annotation.objects.filter(bookmark=OuterRef("id")))
        )

    # Annotation filter
    if search.annotation == BookmarkSearch.FILTER_ANNOTATION_YES:
        query_set = query_set.filter(
            Exists(
                Annotation.objects.filter(
                    bookmark=OuterRef("id"), note_content__gt=""
                )
            )
        )
    elif search.annotation == BookmarkSearch.FILTER_ANNOTATION_NO:
        query_set = query_set.filter(
            ~Exists(
                Annotation.objects.filter(
                    bookmark=OuterRef("id"), note_content__gt=""
                )
            )
        )

    # Filter by bundle
    if search.bundle:
        query_set = _filter_bundle(query_set, search.bundle)

    # 日期筛选逻辑
    if search.date_filter_by in ("added", "modified", "deleted"):
        field_map = {
            "added": "date_added",
            "modified": "date_modified",
            "deleted": "date_deleted",
        }
        field = field_map[search.date_filter_by]
        start = _parse_date(search.date_filter_start)
        end = _parse_date(search.date_filter_end)
        if start:
            query_set = query_set.filter(**{f"{field}__gte": start})
        if end:
            if isinstance(end, datetime.date) and not isinstance(
                end, datetime.datetime
            ):
                end = end + datetime.timedelta(days=1)
            query_set = query_set.filter(**{f"{field}__lt": end})

    # 按高亮/批注创建日期筛选
    if search.date_filter_by in ("highlight", "annotation"):
        start = _parse_date(search.date_filter_start)
        end = _parse_date(search.date_filter_end)
        annotation_qs = Annotation.objects.filter(bookmark=OuterRef("id"))
        if search.date_filter_by == "annotation":
            annotation_qs = annotation_qs.filter(note_content__gt="")
        if start:
            annotation_qs = annotation_qs.filter(date_created__date__gte=start)
        if end:
            if isinstance(end, datetime.date) and not isinstance(
                end, datetime.datetime
            ):
                end = end + datetime.timedelta(days=1)
            annotation_qs = annotation_qs.filter(date_created__lt=end)
        query_set = query_set.filter(Exists(annotation_qs))

    return query_set


def _apply_field_terms_filters(query_set: QuerySet, field_terms: dict) -> QuerySet:
    """筛选field_term.

    支持的fields: title, desc, notes, url, domain
    - title/desc/notes/url：包含
    - domain：严格匹配http/https的host
    """
    if not field_terms:
        return query_set

    for term in field_terms.get("title", []):
        query_set = query_set.filter(title__icontains=term)

    for term in field_terms.get("desc", []):
        query_set = query_set.filter(description__icontains=term)

    for term in field_terms.get("notes", []):
        query_set = query_set.filter(notes__icontains=term)

    for term in field_terms.get("url", []):
        query_set = query_set.filter(url__icontains=term)

    domain_terms = field_terms.get("domain", [])
    if domain_terms:
        combined_domains_condition = Q()

        for raw_group in domain_terms:
            group_condition = _build_domain_group_condition(raw_group)

            # AND 逻辑连接多个 domain:(...) 分组
            combined_domains_condition &= (
                group_condition if combined_domains_condition else group_condition
            )

        if combined_domains_condition:
            query_set = query_set.filter(combined_domains_condition)

    return query_set


def _base_bookmarks_query(
    user: User | None,
    profile: UserProfile,
    search: BookmarkSearch,
) -> QuerySet:
    query_set = Bookmark.objects

    # Filter for user
    if user:
        query_set = query_set.filter(owner=user)

    # 对于随机排序，需要先进行排序，再进行其他过滤
    if search.sort == BookmarkSearch.SORT_RANDOM:
        base_query = query_set
        # 生成随机排序
        if search.request and hasattr(search.request, "session"):
            seed = search.request.session.get("random_sort_seed", int(time.time()))
        else:
            seed = int(time.time())
        ids = list(base_query.values_list("id", flat=True))
        rng = random.Random(seed)
        shuffled = ids[:]
        rng.shuffle(shuffled)
        order = Case(
            *[When(id=pk, then=pos) for pos, pk in enumerate(shuffled)],
            output_field=IntegerField(),
        )
        query_set = query_set.annotate(random_order=order).order_by("random_order")

        # 然后进行其他过滤
        query_set = _apply_filters(query_set, user, profile, search)

    else:
        # 对于非随机排序，保持原有的先过滤后排序逻辑
        query_set = _apply_filters(query_set, user, profile, search)

        # Sort
        if (
            search.sort == BookmarkSearch.SORT_TITLE_ASC
            or search.sort == BookmarkSearch.SORT_TITLE_DESC
        ):
            # For the title, the resolved_title logic from the Bookmark entity needs
            # to be replicated as there is no corresponding database field
            query_set = query_set.annotate(
                effective_title=Case(
                    When(
                        Q(title__isnull=False) & ~Q(title__exact=""),
                        then=Lower("title"),
                    ),
                    default=Lower("url"),
                    output_field=CharField(),
                )
            )

            # For SQLite, if the ICU extension is loaded, use the custom collation
            # loaded into the connection. This results in an improved sort order for
            # unicode characters (umlauts, etc.)
            if settings.USE_SQLITE and settings.USE_SQLITE_ICU_EXTENSION:
                order_field = Collate("effective_title", "ICU")
            else:
                order_field = "effective_title"

            if search.sort == BookmarkSearch.SORT_TITLE_ASC:
                query_set = query_set.order_by(order_field)
            elif search.sort == BookmarkSearch.SORT_TITLE_DESC:
                query_set = query_set.order_by(order_field).reverse()
        elif search.sort == BookmarkSearch.SORT_ADDED_ASC:
            query_set = query_set.order_by("date_added")
        elif search.sort == BookmarkSearch.SORT_ADDED_DESC:
            query_set = query_set.order_by("-date_added")
        elif search.sort == BookmarkSearch.SORT_MODIFIED_ASC:
            query_set = query_set.order_by("date_modified")
        elif search.sort == BookmarkSearch.SORT_MODIFIED_DESC:
            query_set = query_set.order_by("-date_modified")
        elif search.sort == BookmarkSearch.SORT_DELETED_ASC:
            query_set = query_set.order_by("date_deleted")
        elif search.sort == BookmarkSearch.SORT_DELETED_DESC:
            query_set = query_set.order_by("-date_deleted")
        else:
            # Sort by date added, descending by default
            query_set = query_set.order_by("-date_added")

    return query_set


def query_annotations(
    user: User,
    search_q: str = "",
    colors: list[str] | None = None,
    note_filter: str = "",
    sort: str = "-date_created",
    group_by: str = "none",
    date_filter_by: str = "",
    date_filter_start: str = "",
    date_filter_end: str = "",
    bookmark_id: int | None = None,
    with_related: bool = True,
) -> QuerySet:
    """查询用户的所有高亮 & 批注，支持搜索、颜色筛选、类型筛选、排序、聚合。

    Args:
        with_related: 是否 select_related(bookmark, article_asset)。
                      聚合查询(values+annotate)时应设为 False 以避免 JOIN 干扰分组。
    """
    qs = Annotation.objects.filter(bookmark__owner=user)
    if with_related:
        qs = qs.select_related("bookmark", "article_asset")

    # 按书签 ID 过滤
    if bookmark_id:
        qs = qs.filter(bookmark_id=bookmark_id)

    # 搜索关键词（使用搜索引擎解析器）
    if search_q:
        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            profile = UserProfile(user=user)
        qs = _filter_annotation_search_query(qs, search_q, profile)

    # 颜色筛选（多色列表）
    if colors:
        qs = qs.filter(color__in=colors)

    # 批注筛选：off/空=不限, yes=有批注, no=无批注
    if note_filter == "yes":
        qs = qs.filter(note_content__gt="")
    elif note_filter == "no":
        qs = qs.filter(Q(note_content="") | Q(note_content__isnull=True))

    # 日期筛选
    if date_filter_by in ("bookmark_added", "bookmark_modified", "highlight_created", "highlight_modified"):
        start = _parse_date(date_filter_start)
        end = _parse_date(date_filter_end)

        if date_filter_by == "bookmark_added":
            field = "bookmark__date_added"
            if start:
                qs = qs.filter(**{f"{field}__gte": start})
            if end:
                end = end + datetime.timedelta(days=1)
                qs = qs.filter(**{f"{field}__lt": end})

        elif date_filter_by == "bookmark_modified":
            field = "bookmark__date_modified"
            if start:
                qs = qs.filter(**{f"{field}__gte": start})
            if end:
                end = end + datetime.timedelta(days=1)
                qs = qs.filter(**{f"{field}__lt": end})

        elif date_filter_by == "highlight_created":
            if start:
                qs = qs.filter(date_created__date__gte=start)
            if end:
                end = end + datetime.timedelta(days=1)
                qs = qs.filter(date_created__lt=end)

        elif date_filter_by == "highlight_modified":
            if start:
                qs = qs.filter(date_modified__date__gte=start)
            if end:
                end = end + datetime.timedelta(days=1)
                qs = qs.filter(date_modified__lt=end)

    # 随机排序（数据库端随机，避免加载全部 ID 到内存）
    if sort == "random":
        return qs.order_by("?")

    # 不需要排序时直接返回（用于纯聚合查询）
    if not sort:
        return qs.order_by()

    # 聚合排序：根据 group_by 决定主排序和次排序
    if group_by == "bookmark":
        order_fields = ["bookmark__title", sort]
    elif group_by == "domain":
        # 按 URL 域名分组
        from django.db.models.functions import Lower

        qs = qs.annotate(_domain=Lower("bookmark__url"))
        order_fields = ["_domain", sort]
    elif group_by == "color":
        order_fields = ["color", sort]
    else:
        order_fields = [sort]

    qs = qs.order_by(*order_fields)
    return qs


def query_annotation_color_stats(user: User) -> dict:
    """统计用户各颜色的标注数量。"""
    from django.db.models import Count

    stats = (
        Annotation.objects.filter(bookmark__owner=user)
        .values("color")
        .annotate(count=Count("id"))
    )
    return {item["color"]: item["count"] for item in stats}


def query_annotation_summary(user: User) -> dict:
    """返回高亮 & 批注的摘要统计。"""
    from django.db.models import Count

    total_annotations = Annotation.objects.filter(bookmark__owner=user).count()
    total_notes = Annotation.objects.filter(
        bookmark__owner=user, note_content__gt=""
    ).count()
    total_bookmarks = (
        Annotation.objects.filter(bookmark__owner=user)
        .values("bookmark")
        .distinct()
        .count()
    )
    return {
        "total_annotations": total_annotations,
        "total_notes": total_notes,
        "total_bookmarks": total_bookmarks,
    }


def query_bookmark_tags(
    user: User, profile: UserProfile, search: BookmarkSearch
) -> QuerySet:
    bookmarks_query = query_bookmarks(user, profile, search)

    query_set = Tag.objects.filter(bookmark__in=bookmarks_query)

    return query_set.distinct()


def query_archived_bookmark_tags(
    user: User, profile: UserProfile, search: BookmarkSearch
) -> QuerySet:
    bookmarks_query = query_archived_bookmarks(user, profile, search)

    query_set = Tag.objects.filter(bookmark__in=bookmarks_query)

    return query_set.distinct()


def query_shared_bookmark_tags(
    user: User | None,
    profile: UserProfile,
    search: BookmarkSearch,
    public_only: bool,
) -> QuerySet:
    bookmarks_query = query_shared_bookmarks(user, profile, search, public_only)

    query_set = Tag.objects.filter(bookmark__in=bookmarks_query)

    return query_set.distinct()


def query_trashed_bookmark_tags(
    user: User, profile: UserProfile, search: BookmarkSearch
):
    bookmarks_query = query_trashed_bookmarks(user, profile, search)
    query_set = Tag.objects.filter(bookmark__in=bookmarks_query)
    return query_set.distinct()


def query_shared_bookmark_users(
    profile: UserProfile, search: BookmarkSearch, public_only: bool
) -> QuerySet:
    bookmarks_query = query_shared_bookmarks(None, profile, search, public_only)

    query_set = User.objects.filter(bookmark__in=bookmarks_query)

    return query_set.distinct()


def get_user_tags(user: User):
    return Tag.objects.filter(owner=user).all()


def get_tags_for_query(user: User, profile: UserProfile, query: str) -> QuerySet:
    tag_names = extract_tag_names_from_query(query, profile)

    if not tag_names:
        return Tag.objects.none()

    tag_conditions = Q()
    for tag_name in tag_names:
        tag_conditions |= Q(name__iexact=tag_name)

    return Tag.objects.filter(owner=user).filter(tag_conditions).distinct()


def get_shared_tags_for_query(
    user: User | None, profile: UserProfile, query: str, public_only: bool
) -> QuerySet:
    tag_names = extract_tag_names_from_query(query, profile)

    if not tag_names:
        return Tag.objects.none()

    # Build conditions similar to query_shared_bookmarks
    conditions = Q(bookmark__shared=True) & Q(
        bookmark__owner__profile__enable_sharing=True
    )
    if public_only:
        conditions = conditions & Q(
            bookmark__owner__profile__enable_public_sharing=True
        )
    if user is not None:
        conditions = conditions & Q(bookmark__owner=user)

    tag_conditions = Q()
    for tag_name in tag_names:
        tag_conditions |= Q(name__iexact=tag_name)

    return Tag.objects.filter(conditions).filter(tag_conditions).distinct()


def parse_query_string(query_string):
    r"""解析查询字符串为不同组件.

    语法说明:
    - Field terms:
        - 以title|desc|notes|url|domain开头，后跟:和非转义的(，然后是内容，直到匹配的)
        - 如果(被转义为\，则token被视为普通搜索项，如title:\(hello\) -> 搜索项'title:(hello)'
        - 在(...)中允许空格。)可以被转义为\\)
    - 保留的特性：#tag, !untagged, !unread
    """
    if not query_string:
        query_string = ""

    tokens = _tokenize_query_string(query_string.strip())
    return _parse_tokens(tokens)


def replace_field_terms(
    query_string: str, field_name: str, new_terms: list[str]
) -> str:
    tokens = _tokenize_query_string((query_string or "").strip())
    filtered_tokens = []

    for token in tokens:
        if _is_field_term(token):
            parsed_field_name, _ = _extract_field_content(token)
            if parsed_field_name == field_name:
                continue
        filtered_tokens.append(token)

    for term in new_terms:
        if term:
            # 包含空格或会被 tokenizer 断词的特殊字符时用引号
            if any(c in term for c in ' ()#!"'):
                escaped = term.replace("\\", "\\\\").replace('"', '\\"')
                filtered_tokens.append(f'{field_name}:"{escaped}"')
            else:
                filtered_tokens.append(f"{field_name}:{term}")

    return " ".join(filtered_tokens).strip()


def _tokenize_query_string(query_string):
    """分词：将query_string拆分为tokens, 处理field_term和转义."""
    if not query_string:
        return []

    tokens = []
    i = 0

    while i < len(query_string):
        # 忽略前置空格
        while i < len(query_string) and query_string[i].isspace():
            i += 1

        if i >= len(query_string):
            break

        # 检查是否为field_term，若是则进行提取
        field_prefixes = ("title:", "desc:", "notes:", "url:", "domain:")
        is_field_term = False
        field_prefix = None

        for prefix in field_prefixes:
            if query_string.startswith(prefix, i):
                is_field_term = True
                field_prefix = prefix
                break

        if is_field_term:
            token = _extract_field_token(query_string, i, field_prefix)
            if token:
                tokens.append(token)
                i += len(token)
                continue

        # 解析为普通token
        token_start = i
        while i < len(query_string) and not query_string[i].isspace():
            i += 1
        tokens.append(query_string[token_start:i])

    return tokens


def _extract_field_token(query_string, start_pos, field_prefix):
    """提取field_term，支持 field:(content) 和 field:keyword / field:"phrase" 两种语法。"""
    if not query_string.startswith(field_prefix, start_pos):
        return None

    prefix_end = start_pos + len(field_prefix)

    if prefix_end >= len(query_string):
        return None

    # 新语法：field:"phrase" 或 field:'phrase'
    if query_string[prefix_end] in ('"', "'"):
        quote_char = query_string[prefix_end]
        i = prefix_end + 1
        while i < len(query_string):
            if query_string[i] == "\\" and i + 1 < len(query_string):
                i += 2
                continue
            if query_string[i] == quote_char:
                return query_string[start_pos:i + 1]
            i += 1
        return None

    # 新语法：field:keyword（读到空格为止）
    if query_string[prefix_end] != "(":
        i = prefix_end
        while i < len(query_string) and not query_string[i].isspace():
            i += 1
        if i > prefix_end:
            return query_string[start_pos:i]
        return None

    # 查找闭合的 ')'
    depth = 1
    escaped = False
    i = prefix_end + 1

    while i < len(query_string):
        char = query_string[i]

        if escaped:
            escaped = False
            i += 1
            continue

        if char == "\\":
            escaped = True
            i += 1
            continue

        if char == "(":
            depth += 1
            i += 1
            continue

        if char == ")":
            depth -= 1
            i += 1
            if depth == 0:
                return query_string[start_pos:i]
            continue

        i += 1

    return None


def _parse_tokens(tokens):
    """解析tokens为搜索组件."""
    search_terms = []
    tag_names = []
    field_terms = {"title": [], "desc": [], "notes": [], "url": [], "domain": []}
    untagged = False
    unread = False

    for token in tokens:
        if token.startswith("#") and len(token) > 1:
            tag_names.append(token[1:])
        elif token == "!untagged":
            untagged = True
        elif token == "!unread":
            unread = True
        elif _is_field_term(token):
            field_name, content = _extract_field_content(token)
            if field_name and content:
                field_terms[field_name].append(content)
            else:
                # Field term syntax was detected but parsing failed
                # Treat as plain search term
                unescaped_token = _unescape_token(token)
                search_terms.append(unescaped_token)
        else:
            # Unescape parentheses for plain terms
            unescaped_token = _unescape_token(token)
            search_terms.append(unescaped_token)

    tag_names = unique(tag_names, str.lower)

    return {
        "search_terms": search_terms,
        "tag_names": tag_names,
        "untagged": untagged,
        "unread": unread,
        "field_terms": field_terms,
    }


def _is_field_term(token):
    """判断是否为field_term(如: title:(content))."""
    field_prefixes = ("title:", "desc:", "notes:", "url:", "domain:")
    return any(token.startswith(prefix) for prefix in field_prefixes)


def _extract_field_content(token):
    """提取字段名称和内容，支持 field:(content) 和 field:keyword / field:"phrase" 两种语法。"""
    field_prefixes = ("title:", "desc:", "notes:", "url:", "domain:")

    for prefix in field_prefixes:
        if token.startswith(prefix):
            field_name = prefix[:-1]  # Remove trailing ':'
            content_part = token[len(prefix) :]

            # 新语法：field:"phrase" 或 field:'phrase'
            if content_part and content_part[0] in ('"', "'"):
                quote_char = content_part[0]
                if content_part[-1] == quote_char and len(content_part) > 1:
                    content = content_part[1:-1]
                    # 反转义引号和反斜杠
                    content = content.replace(f"\\{quote_char}", quote_char).replace("\\\\", "\\")
                    return field_name, content
                return None, None

            # 新语法：field:keyword
            if content_part and not content_part.startswith("("):
                return field_name, content_part

            # 旧语法：field:(content)
            if content_part.startswith("\\("):
                return None, None

            if content_part.startswith("("):
                content = _extract_parenthesized_content(content_part)
                if content is not None:
                    return field_name, content

    return None, None


def _extract_parenthesized_content(text):
    """提取括号内的内容."""
    if not text.startswith("("):
        return None

    content_start = 1
    depth = 1
    escaped = False
    i = 1

    while i < len(text):
        char = text[i]

        if escaped:
            escaped = False
            i += 1
            continue

        if char == "\\":
            escaped = True
            i += 1
            continue

        if char == "(":
            # Do not allow nesting: treat as literal
            depth += 1
            i += 1
            continue

        if char == ")":
            depth -= 1
            i += 1
            if depth == 0:
                content = text[content_start : i - 1]
                return _unescape_token(content)
            continue

        i += 1

    return None


def _unescape_token(token):
    """处理转义."""
    return token.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
