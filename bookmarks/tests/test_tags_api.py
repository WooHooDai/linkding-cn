from django.urls import reverse
from rest_framework import status

from bookmarks.models import Bookmark, Tag
from bookmarks.tests.helpers import BookmarkFactoryMixin, LinkdingApiTestCase


class TagsApiTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    def test_tag_list_sorted_by_bookmark_count_desc(self):
        self.authenticate()

        tag_low = self.setup_tag(name="low")
        tag_high = self.setup_tag(name="high")
        tag_none = self.setup_tag(name="none")
        self.setup_bookmark(tags=[tag_low])
        self.setup_bookmark(tags=[tag_high])
        self.setup_bookmark(tags=[tag_high])
        deleted = self.setup_bookmark(tags=[tag_low])
        deleted.is_deleted = True
        deleted.save()

        url = reverse("linkding:tag-list")
        response = self.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        counts = {tag["name"]: tag["bookmark_count"] for tag in response.json()["results"]}
        self.assertEqual(counts["low"], 1)
        names = [tag["name"] for tag in response.json()["results"]]
        self.assertEqual(names, ["high", "low", "none"])

    def test_delete_tag(self):
        self.authenticate()

        tag = self.setup_tag()

        url = reverse("linkding:tag-detail", kwargs={"pk": tag.id})
        self.delete(url, expected_status_code=status.HTTP_204_NO_CONTENT)

        self.assertFalse(Tag.objects.filter(id=tag.id).exists())

    def test_delete_tag_keeps_tagged_bookmarks(self):
        self.authenticate()

        tag = self.setup_tag()
        bookmark = self.setup_bookmark(tags=[tag])

        url = reverse("linkding:tag-detail", kwargs={"pk": tag.id})
        self.delete(url, expected_status_code=status.HTTP_204_NO_CONTENT)

        self.assertFalse(Tag.objects.filter(id=tag.id).exists())
        self.assertTrue(Bookmark.objects.filter(id=bookmark.id).exists())
        self.assertEqual(bookmark.tags.count(), 0)

    def test_can_not_delete_tag_of_other_user(self):
        self.authenticate()

        other_user = self.setup_user()
        tag = self.setup_tag(user=other_user)

        url = reverse("linkding:tag-detail", kwargs={"pk": tag.id})
        self.delete(url, expected_status_code=status.HTTP_404_NOT_FOUND)

        self.assertTrue(Tag.objects.filter(id=tag.id).exists())
