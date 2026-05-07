import importlib
from datetime import timedelta
from itertools import count

from django.test import TestCase
from django.utils import timezone

from bookmarks.models import Bookmark, BookmarkAsset, Tag
from bookmarks.tests.helpers import BookmarkFactoryMixin, disable_logging


class MergeBookmarkGroupTest(TestCase, BookmarkFactoryMixin):
    """验证 _merge_bookmark_group 不会丢失任何关联数据。

    合并函数不关心 URL 是否相同——它只负责把 others 的数据转移至
    primary 后删除 others。测试通过唯一 URL 规避 UniqueConstraint，
    专注验证数据合并不丢失。
    """

    def setUp(self):
        super().setUp()
        self.user = self.get_or_create_test_user()
        self.migration = importlib.import_module(
            "bookmarks.migrations.0066_deduplicate_bookmarks"
        )
        self.merge = self.migration._merge_bookmark_group
        self.BookmarkToTagRelationShip = Bookmark.tags.through
        self.now = timezone.now()
        self._url_counter = count()

    def _url(self):
        return f"https://example.com/{next(self._url_counter)}"

    def _make_bookmark(self, url, title, description="", notes="", **kwargs):
        defaults = dict(
            date_added=self.now,
            date_modified=self.now,
            date_accessed=self.now,
        )
        defaults.update(kwargs)
        return Bookmark.objects.create(
            url=url,
            title=title,
            description=description,
            notes=notes,
            owner=self.user,
            **defaults,
        )

    def _make_asset(self, bookmark, asset_type="snapshot", file="test.html"):
        return BookmarkAsset.objects.create(
            bookmark=bookmark,
            asset_type=asset_type,
            content_type="text/html",
            status="complete",
            file=file,
            display_name="test",
        )

    # ——— 资产转移 ——————————————————————————————————————————————

    @disable_logging
    def test_bookmark_assets_transferred_to_primary(self):
        primary = self._make_bookmark(self._url(), "Primary")
        other = self._make_bookmark(self._url(), "Other")
        asset = self._make_asset(other, file="snapshot.html")

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        self.assertFalse(Bookmark.objects.filter(id=other.id).exists())
        asset.refresh_from_db()
        self.assertEqual(asset.bookmark_id, primary.id)

    @disable_logging
    def test_latest_snapshot_fk_transferred(self):
        primary = self._make_bookmark(self._url(), "Primary")
        other = self._make_bookmark(self._url(), "Other")
        asset = self._make_asset(other, file="snap.html")
        other.latest_snapshot = asset
        other.save()

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.latest_snapshot_id, asset.id)

    @disable_logging
    def test_latest_snapshot_not_overwritten_if_primary_has_one(self):
        primary = self._make_bookmark(self._url(), "Primary")
        primary_asset = self._make_asset(primary, file="primary_snap.html")
        primary.latest_snapshot = primary_asset
        primary.save()

        other = self._make_bookmark(self._url(), "Other")
        other_asset = self._make_asset(other, file="other_snap.html")
        other.latest_snapshot = other_asset
        other.save()

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.latest_snapshot_id, primary_asset.id)

    # ——— 文本字段合并 ————————————————————————————————————————————

    @disable_logging
    def test_notes_merged_when_primary_empty(self):
        primary = self._make_bookmark(self._url(), "P", notes="")
        other = self._make_bookmark(self._url(), "O", notes="Important notes here")

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.notes, "Important notes here")

    @disable_logging
    def test_notes_concatenated_when_both_have_content(self):
        primary = self._make_bookmark(self._url(), "P", notes="Primary notes")
        other = self._make_bookmark(self._url(), "O", notes="Secondary notes")

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.notes, "Primary notes\n\nSecondary notes")

    @disable_logging
    def test_notes_concatenated_from_multiple_others(self):
        primary = self._make_bookmark(self._url(), "P", notes="Note A")
        other1 = self._make_bookmark(self._url(), "O1", notes="Note B")
        other2 = self._make_bookmark(self._url(), "O2", notes="Note C")

        self.merge(primary, [other1, other2], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.notes, "Note A\n\nNote B\n\nNote C")

    @disable_logging
    def test_description_concatenated(self):
        primary = self._make_bookmark(self._url(), "P", description="Desc A")
        other = self._make_bookmark(self._url(), "O", description="Desc B")

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.description, "Desc A\n\nDesc B")

    @disable_logging
    def test_description_merged_when_primary_empty(self):
        primary = self._make_bookmark(self._url(), "P", description="")
        other = self._make_bookmark(self._url(), "O", description="A description")

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.description, "A description")

    @disable_logging
    def test_title_merged_when_primary_empty(self):
        primary = self._make_bookmark(self._url(), "")
        other = self._make_bookmark(self._url(), "A real title")

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.title, "A real title")

    @disable_logging
    def test_web_archive_snapshot_url_merged(self):
        primary = self._make_bookmark(
            self._url(), "P", web_archive_snapshot_url=""
        )
        other = self._make_bookmark(
            self._url(), "O", web_archive_snapshot_url="https://archive.org/123"
        )

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.web_archive_snapshot_url, "https://archive.org/123")

    # ——— 布尔字段合并 ————————————————————————————————————————————

    @disable_logging
    def test_unread_uses_or_logic(self):
        primary = self._make_bookmark(self._url(), "P", unread=False)
        other = self._make_bookmark(self._url(), "O", unread=True)

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertTrue(primary.unread)

    @disable_logging
    def test_shared_uses_or_logic(self):
        primary = self._make_bookmark(self._url(), "P", shared=False)
        other = self._make_bookmark(self._url(), "O", shared=True)

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertTrue(primary.shared)

    @disable_logging
    def test_is_archived_uses_and_logic(self):
        primary = self._make_bookmark(self._url(), "P", is_archived=True)
        other = self._make_bookmark(self._url(), "O", is_archived=False)

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertFalse(primary.is_archived)

    @disable_logging
    def test_is_deleted_uses_or_logic(self):
        primary = self._make_bookmark(self._url(), "P", is_deleted=False)
        other = self._make_bookmark(self._url(), "O", is_deleted=True)

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertTrue(primary.is_deleted)

    # ——— 日期合并 ————————————————————————————————————————————————

    @disable_logging
    def test_date_added_keeps_earliest(self):
        early = self.now - timedelta(days=30)
        late = self.now

        primary = self._make_bookmark(self._url(), "P", date_added=late)
        other = self._make_bookmark(self._url(), "O", date_added=early)

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.date_added, early)

    @disable_logging
    def test_date_accessed_keeps_latest(self):
        early = self.now - timedelta(days=30)
        late = self.now

        primary = self._make_bookmark(self._url(), "P", date_accessed=early)
        other = self._make_bookmark(self._url(), "O", date_accessed=late)

        self.merge(primary, [other], self.BookmarkToTagRelationShip, BookmarkAsset)

        primary.refresh_from_db()
        self.assertEqual(primary.date_accessed, late)

    # ——— 标签合并 ————————————————————————————————————————————————

    @disable_logging
    def test_tags_are_union_of_all(self):
        t1 = Tag.objects.create(name="tag1", date_added=self.now, owner=self.user)
        t2 = Tag.objects.create(name="tag2", date_added=self.now, owner=self.user)
        t3 = Tag.objects.create(name="tag3", date_added=self.now, owner=self.user)

        primary = self._make_bookmark(self._url(), "P")
        other1 = self._make_bookmark(self._url(), "O1")
        other2 = self._make_bookmark(self._url(), "O2")

        primary.tags.add(t1)
        other1.tags.add(t2)
        other2.tags.add(t3)

        self.merge(
            primary, [other1, other2], self.BookmarkToTagRelationShip, BookmarkAsset
        )

        primary.refresh_from_db()
        tag_names = set(primary.tags.values_list("name", flat=True))
        self.assertEqual(tag_names, {"tag1", "tag2", "tag3"})

    # ——— 综合场景 ————————————————————————————————————————————————

    @disable_logging
    def test_merge_multiple_others_preserves_all_data(self):
        """综合场景：3 条书签各有不同内容，合并后一条不丢。"""
        t1 = Tag.objects.create(name="a", date_added=self.now, owner=self.user)
        t2 = Tag.objects.create(name="b", date_added=self.now, owner=self.user)
        t3 = Tag.objects.create(name="c", date_added=self.now, owner=self.user)

        primary = self._make_bookmark(
            self._url(), "Primary Title",
            notes="Primary note", description="Primary desc",
        )
        primary.tags.add(t1)

        other1 = self._make_bookmark(
            self._url(), "", notes="Note from O1",
            description="Desc from O1", shared=True,
        )
        other1.tags.add(t2)

        other2 = self._make_bookmark(
            self._url(), "", notes="Note from O2", is_archived=False,
        )
        other2.tags.add(t3)
        asset = self._make_asset(other2, file="snapshot.html")
        other2.latest_snapshot = asset
        other2.save()

        self.merge(
            primary, [other1, other2],
            self.BookmarkToTagRelationShip, BookmarkAsset,
        )

        primary.refresh_from_db()

        self.assertEqual(primary.title, "Primary Title")
        self.assertEqual(primary.notes, "Primary note\n\nNote from O1\n\nNote from O2")
        self.assertEqual(primary.description, "Primary desc\n\nDesc from O1")
        self.assertTrue(primary.shared)
        self.assertFalse(primary.is_archived)

        tag_names = set(primary.tags.values_list("name", flat=True))
        self.assertEqual(tag_names, {"a", "b", "c"})

        asset.refresh_from_db()
        self.assertEqual(asset.bookmark_id, primary.id)
        self.assertEqual(primary.latest_snapshot_id, asset.id)

        self.assertFalse(Bookmark.objects.filter(id=other1.id).exists())
        self.assertFalse(Bookmark.objects.filter(id=other2.id).exists())
        self.assertEqual(Bookmark.objects.filter(owner=self.user).count(), 1)
