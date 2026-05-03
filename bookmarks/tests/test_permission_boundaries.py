from django.urls import reverse
from rest_framework import status

from bookmarks.models import BookmarkBundle
from bookmarks.tests.helpers import BookmarkFactoryMixin, LinkdingApiTestCase


class TagsApiPermissionTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    """Verify that the tags API enforces user isolation."""

    def authenticate(self) -> None:
        self.api_token = self.setup_api_token()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.api_token.key)

    def test_list_tags_requires_authentication(self):
        self.get(
            reverse("linkding:tag-list"),
            expected_status_code=status.HTTP_401_UNAUTHORIZED,
        )

    def test_list_tags_only_returns_own_tags(self):
        self.authenticate()
        user = self.get_or_create_test_user()
        other_user = self.setup_user()

        self.setup_tag(user=user, name="my-tag")
        self.setup_tag(user=other_user, name="other-tag")

        response = self.get(
            reverse("linkding:tag-list"), expected_status_code=status.HTTP_200_OK
        )
        tag_names = [t["name"] for t in response.data["results"]]
        self.assertIn("my-tag", tag_names)
        self.assertNotIn("other-tag", tag_names)

    def test_create_tag_requires_authentication(self):
        data = {"name": "new-tag"}
        self.post(
            reverse("linkding:tag-list"),
            data,
            expected_status_code=status.HTTP_401_UNAUTHORIZED,
        )

    def test_get_tag_detail_only_returns_own_tags(self):
        self.authenticate()
        other_user = self.setup_user()
        other_tag = self.setup_tag(user=other_user, name="other-tag")

        self.get(
            reverse("linkding:tag-detail", args=[other_tag.id]),
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )


class BookmarkApiCrossUserTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    """Verify that bookmark API endpoints reject cross-user operations."""

    def authenticate(self) -> None:
        self.api_token = self.setup_api_token()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.api_token.key)

    def test_delete_other_users_bookmark_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(user=other_user)

        self.delete(
            reverse("linkding:bookmark-detail", args=[bookmark.id]),
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_archive_other_users_bookmark_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(user=other_user)

        self.post(
            reverse("linkding:bookmark-archive", args=[bookmark.id]),
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_unarchive_other_users_bookmark_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(user=other_user, is_archived=True)

        self.post(
            reverse("linkding:bookmark-unarchive", args=[bookmark.id]),
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_check_does_not_return_other_users_bookmarks(self):
        self.authenticate()
        other_user = self.setup_user()
        self.setup_bookmark(
            user=other_user, url="https://other-user-bookmark.com", title="Other"
        )

        response = self.get(
            reverse("linkding:bookmark-check")
            + "?url=https%3A%2F%2Fother-user-bookmark.com",
            expected_status_code=status.HTTP_200_OK,
        )
        self.assertIsNone(response.data.get("bookmark"))

    def test_trash_other_users_bookmark_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(user=other_user)

        self.post(
            reverse("linkding:bookmark-trash", args=[bookmark.id]),
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )


class SharedBookmarkWriteTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    """Verify that shared bookmarks are read-only for non-owners."""

    def authenticate(self) -> None:
        self.api_token = self.setup_api_token()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.api_token.key)

    def test_cannot_update_shared_bookmark_as_non_owner(self):
        self.authenticate()
        other_user = self.setup_user(enable_sharing=True)
        shared_bookmark = self.setup_bookmark(user=other_user, shared=True)

        self.put(
            reverse("linkding:bookmark-detail", args=[shared_bookmark.id]),
            {"url": "https://hacked.com"},
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_cannot_patch_shared_bookmark_as_non_owner(self):
        self.authenticate()
        other_user = self.setup_user(enable_sharing=True)
        shared_bookmark = self.setup_bookmark(user=other_user, shared=True)

        self.patch(
            reverse("linkding:bookmark-detail", args=[shared_bookmark.id]),
            {"title": "Hacked"},
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_cannot_delete_shared_bookmark_as_non_owner(self):
        self.authenticate()
        other_user = self.setup_user(enable_sharing=True)
        shared_bookmark = self.setup_bookmark(user=other_user, shared=True)

        self.delete(
            reverse("linkding:bookmark-detail", args=[shared_bookmark.id]),
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )


class BundleApiCrossUserTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    """Verify that bundle API endpoints reject cross-user operations."""

    def authenticate(self) -> None:
        self.api_token = self.setup_api_token()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.api_token.key)

    def test_create_bundle_ignores_other_user_in_payload(self):
        self.authenticate()
        other_user = self.setup_user()
        data = {
            "name": "sneaky-bundle",
            "search": "",
            "owner": other_user.id,
        }

        self.post(
            reverse("linkding:bundle-list"),
            data,
            expected_status_code=status.HTTP_201_CREATED,
        )
        # Bundle should be created under the authenticated user, not other_user
        user = self.get_or_create_test_user()
        bundle = BookmarkBundle.objects.get(name="sneaky-bundle")
        self.assertEqual(bundle.owner, user)

    def test_update_other_users_bundle_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        bundle = self.setup_bundle(user=other_user, name="other-bundle")

        self.put(
            reverse("linkding:bundle-detail", args=[bundle.id]),
            {"name": "hacked"},
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_delete_other_users_bundle_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        bundle = self.setup_bundle(user=other_user, name="other-bundle")

        self.delete(
            reverse("linkding:bundle-detail", args=[bundle.id]),
            expected_status_code=status.HTTP_404_NOT_FOUND,
        )


class AssetApiCrossUserTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    """Verify that asset API endpoints reject cross-user operations."""

    def authenticate(self) -> None:
        self.api_token = self.setup_api_token()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.api_token.key)

    def test_upload_asset_for_other_users_bookmark_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        other_bookmark = self.setup_bookmark(user=other_user)

        url = reverse(
            "linkding:bookmark_asset-upload", kwargs={"bookmark_id": other_bookmark.id}
        )
        self.post(url, {}, expected_status_code=status.HTTP_404_NOT_FOUND)

    def test_delete_other_users_asset_returns_404(self):
        self.authenticate()
        other_user = self.setup_user()
        other_bookmark = self.setup_bookmark(user=other_user)
        other_asset = self.setup_asset(bookmark=other_bookmark)

        url = reverse(
            "linkding:bookmark_asset-detail",
            kwargs={"bookmark_id": other_bookmark.id, "pk": other_asset.id},
        )
        self.delete(url, expected_status_code=status.HTTP_404_NOT_FOUND)
