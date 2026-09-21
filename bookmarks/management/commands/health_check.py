"""Check bookmark health.

用法：
  python manage.py health_check --job <job_id>         执行/继续一个 CheckJob（主入口）
  python manage.py health_check --bookmark <id>       单条书签检查（调试用）

由检查页面按需以子进程方式拉起；不依赖 huey / cron。
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from bookmarks.models import Bookmark, CheckJob
from bookmarks.services import health_checker

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check bookmark health (single bookmark or a CheckJob)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--job",
            type=int,
            default=None,
            help="CheckJob id to run/resume.",
        )
        parser.add_argument(
            "--bookmark",
            type=int,
            default=None,
            help="Single bookmark id to check (debug).",
        )

    def handle(self, *args, **options):
        job_id = options.get("job")
        bookmark_id = options.get("bookmark")

        if job_id is not None:
            try:
                job = CheckJob.objects.get(pk=job_id)
            except CheckJob.DoesNotExist as exc:
                raise CommandError(f"CheckJob {job_id} does not exist") from exc
            health_checker.run_job(job)
            job.refresh_from_db()
            self.stdout.write(
                self.style.SUCCESS(
                    f"CheckJob {job.pk} finished: state={job.state} "
                    f"({job.done}/{job.total})"
                )
            )
            return

        if bookmark_id is not None:
            try:
                bookmark = Bookmark.objects.get(pk=bookmark_id)
            except Bookmark.DoesNotExist as exc:
                raise CommandError(f"Bookmark {bookmark_id} does not exist") from exc
            result = health_checker.check_bookmark(bookmark)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Bookmark {bookmark.pk}: {result['status']} "
                    f"(http={result.get('http_status')}, reason={result.get('reason')!r})"
                )
            )
            return

        raise CommandError("Specify --job <id> or --bookmark <id>")
