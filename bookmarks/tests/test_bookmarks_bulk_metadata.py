from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Bookmark
from bookmarks.services.bookmarks import (
    bulk_edit_metadata,
    parse_metadata_ops,
    preview_metadata_edits,
)
from bookmarks.tests.helpers import BookmarkFactoryMixin


class ParseMetadataOpsTestCase(TestCase):
    def test_drops_unknown_fields_and_modes(self):
        ops = parse_metadata_ops(
            {
                "url": {"mode": "set", "value": "x"},
                "title": {"mode": "explode"},
                "description": {"mode": "clear"},
            }
        )

        self.assertEqual(ops, {"description": {"mode": "clear"}})

    def test_drops_replace_without_find_string(self):
        ops = parse_metadata_ops(
            {
                "title": {"mode": "replace", "find": "", "replace": "x"},
                "description": {"mode": "replace", "find": "a", "replace": "b"},
            }
        )

        self.assertNotIn("title", ops)
        self.assertEqual(ops["description"]["rules"], [{"find": "a", "replace": "b"}])

    def test_tolerates_invalid_json(self):
        self.assertEqual(parse_metadata_ops("{not json"), {})


class BulkEditMetadataServiceTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def test_set_and_clear(self):
        bookmark1 = self.setup_bookmark(title="a", description="d1", author="x")
        bookmark2 = self.setup_bookmark(title="b", description="d2", author="y")

        stats = bulk_edit_metadata(
            [bookmark1.id, bookmark2.id],
            {
                "title": {"mode": "set", "value": "统一标题"},
                "description": {"mode": "clear"},
            },
            self.user,
        )

        bookmark1.refresh_from_db()
        bookmark2.refresh_from_db()
        self.assertEqual(bookmark1.title, "统一标题")
        self.assertEqual(bookmark2.title, "统一标题")
        self.assertEqual(bookmark1.description, "")
        self.assertEqual(bookmark2.description, "")
        # 作者未出现在 ops 中，必须原样保留
        self.assertEqual(bookmark1.author, "x")
        self.assertEqual(bookmark2.author, "y")
        self.assertEqual(stats["updated"], 2)
        self.assertEqual(stats["fields"]["title"], 2)

    def test_replace_is_literal_and_global(self):
        bookmark = self.setup_bookmark(title="【转】a.b【转】a.b 【转】")
        other = self.setup_bookmark(title="untouched")

        bulk_edit_metadata(
            [bookmark.id, other.id],
            {"title": {"mode": "replace", "find": "【转】", "replace": ""}},
            self.user,
        )

        bookmark.refresh_from_db()
        other.refresh_from_db()
        # "." 在正则里有含义，这里必须按字面量处理
        self.assertEqual(bookmark.title, "a.ba.b ")
        self.assertEqual(other.title, "untouched")

    def test_replace_can_ignore_case(self):
        bookmark = self.setup_bookmark(title="Hello WORLD hello")

        bulk_edit_metadata(
            [bookmark.id],
            {
                "title": {
                    "mode": "replace",
                    "find": "hello",
                    "replace": "hi",
                    "case_insensitive": True,
                }
            },
            self.user,
        )

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "hi WORLD hi")

    def test_normalize_keeps_line_breaks(self):
        bookmark = self.setup_bookmark(description="  a  \t b \n c　　d  ")

        bulk_edit_metadata(
            [bookmark.id], {"description": {"mode": "normalize"}}, self.user
        )

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.description, "a b\nc d")

    def test_replace_rules_run_in_order(self):
        bookmark = self.setup_bookmark(title="abc")

        bulk_edit_metadata(
            [bookmark.id],
            {
                "title": {
                    "mode": "replace",
                    "rules": [
                        {"find": "a", "replace": "b"},
                        {"find": "b", "replace": "c"},
                    ],
                }
            },
            self.user,
        )
        bookmark.refresh_from_db()
        # 后一条规则看到的是前一条改写后的值
        self.assertEqual(bookmark.title, "ccc")

    def test_replace_rule_order_matters(self):
        bookmark = self.setup_bookmark(title="abc")

        bulk_edit_metadata(
            [bookmark.id],
            {
                "title": {
                    "mode": "replace",
                    "rules": [
                        {"find": "b", "replace": "c"},
                        {"find": "a", "replace": "b"},
                    ],
                }
            },
            self.user,
        )
        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "bcc")

    def test_skips_rules_without_find_string(self):
        bookmark = self.setup_bookmark(title="x-1")

        bulk_edit_metadata(
            [bookmark.id],
            {
                "title": {
                    "mode": "replace",
                    "rules": [
                        {"find": "", "replace": "z"},
                        {"find": "-1", "replace": ""},
                    ],
                }
            },
            self.user,
        )
        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "x")

    def test_parse_caps_rule_count(self):
        ops = parse_metadata_ops(
            {
                "title": {
                    "mode": "replace",
                    "rules": [{"find": f"s{i}", "replace": ""} for i in range(15)],
                }
            }
        )

        self.assertEqual(len(ops["title"]["rules"]), 10)

    def test_parse_tags_accepts_add_and_remove(self):
        ops = parse_metadata_ops({"tags": {"add": "one two", "remove": "three"}})

        self.assertEqual(ops["tags"]["add"], ["one", "two"])
        self.assertEqual(ops["tags"]["remove"], ["three"])

    def test_parse_accepts_single_rule_shape(self):
        ops = parse_metadata_ops(
            {"title": {"mode": "replace", "find": "a", "replace": "b"}}
        )

        self.assertEqual(ops["title"]["rules"], [{"find": "a", "replace": "b"}])

    def test_preview_reports_per_rule_hits(self):
        bookmark1 = self.setup_bookmark(title="广告 正文")
        bookmark2 = self.setup_bookmark(title="广告")
        ops = {
            "title": {
                "mode": "replace",
                "rules": [
                    {"find": "广告", "replace": ""},
                    {"find": "不存在的串", "replace": ""},
                ],
            }
        }
        ids = [bookmark1.id, bookmark2.id]

        preview = preview_metadata_edits(ids, ops, self.user)

        self.assertEqual(preview["fields"]["title"], 2)
        self.assertEqual(preview["steps"]["title"], [2, 0])

    def test_skips_bookmarks_without_effective_change(self):
        bookmark = self.setup_bookmark(title="same")
        before = Bookmark.objects.get(id=bookmark.id).date_modified

        stats = bulk_edit_metadata(
            [bookmark.id], {"title": {"mode": "set", "value": "same"}}, self.user
        )

        bookmark.refresh_from_db()
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(bookmark.date_modified, before)

    def test_only_touches_own_bookmarks(self):
        other_user = User.objects.create_user("other", "other@example.com", "pw")
        other_bookmark = self.setup_bookmark(user=other_user, title="theirs")
        own_bookmark = self.setup_bookmark(title="mine")

        bulk_edit_metadata(
            [other_bookmark.id, own_bookmark.id],
            {"title": {"mode": "set", "value": "hijacked"}},
            self.user,
        )

        other_bookmark.refresh_from_db()
        own_bookmark.refresh_from_db()
        self.assertEqual(other_bookmark.title, "theirs")
        self.assertEqual(own_bookmark.title, "hijacked")

    def test_tag_add_and_remove(self):
        tag1 = self.setup_tag(name="keep")
        tag2 = self.setup_tag(name="drop")
        bookmark = self.setup_bookmark(tags=[tag1, tag2])

        bulk_edit_metadata([bookmark.id], {"tags": {"remove": ["drop"]}}, self.user)
        bookmark.refresh_from_db()
        self.assertCountEqual(bookmark.tags.all(), [tag1])

        bulk_edit_metadata([bookmark.id], {"tags": {"add": ["new-tag"]}}, self.user)
        bookmark.refresh_from_db()
        self.assertCountEqual(
            bookmark.tags.values_list("name", flat=True), ["keep", "new-tag"]
        )

    def test_tag_remove_wins_before_add_for_same_name(self):
        tag = self.setup_tag(name="dup")
        bookmark = self.setup_bookmark(tags=[tag])

        bulk_edit_metadata(
            [bookmark.id],
            {"tags": {"add": ["dup", "extra"], "remove": ["dup"]}},
            self.user,
        )

        bookmark.refresh_from_db()
        self.assertCountEqual(
            bookmark.tags.values_list("name", flat=True), ["dup", "extra"]
        )

    def test_preview_matches_execution(self):
        bookmarks = [
            self.setup_bookmark(title="a", description="x"),
            self.setup_bookmark(title="b", description=""),
        ]
        ops = {
            "title": {"mode": "set", "value": "same"},
            "description": {"mode": "replace", "find": "x", "replace": "y"},
        }
        ids = [bookmark.id for bookmark in bookmarks]

        preview = preview_metadata_edits(ids, ops, self.user)
        # 预览不得写库
        bookmarks[0].refresh_from_db()
        self.assertEqual(bookmarks[0].title, "a")

        stats = bulk_edit_metadata(ids, ops, self.user)

        self.assertEqual(preview["total"], 2)
        self.assertEqual(preview["fields"], stats["fields"])
        self.assertEqual(preview["updated"], stats["updated"])

    def test_rolls_back_when_write_fails(self):
        bookmark = self.setup_bookmark(title="before")

        with (
            patch(
                "django.db.models.QuerySet.bulk_update",
                side_effect=RuntimeError("boom"),
            ),
            self.assertRaises(RuntimeError),
        ):
            bulk_edit_metadata(
                [bookmark.id],
                {"title": {"mode": "set", "value": "after"}},
                self.user,
            )

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "before")


class BulkEditMetadataViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def test_edits_selected_bookmarks(self):
        bookmark1 = self.setup_bookmark(title="a", description="x")
        bookmark2 = self.setup_bookmark(title="b", description="x")

        self.client.post(
            reverse("linkding:bookmarks.index.action"),
            {
                "bulk_action": ["bulk_edit_metadata"],
                "bulk_execute": [""],
                "bulk_metadata_ops": [
                    '{"description": {"mode": "clear"},'
                    ' "tags": {"mode": "add", "value": "inbox"}}'
                ],
                "bookmark_id": [str(bookmark1.id), str(bookmark2.id)],
            },
        )

        bookmark1.refresh_from_db()
        bookmark2.refresh_from_db()
        self.assertEqual(bookmark1.description, "")
        self.assertEqual(bookmark2.description, "")
        self.assertEqual(
            list(bookmark1.tags.values_list("name", flat=True)), ["inbox"]
        )

    def test_rejects_selection_over_limit(self):
        bookmarks = [self.setup_bookmark() for _ in range(3)]

        with patch("bookmarks.views.bookmarks.METADATA_EDIT_LIMIT", 2):
            self.client.post(
                reverse("linkding:bookmarks.index.action"),
                {
                    "bulk_action": ["bulk_edit_metadata"],
                    "bulk_execute": [""],
                    "bulk_metadata_ops": ['{"title": {"mode": "set", "value": "x"}}'],
                    "bookmark_id": [str(bookmark.id) for bookmark in bookmarks],
                },
            )

        for bookmark in bookmarks:
            bookmark.refresh_from_db()
            self.assertNotEqual(bookmark.title, "x")

    def test_preview_endpoint_returns_stats_without_writing(self):
        bookmark1 = self.setup_bookmark(title="a", description="x")
        bookmark2 = self.setup_bookmark(title="b", description="")

        response = self.client.post(
            reverse("linkding:bookmarks.metadata.preview"),
            {
                "mode": ["index"],
                "bulk_metadata_ops": [
                    '{"description": {"mode": "set", "value": "same"}}'
                ],
                "bookmark_id": [str(bookmark1.id), str(bookmark2.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["exceeded"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["stats"]["fields"]["description"], 2)

        bookmark1.refresh_from_db()
        self.assertEqual(bookmark1.description, "x")

    def test_preview_requires_post(self):
        response = self.client.get(reverse("linkding:bookmarks.metadata.preview"))
        self.assertEqual(response.status_code, 405)
