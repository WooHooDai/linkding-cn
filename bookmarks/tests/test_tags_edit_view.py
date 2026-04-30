from django.test import TestCase
from django.urls import reverse

from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class TagsEditViewTestCase(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self) -> None:
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def test_update_tag(self):
        tag = self.setup_tag(name="old_name")

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag.id]), {"name": "new_name"}
        )

        self.assertRedirects(response, reverse("linkding:tags.index"))

        tag.refresh_from_db()
        self.assertEqual(tag.name, "new_name")

    def test_allow_case_changes(self):
        tag = self.setup_tag(name="tag")

        self.client.post(reverse("linkding:tags.edit", args=[tag.id]), {"name": "TAG"})

        tag.refresh_from_db()
        self.assertEqual(tag.name, "TAG")

    def test_can_only_edit_own_tags(self):
        other_user = self.setup_user()
        tag = self.setup_tag(user=other_user)

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag.id]), {"name": "new_name"}
        )

        self.assertEqual(response.status_code, 404)
        tag.refresh_from_db()
        self.assertNotEqual(tag.name, "new_name")

    def test_show_error_for_empty_name(self):
        tag = self.setup_tag(name="tag1")

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag.id]), {"name": ""}
        )

        self.assertContains(response, "This field is required", status_code=422)
        tag.refresh_from_db()
        self.assertEqual(tag.name, "tag1")

    def test_show_error_for_duplicate_name(self):
        tag1 = self.setup_tag(name="tag1")
        self.setup_tag(name="tag2")

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag1.id]), {"name": "tag2"}
        )

        self.assertContains(
            response, "Tag &quot;tag2&quot; already exists", status_code=422
        )
        tag1.refresh_from_db()
        self.assertEqual(tag1.name, "tag1")

    def test_show_error_for_duplicate_name_different_casing(self):
        tag1 = self.setup_tag(name="tag1")
        self.setup_tag(name="tag2")

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag1.id]), {"name": "TAG2"}
        )

        self.assertContains(
            response, "Tag &quot;TAG2&quot; already exists", status_code=422
        )
        tag1.refresh_from_db()
        self.assertEqual(tag1.name, "tag1")

    def test_no_error_for_duplicate_name_different_user(self):
        other_user = self.setup_user()
        self.setup_tag(name="tag1", user=other_user)

        tag2 = self.setup_tag(name="tag2")

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag2.id]), {"name": "tag1"}
        )

        self.assertRedirects(response, reverse("linkding:tags.index"))
        tag2.refresh_from_db()
        self.assertEqual(tag2.name, "tag1")

    def test_update_shows_success_message(self):
        tag = self.setup_tag(name="old_name")

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag.id]),
            {"name": "new_name"},
            follow=True,
        )

        self.assertInHTML(
            """
            <div class="toast toast-success" role="alert">
                Tag "new_name" updated successfully.
            </div>
        """,
            response.content.decode(),
        )

    def test_update_tag_preserves_query_parameters(self):
        tag = self.setup_tag(name="old_name")

        url = (
            reverse("linkding:tags.edit", args=[tag.id])
            + "?search=search&unused=true&page=2&sort=name-desc"
        )
        response = self.client.post(url, {"name": "new_name"})

        expected_redirect = (
            reverse("linkding:tags.index")
            + "?search=search&unused=true&page=2&sort=name-desc"
        )
        self.assertRedirects(response, expected_redirect)

    def test_frame_get_renders_modal(self):
        tag = self.setup_tag(name="tag1")

        response = self.client.get(
            reverse("linkding:tags.edit", args=[tag.id]), HTTP_TURBO_FRAME="tag-modal"
        )

        soup = self.make_soup(response.content.decode())
        self.assertIsNotNone(soup.select_one('turbo-frame#tag-modal'))
        self.assertIsNotNone(soup.select_one("ld-modal"))
        self.assertContains(
            response,
            f'action="{reverse("linkding:tags.edit", args=[tag.id])}?"',
            html=False,
        )

    def test_invalid_turbo_post_replaces_modal(self):
        tag = self.setup_tag(name="tag1")

        response = self.client.post(
            reverse("linkding:tags.edit", args=[tag.id]),
            {"name": ""},
            HTTP_ACCEPT="text/vnd.turbo-stream.html",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response["Content-Type"], "text/vnd.turbo-stream.html")
        self.assertIn('target="tag-modal"', response.content.decode())
