"""书签健康检查：任务触发与进度 API（页面/入口已并入书签列表与 bundle 列表）。"""

import logging
import os
import subprocess
import sys

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from bookmarks.models import HEALTH_STATUS_CHOICES, Bookmark, CheckJob
from bookmarks.services import health_checker
from bookmarks.type_defs import HttpRequest
from bookmarks.views.contexts import (
    _is_health_stale,
    format_health_checked_at,
)

logger = logging.getLogger(__name__)

ACTIVE_STATES = (CheckJob.STATE_PENDING, CheckJob.STATE_RUNNING)
# 存在这些状态时拒绝新任务：暂停中（interrupted）的任务尚未完成，不允许并发
BLOCK_NEW_STATES = (
    CheckJob.STATE_PENDING,
    CheckJob.STATE_RUNNING,
    CheckJob.STATE_INTERRUPTED,
)


def _resolve_python_bin() -> str:
    """解析用于拉起子进程的 Python 解释器路径。

    优先级：VIRTUAL_ENV 环境变量（runserver / uvicorn 激活的 venv）→
    项目内 .venv（docker 镜像兜底）→ sys.executable。uWSGI 嵌入模式下
    sys.executable 会指向 uwsgi 自身，不能直接用于启动子进程。
    """
    candidates = []
    if os.environ.get("VIRTUAL_ENV"):
        candidates.append(os.environ["VIRTUAL_ENV"])
    candidates.append(os.path.join(settings.BASE_DIR, ".venv"))
    for venv_dir in candidates:
        if os.name == "nt":
            python_bin = os.path.join(venv_dir, "Scripts", "python.exe")
        else:
            python_bin = os.path.join(venv_dir, "bin", "python")
        if os.path.isfile(python_bin):
            return python_bin
    return sys.executable


def _spawn_check_job(job: CheckJob) -> None:
    """以子进程方式拉起 health_check --job，脱离请求进程。

    - 使用 venv 内的 Python 解释器而非 sys.executable：uWSGI 嵌入模式下
      sys.executable 会指向 uwsgi 自身，直接调用会导致子进程启动失败。
    - start_new_session 使子进程不受请求进程/开发服务器重启连带影响。
    """
    log_dir = os.path.join(settings.BASE_DIR, "logs")
    log_path = os.path.join(log_dir, f"health_check_{job.pk}.log")
    manage_py = os.path.join(settings.BASE_DIR, "manage.py")
    python_bin = _resolve_python_bin()
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(log_path, "ab") as log_file:
            subprocess.Popen(
                [python_bin, manage_py, "health_check", "--job", str(job.pk)],
                cwd=settings.BASE_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=os.environ.copy(),
            )
    except OSError as exc:
        logger.exception("Failed to spawn check job %s", job.pk)
        raise RuntimeError(f"Failed to spawn check job: {exc}") from exc


def get_active_check_job(user):
    """当前用户的活跃健康检查任务（供书签列表 / bundle 列表页轮询胶囊）。"""
    from bookmarks.models import CheckJob

    return (
        CheckJob.objects.filter(
            owner=user, state__in=("pending", "running", "interrupted")
        )
        .order_by("-created_at")
        .first()
    )


def start_ids_job(user, ids) -> tuple:
    """为指定书签 id 列表创建并拉起健康检查任务（供批量动作复用）。

    返回 (job, None) 或 (None, error_message)。
    """
    from bookmarks.models import CheckJob

    if CheckJob.objects.filter(owner=user, state__in=BLOCK_NEW_STATES).exists():
        return None, _("A health check job is already running.")
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return None, _("Invalid bookmark ids.")
    if not ids:
        return None, _("Please select bookmarks to check.")
    job = CheckJob.objects.create(
        owner=user, scope={"mode": "ids", "ids": ids}, total=len(ids)
    )
    try:
        _spawn_check_job(job)
    except RuntimeError as exc:
        CheckJob.objects.filter(pk=job.pk).update(
            state=CheckJob.STATE_FAILED,
            error=str(exc)[:2000],
            finished_at=timezone.now(),
        )
        return None, _("Failed to start health check job.")
    return job, None


@login_required
def job_status(request: HttpRequest, job_id: int):
    job = get_object_or_404(CheckJob, pk=job_id, owner=request.user)
    stale = health_checker.is_heartbeat_stale(job)
    data = {
        "id": job.pk,
        "state": job.state,
        "total": job.total,
        "done": job.done,
        "heartbeat": job.heartbeat.isoformat() if job.heartbeat else None,
        "heartbeat_stale": stale,
        "error": job.error,
        "stop_requested": job.stop_requested,
        "active": job.is_active,
        "progress": int((job.done / job.total) * 100) if job.total else 0,
        "elapsed_sec": _job_elapsed_sec(job),
    }
    return JsonResponse(data)


def _job_elapsed_sec(job: CheckJob) -> int:
    """任务进行时长（暂停时段不计入）。"""
    if not job.started_at:
        return 0
    if job.paused_at:
        end = job.paused_at
    elif job.finished_at:
        end = job.finished_at
    else:
        end = timezone.now()
    return max(0, int((end - job.started_at).total_seconds()) - (job.paused_sec or 0))


@login_required
@require_POST
def job_control(request: HttpRequest, job_id: int):
    """任务控制：pause（暂停）/ resume（继续）/ cancel（中止）。

    - pause：置 stop_requested=True，worker 在下一次迭代时把任务置为
      interrupted（pending 未启动时，子进程启动前也会立即转 interrupted）。
    - resume：仅对 interrupted 任务重新拉起子进程续跑（run_job 会清掉
      stop_requested 并把状态置回 running）。
    - cancel：置 cancelled + stop_requested=True，worker 保持 cancelled 退出。
    """
    job = get_object_or_404(CheckJob, pk=job_id, owner=request.user)
    action = request.POST.get("action")

    if action == "pause":
        if job.state not in ACTIVE_STATES:
            return JsonResponse(
                {"ok": False, "error": _("Job is not running.")}, status=400
            )
        CheckJob.objects.filter(pk=job.pk).update(stop_requested=True)
        return JsonResponse({"ok": True, "state": job.state})

    if action == "resume":
        if job.state != CheckJob.STATE_INTERRUPTED:
            return JsonResponse(
                {"ok": False, "error": _("Job is not paused.")}, status=400
            )
        # 清掉暂停标记（worker 靠 stop_requested 判定暂停），并把当前暂停段
        # 计入累计暂停时长；随后拉起新子进程续跑（run_job 接受 interrupted，
        # 且仅首次启动时设置 started_at，进行时长得以保留）
        now = timezone.now()
        extra_paused = (
            int((now - job.paused_at).total_seconds()) if job.paused_at else 0
        )
        CheckJob.objects.filter(pk=job.pk).update(
            stop_requested=False,
            paused_sec=(job.paused_sec or 0) + max(0, extra_paused),
            paused_at=None,
        )
        try:
            _spawn_check_job(job)
        except RuntimeError as exc:
            return JsonResponse(
                {"ok": False, "error": str(exc)[:500]}, status=500
            )
        return JsonResponse({"ok": True})

    if action == "cancel":
        CheckJob.objects.filter(pk=job.pk).update(
            state=CheckJob.STATE_CANCELLED, stop_requested=True
        )
        return JsonResponse({"ok": True})

    return JsonResponse(
        {"ok": False, "error": _("Unknown action.")}, status=400
    )


@login_required
@require_POST
def check_single(request: HttpRequest, bookmark_id: int):
    """详情弹窗的单条健康检查（同步执行，返回 JSON）。

    检查结果直接写回书签；该域名禁用了健康检查（L1+L2 均关）时返回
    status=None（页面显示为 unknown）。
    """
    bookmark = get_object_or_404(
        Bookmark, pk=bookmark_id, owner=request.user, is_deleted=False
    )
    try:
        health_checker.check_bookmark(
            bookmark, write=True, username=request.user.username
        )
    except Exception as exc:
        logger.exception("Health check failed for bookmark %s", bookmark_id)
        return JsonResponse({"ok": False, "error": str(exc)[:500]}, status=500)
    bookmark.refresh_from_db(fields=["health_status", "health_details"])
    status = bookmark.health_status
    details = bookmark.health_details or {}
    status_display = ""
    if status:
        status_display = dict(HEALTH_STATUS_CHOICES).get(status, status)
    reason = details.get("reason", "")
    max_age_days = (
        request.user.profile.health_max_age
        if getattr(request.user, "profile", None)
        else 15
    )
    return JsonResponse(
        {
            "ok": True,
            "status": status,
            "status_display": status_display,
            "checked_at": details.get("checked_at"),
            "checked_at_display": format_health_checked_at(
                details.get("checked_at", "")
            ),
            "checked_at_label": _("Checked at"),
            "health_stale": _is_health_stale(
                details.get("checked_at", ""), max_age_days
            ),
            "reason": reason,
            "http_status": details.get("http_status"),
        }
    )


