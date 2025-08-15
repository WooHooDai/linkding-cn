import time
import random
import datetime
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet, Exists, OuterRef, Case, When, CharField, F, IntegerField
from django.db.models.expressions import RawSQL
from django.db.models.functions import Lower, Mod

from bookmarks.models import (
    Bookmark,
    BookmarkBundle,
    BookmarkSearch,
    Tag,
    UserProfile,
    parse_tag_string,
)
from bookmarks.utils import unique


def query_bookmarks(
    user: User,
    profile: UserProfile,
    search: BookmarkSearch,
) -> QuerySet:
    return _base_bookmarks_query(user, profile, search).filter(is_archived=False, is_deleted=False)


def query_archived_bookmarks(
    user: User, profile: UserProfile, search: BookmarkSearch
) -> QuerySet:
    return _base_bookmarks_query(user, profile, search).filter(is_archived=True, is_deleted=False)


def query_shared_bookmarks(
    user: Optional[User],
    profile: UserProfile,
    search: BookmarkSearch,
    public_only: bool,
) -> QuerySet:
    conditions = Q(shared=True) & Q(owner__profile__enable_sharing=True) & Q(is_deleted=False)
    if public_only:
        conditions = conditions & Q(owner__profile__enable_public_sharing=True)

    return _base_bookmarks_query(user, profile, search).filter(conditions)


def query_trashed_bookmarks(
    user: User,
    profile: UserProfile,
    search: BookmarkSearch,
) -> QuerySet:
    return _base_bookmarks_query(user, profile, search).filter(is_deleted=True)


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


def _apply_filters(query_set: QuerySet, user: Optional[User], profile: UserProfile, search: BookmarkSearch) -> QuerySet:
    # Filter by modified_since if provided
    if search.modified_since:
        try:
            query_set = query_set.filter(date_modified__gt=search.modified_since)
        except ValidationError:
            # If the date format is invalid, ignore the filter
            pass

    # Filter by added_since if provided
    if search.added_since:
        try:
            query_set = query_set.filter(date_added__gt=search.added_since)
        except ValidationError:
            # If the date format is invalid, ignore the filter
            pass

    # Filter by deleted_since if provided
    if search.deleted_since:
        try:
            query_set = query_set.filter(date_deleted__gt=search.deleted_since)
        except ValidationError:
            # If the date format is invalid, ignore the filter
            pass

    # Split query into search terms, tags and field-terms
    query = parse_query_string(search.q)

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

    # 筛选field_term
    query_set = _apply_field_terms_filters(query_set, query.get("field_terms", {}))

    for tag_name in query["tag_names"]:
        query_set = query_set.filter(tags__name__iexact=tag_name)

    # Untagged bookmarks
    if query["untagged"]:
        query_set = query_set.filter(tags=None)
    # Legacy unread bookmarks filter from query
    if query["unread"]:
        query_set = query_set.filter(unread=True)

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

    # Filter by bundle
    if search.bundle:
        query_set = _filter_bundle(query_set, search.bundle)

    # 日期筛选逻辑
    def _parse_date(value):
        if isinstance(value, datetime.date):
            return value
        if isinstance(value, str) and value:
            try:
                return datetime.datetime.strptime(value, "%Y-%m-%d").date()
            except Exception:
                return None
        return None

    if search.date_filter_by in ("added", "modified", "deleted"):
        field_map = {
            "added": "date_added",
            "modified": "date_modified",
            "deleted": "date_deleted"
        }
        field = field_map[search.date_filter_by]
        start = _parse_date(search.date_filter_start)
        end = _parse_date(search.date_filter_end)
        if start:
            query_set = query_set.filter(**{f"{field}__gte": start})
        if end:
            if isinstance(end, datetime.date) and not isinstance(end, datetime.datetime):
                end = end + datetime.timedelta(days=1)
            query_set = query_set.filter(**{f"{field}__lt": end})

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
            # 或语法：使用`|`连接多个域名，如 domain:(v2ex.com | x.com)
            parts = [p.strip().lower() for p in raw_group.split('|')]
            parts = [p for p in parts if p]
            if not parts:
                continue

            group_condition = Q()
            for part in parts:
                if part.startswith('.'):
                    # 子域名匹配：domain:(.a.com) 匹配 *.a.com，不包含 a.com 本身
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
                    group_condition |= (http_sub | https_sub)
                else:
                    # 精确域名匹配
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
                    group_condition |= (http_exact | https_exact)

            # AND 逻辑连接多个 domain:(...) 分组
            combined_domains_condition &= group_condition if combined_domains_condition else group_condition

        if combined_domains_condition:
            query_set = query_set.filter(combined_domains_condition)

    return query_set


def _base_bookmarks_query(
    user: Optional[User],
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
        if search.request and hasattr(search.request, 'session'):
            seed = search.request.session.get('random_sort_seed', int(time.time()))
        else:
            seed = int(time.time())
        ids = list(base_query.values_list('id', flat=True))
        rng = random.Random(seed)
        shuffled = ids[:]
        rng.shuffle(shuffled)
        order = Case(
            *[When(id=pk, then=pos) for pos, pk in enumerate(shuffled)],
            output_field=IntegerField()
        )
        query_set = query_set.annotate(random_order=order).order_by('random_order')
        
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
                    When(Q(title__isnull=False) & ~Q(title__exact=""), then=Lower("title")),
                    default=Lower("url"),
                    output_field=CharField(),
                )
            )

            # For SQLite, if the ICU extension is loaded, use the custom collation
            # loaded into the connection. This results in an improved sort order for
            # unicode characters (umlauts, etc.)
            if settings.USE_SQLITE and settings.USE_SQLITE_ICU_EXTENSION:
                order_field = RawSQL("effective_title COLLATE ICU", ())
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
        elif search.sort == BookmarkSearch.SORT_DELETED_ASC:
            query_set = query_set.order_by("date_deleted")
        elif search.sort == BookmarkSearch.SORT_DELETED_DESC:
            query_set = query_set.order_by("-date_deleted")
        else:
            # Sort by date added, descending by default
            query_set = query_set.order_by("-date_added")

    return query_set


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
    user: Optional[User],
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


def parse_query_string(query_string):
    """解析查询字符串为不同组件.

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
    """提取field_term."""
    if not query_string.startswith(field_prefix, start_pos):
        return None
    
    prefix_end = start_pos + len(field_prefix)
    
    # 检查是否跟着非转义 '('
    if prefix_end >= len(query_string) or query_string[prefix_end] != '(':
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
        
        if char == '\\':
            escaped = True
            i += 1
            continue
        
        if char == '(':
            depth += 1
            i += 1
            continue
        
        if char == ')':
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
    field_terms = {
        "title": [], "desc": [], "notes": [], "url": [], "domain": []
    }
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
    """提取字段名称和内容."""
    field_prefixes = ("title:", "desc:", "notes:", "url:", "domain:")
    
    for prefix in field_prefixes:
        if token.startswith(prefix):
            field_name = prefix[:-1]  # Remove trailing ':'
            content_part = token[len(prefix):]
            
            # Check if content starts with unescaped '('
            # If it starts with '\(', it's escaped and should be treated as plain text
            if content_part.startswith('\\('):
                return None, None
            
            if not content_part.startswith('('):
                return None, None
            
            # Extract content between parentheses
            content = _extract_parenthesized_content(content_part)
            if content is not None:
                return field_name, content
    
    return None, None


def _extract_parenthesized_content(text):
    """提取括号内的内容."""
    if not text.startswith('('):
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
        
        if char == '\\':
            escaped = True
            i += 1
            continue
        
        if char == '(':
            # Do not allow nesting: treat as literal
            depth += 1
            i += 1
            continue
        
        if char == ')':
            depth -= 1
            i += 1
            if depth == 0:
                content = text[content_start:i-1]
                return _unescape_token(content)
            continue
        
        i += 1
    
    return None


def _unescape_token(token):
    """处理转义."""
    return (token.replace("\\(", "(")
                 .replace("\\)", ")")
                 .replace("\\\\", "\\"))
