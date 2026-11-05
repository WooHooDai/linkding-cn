from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Annotation
from bookmarks.tests.helpers import BookmarkFactoryMixin


class HighlightsBulkViewTest(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def setup_highlight(
        self, text="a highlight", note="", color=Annotation.COLOR_YELLOW, bookmark=None
    ):
        bookmark = bookmark or self.setup_bookmark()
        return Annotation.objects.create(
            bookmark=bookmark,
            selector={"type": "TextQuoteSelector", "exact": text},
            selected_text=text,
            color=color,
            note_content=note,
        )

    def bulk(self, data, query=""):
        url = reverse("linkding:bookmarks.highlights") + query
        payload = {"action": "bulk_execute"}
        payload.update(data)
        return self.client.post(url, payload)

    # --- 选择解析 ---

    def test_bulk_delete_only_affects_selected_highlights(self):
        selected = self.setup_highlight()
        other = self.setup_highlight()

        self.bulk(
            {
                "bulk_action": "bulk_delete",
                "annotation_id": [str(selected.id)],
            }
        )

        self.assertFalse(Annotation.objects.filter(id=selected.id).exists())
        self.assertTrue(Annotation.objects.filter(id=other.id).exists())

    def test_bulk_delete_ignores_other_users_highlights(self):
        other_user = self.setup_user()
        foreign = self.setup_highlight(bookmark=self.setup_bookmark(user=other_user))

        self.bulk(
            {
                "bulk_action": "bulk_delete",
                "annotation_id": [str(foreign.id)],
            }
        )

        self.assertTrue(Annotation.objects.filter(id=foreign.id).exists())

    def test_bulk_select_across_respects_url_filters(self):
        with_note = self.setup_highlight(note="annotated")
        without_note = self.setup_highlight()

        self.bulk(
            {
                "bulk_action": "bulk_delete",
                "bulk_select_across": "on",
            },
            query="?note_filter=yes",
        )

        self.assertFalse(Annotation.objects.filter(id=with_note.id).exists())
        self.assertTrue(Annotation.objects.filter(id=without_note.id).exists())

    def test_bulk_select_across_respects_bookmark_filter(self):
        bookmark = self.setup_bookmark()
        scoped = self.setup_highlight(bookmark=bookmark)
        other = self.setup_highlight()

        self.bulk(
            {
                "bulk_action": "bulk_delete",
                "bulk_select_across": "on",
            },
            query=f"?bookmark_id={bookmark.id}",
        )

        self.assertFalse(Annotation.objects.filter(id=scoped.id).exists())
        self.assertTrue(Annotation.objects.filter(id=other.id).exists())

    def test_bulk_select_across_excludes_unchecked_highlights(self):
        excluded = self.setup_highlight()
        kept = self.setup_highlight()

        self.bulk(
            {
                "bulk_action": "bulk_delete",
                "bulk_select_across": "on",
                "bulk_exclude_id": [str(excluded.id)],
            }
        )

        self.assertTrue(Annotation.objects.filter(id=excluded.id).exists())
        self.assertFalse(Annotation.objects.filter(id=kept.id).exists())

    # --- 动作 ---

    def test_bulk_clear_note_updates_timestamp(self):
        annotation = self.setup_highlight(note="原来的批注")
        original_modified = annotation.date_modified

        self.bulk(
            {
                "bulk_action": "bulk_clear_note",
                "annotation_id": [str(annotation.id)],
            }
        )

        annotation.refresh_from_db()
        self.assertEqual("", annotation.note_content)
        self.assertGreater(annotation.date_modified, original_modified)

    def test_bulk_change_color(self):
        annotation = self.setup_highlight(color=Annotation.COLOR_YELLOW)

        self.bulk(
            {
                "bulk_action": "bulk_change_color",
                "bulk_new_color": Annotation.COLOR_PINK,
                "annotation_id": [str(annotation.id)],
            }
        )

        annotation.refresh_from_db()
        self.assertEqual(Annotation.COLOR_PINK, annotation.color)

    def test_bulk_change_color_ignores_unknown_color(self):
        annotation = self.setup_highlight(color=Annotation.COLOR_YELLOW)

        self.bulk(
            {
                "bulk_action": "bulk_change_color",
                "bulk_new_color": "chartreuse",
                "annotation_id": [str(annotation.id)],
            }
        )

        annotation.refresh_from_db()
        self.assertEqual(Annotation.COLOR_YELLOW, annotation.color)

    def test_single_delete_uses_own_field(self):
        # 勾选状态下表单同时带着 annotation_id 列表，删除按钮必须按自己的值删
        target = self.setup_highlight()
        checked = self.setup_highlight()

        self.client.post(
            reverse("linkding:bookmarks.highlights"),
            {
                "action": "delete",
                "delete": str(target.id),
                "annotation_id": [str(checked.id)],
            },
        )

        self.assertFalse(Annotation.objects.filter(id=target.id).exists())
        self.assertTrue(Annotation.objects.filter(id=checked.id).exists())

    # --- 复制取数 ---

    def test_copy_payload_returns_selected_items(self):
        first = self.setup_highlight(text="第一段", note="批注一")
        self.setup_highlight(text="第二段")

        response = self.client.post(
            reverse("linkding:bookmarks.highlights_copy_payload"),
            {
                "annotation_id": [str(first.id)],
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            [{"text": "第一段", "note": "批注一"}], response.json()["items"]
        )

    def test_copy_payload_select_across_respects_filters(self):
        annotated = self.setup_highlight(text="有批注", note="批注")
        self.setup_highlight(text="没有批注")

        response = self.client.post(
            reverse("linkding:bookmarks.highlights_copy_payload") + "?note_filter=yes",
            {"bulk_select_across": "on"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(["有批注"], [item["text"] for item in response.json()["items"]])
        self.assertTrue(Annotation.objects.filter(id=annotated.id).exists())

    # --- 模板 ---

    def test_page_renders_shared_bulk_bar(self):
        self.setup_highlight()

        response = self.client.get(reverse("linkding:bookmarks.highlights"))
        html = response.content.decode()

        self.assertEqual(200, response.status_code)
        self.assertIn("bulk-edit-bar", html)
        self.assertIn('name="annotation_id"', html)
        self.assertIn('name="delete"', html)
        self.assertNotIn("highlights-bulk-bar", html)
        self.assertNotIn("bulk_session_ids", html)

    def test_group_header_offers_bulk_checkbox(self):
        self.setup_highlight()

        response = self.client.get(
            reverse("linkding:bookmarks.highlights"), {"group_by": "bookmark"}
        )
        html = response.content.decode()

        self.assertIn("bulk-group-checkbox", html)
        self.assertIn("data-group-key", html)
