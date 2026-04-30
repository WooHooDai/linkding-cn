from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from bookmarks.forms import TagForm, TagMergeForm
from bookmarks.models import Bookmark, Tag
from bookmarks.type_defs import HttpRequest
from bookmarks.utils import redirect_with_query
from bookmarks.views import turbo


@login_required
def tags_index(request: HttpRequest):
    if request.method == "POST" and "delete_tag" in request.POST:
        tag_id = request.POST.get("delete_tag")
        tag = get_object_or_404(Tag, id=tag_id, owner=request.user)
        tag_name = tag.name
        tag.delete()
        if not turbo.is_frame(request, "tag-main"):
            messages.success(
                request,
                _('Tag "%(tag_name)s" deleted successfully.') % {"tag_name": tag_name},
            )

        return redirect_with_query(request, reverse("linkding:tags.index"))

    search = request.GET.get("search", "").strip()
    unused_only = request.GET.get("unused", "") == "true"
    sort = request.GET.get("sort", "name-asc")

    tags_queryset = Tag.objects.filter(owner=request.user).annotate(
        bookmark_count=Count("bookmark")
    )

    if sort == "name-desc":
        tags_queryset = tags_queryset.order_by("-name")
    elif sort == "count-asc":
        tags_queryset = tags_queryset.order_by("bookmark_count", "name")
    elif sort == "count-desc":
        tags_queryset = tags_queryset.order_by("-bookmark_count", "name")
    else:  # Default: name-asc
        tags_queryset = tags_queryset.order_by("name")
    total_tags = tags_queryset.count()

    if search:
        tags_queryset = tags_queryset.filter(name__icontains=search)

    if unused_only:
        tags_queryset = tags_queryset.filter(bookmark_count=0)

    paginator = Paginator(tags_queryset, 50)
    page_number = request.GET.get("page")
    page = paginator.get_page(page_number)

    context = {
        "page": page,
        "search": search,
        "unused_only": unused_only,
        "sort": sort,
        "total_tags": total_tags,
    }

    return render(request, "tags/index.html", context)


@login_required
def tag_new(request: HttpRequest):
    form_data = request.POST if request.method == "POST" else None
    form = TagForm(user=request.user, data=form_data)

    if request.method == "POST":
        if form.is_valid():
            tag = form.save()
            messages.success(
                request,
                _('Tag "%(tag_name)s" created successfully.') % {"tag_name": tag.name},
            )
            return HttpResponseRedirect(reverse("linkding:tags.index"))
        if turbo.accept(request):
            return turbo.replace(
                request,
                "tag-modal",
                "tags/new_modal.html",
                {"form": form},
                status=422,
            )

    status = 422 if request.method == "POST" and not form.is_valid() else 200
    template = (
        "tags/new_modal.html"
        if turbo.is_frame(request, "tag-modal")
        else "tags/new.html"
    )
    return render(request, template, {"form": form}, status=status)


@login_required
def tag_edit(request: HttpRequest, tag_id: int):
    tag = get_object_or_404(Tag, id=tag_id, owner=request.user)
    form_data = request.POST if request.method == "POST" else None
    form = TagForm(user=request.user, data=form_data, instance=tag)

    if request.method == "POST":
        if form.is_valid():
            form.save()
            if not turbo.is_frame(request, "tag-main"):
                messages.success(
                    request,
                    _('Tag "%(tag_name)s" updated successfully.')
                    % {"tag_name": tag.name},
                )
            return redirect_with_query(request, reverse("linkding:tags.index"))
        if turbo.accept(request):
            return turbo.replace(
                request,
                "tag-modal",
                "tags/edit_modal.html",
                {"tag": tag, "form": form},
                status=422,
            )

    status = 422 if request.method == "POST" and not form.is_valid() else 200
    template = (
        "tags/edit_modal.html"
        if turbo.is_frame(request, "tag-modal")
        else "tags/edit.html"
    )
    return render(request, template, {"tag": tag, "form": form}, status=status)


@login_required
def tag_merge(request: HttpRequest):
    form_data = request.POST if request.method == "POST" else None
    form = TagMergeForm(user=request.user, data=form_data)

    if request.method == "POST":
        if form.is_valid():
            target_tag = form.cleaned_data["target_tag"]
            merge_tags = form.cleaned_data["merge_tags"]

            with transaction.atomic():
                BookmarkTag = Bookmark.tags.through

                # Get all bookmarks that have any of the merge tags, but do not
                # already have the target tag
                bookmark_ids = list(
                    Bookmark.objects.filter(tags__in=merge_tags)
                    .exclude(tags=target_tag)
                    .values_list("id", flat=True)
                    .distinct()
                )

                # Create new relationships to the target tag
                new_relationships = [
                    BookmarkTag(tag_id=target_tag.id, bookmark_id=bookmark_id)
                    for bookmark_id in bookmark_ids
                ]

                if new_relationships:
                    BookmarkTag.objects.bulk_create(new_relationships)

                # Bulk delete all relationships for merge tags
                merge_tag_ids = [tag.id for tag in merge_tags]
                BookmarkTag.objects.filter(tag_id__in=merge_tag_ids).delete()

                # Delete the merged tags
                tag_names = [tag.name for tag in merge_tags]
                Tag.objects.filter(id__in=merge_tag_ids).delete()

                messages.success(
                    request,
                    ngettext(
                        'Successfully merged %(count)s tag (%(tag_names)s) into "%(target_tag)s".',
                        'Successfully merged %(count)s tags (%(tag_names)s) into "%(target_tag)s".',
                        len(merge_tags),
                    )
                    % {
                        "count": len(merge_tags),
                        "tag_names": ", ".join(tag_names),
                        "target_tag": target_tag.name,
                    },
                )

            return HttpResponseRedirect(reverse("linkding:tags.index"))
        if turbo.accept(request):
            return turbo.replace(
                request,
                "tag-modal",
                "tags/merge_modal.html",
                {"form": form},
                status=422,
            )

    status = 422 if request.method == "POST" and not form.is_valid() else 200
    template = (
        "tags/merge_modal.html"
        if turbo.is_frame(request, "tag-modal")
        else "tags/merge.html"
    )
    return render(request, template, {"form": form}, status=status)
