from django.http import HttpRequest, HttpResponse
from django.shortcuts import render as django_render
from django.template import loader

BOOKMARK_PAGE_STREAM_HEADER = "X-Linkding-Bookmark-Page-Stream"


def accept(request: HttpRequest):
    is_turbo_request = "text/vnd.turbo-stream.html" in request.headers.get("Accept", "")
    disable_turbo = request.POST.get("disable_turbo", "false") == "true"

    return is_turbo_request and not disable_turbo


def accept_bookmark_page_stream(request: HttpRequest):
    return accept(request) and request.headers.get(BOOKMARK_PAGE_STREAM_HEADER) == "1"


def is_frame(request: HttpRequest, frame: str) -> bool:
    return request.headers.get("Turbo-Frame") == frame


def stream(request: HttpRequest, template_name: str, context: dict) -> HttpResponse:
    response = django_render(request, template_name, context)
    response["Content-Type"] = "text/vnd.turbo-stream.html"
    return response


def replace(
    request: HttpRequest,
    target_id: str,
    template_name: str,
    context: dict,
    status: int = 200,
    method: str = "morph",
) -> HttpResponse:
    content = loader.render_to_string(template_name, context, request)
    method_attr = f' method="{method}"' if method else ""
    stream_content = (
        f'<turbo-stream action="replace"{method_attr} target="{target_id}">'
        f"<template>{content}</template></turbo-stream>"
    )
    response = HttpResponse(stream_content, status=status)
    response["Content-Type"] = "text/vnd.turbo-stream.html"
    return response
