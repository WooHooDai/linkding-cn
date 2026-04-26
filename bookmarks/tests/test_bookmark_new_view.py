from unittest import mock

from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Bookmark
import bookmarks.forms
from bookmarks.services import favicon_loader
from bookmarks.tests.helpers import BookmarkFactoryMixin


class BookmarkNewViewTestCase(TestCase, BookmarkFactoryMixin):

    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def create_form_data(self, overrides=None):
        if overrides is None:
            overrides = {}
        form_data = {
            "url": "http://example.com",
            "tag_string": "tag1 tag2",
            "title": "test title",
            "description": "test description",
            "notes": "test notes",
            "unread": False,
            "shared": False,
            "auto_close": "",
        }
        return {**form_data, **overrides}

    def test_should_create_new_bookmark(self):
        form_data = self.create_form_data()

        self.client.post(reverse("linkding:bookmarks.new"), form_data)

        self.assertEqual(Bookmark.objects.count(), 1)

        bookmark = Bookmark.objects.first()
        self.assertEqual(bookmark.owner, self.user)
        self.assertEqual(bookmark.url, form_data["url"])
        self.assertEqual(bookmark.title, form_data["title"])
        self.assertEqual(bookmark.description, form_data["description"])
        self.assertEqual(bookmark.notes, form_data["notes"])
        self.assertEqual(bookmark.unread, form_data["unread"])
        self.assertEqual(bookmark.shared, form_data["shared"])
        self.assertEqual(bookmark.tags.count(), 2)
        tags = bookmark.tags.order_by("name").all()
        self.assertEqual(tags[0].name, "tag1")
        self.assertEqual(tags[1].name, "tag2")

    def test_should_use_fast_metadata_enrichment_flow_for_new_bookmarks(self):
        form_data = self.create_form_data({"title": "", "description": ""})

        with mock.patch.object(
            bookmarks.forms,
            "create_bookmark",
            wraps=bookmarks.forms.create_bookmark,
        ) as mock_create_bookmark:
            self.client.post(reverse("linkding:bookmarks.new"), form_data)

        mock_create_bookmark.assert_called_once_with(
            mock.ANY,
            "tag1,tag2",
            self.user,
            schedule_metadata_enrichment=True,
        )

    def test_should_return_422_with_invalid_form(self):
        form_data = self.create_form_data({"url": ""})
        response = self.client.post(reverse("linkding:bookmarks.new"), form_data)
        self.assertEqual(response.status_code, 422)

    def test_should_create_new_unread_bookmark(self):
        form_data = self.create_form_data({"unread": True})

        self.client.post(reverse("linkding:bookmarks.new"), form_data)

        self.assertEqual(Bookmark.objects.count(), 1)

        bookmark = Bookmark.objects.first()
        self.assertTrue(bookmark.unread)

    def test_should_create_new_shared_bookmark(self):
        form_data = self.create_form_data({"shared": True})

        self.client.post(reverse("linkding:bookmarks.new"), form_data)

        self.assertEqual(Bookmark.objects.count(), 1)

        bookmark = Bookmark.objects.first()
        self.assertTrue(bookmark.shared)

    def test_should_prefill_url_from_url_parameter(self):
        response = self.client.get(
            reverse("linkding:bookmarks.new") + "?url=http://example.com"
        )
        html = response.content.decode()

        self.assertInHTML(
            """
            <input type="text" name="url" value="http://example.com" aria-invalid="false" class="form-input" required id="id_url">
            """,
            html,
        )

    def test_should_prefill_title_from_url_parameter(self):
        response = self.client.get(
            reverse("linkding:bookmarks.new") + "?title=Example%20Title"
        )
        html = response.content.decode()

        self.assertInHTML(
            '<input type="text" name="title" value="Example Title" '
            'class="form-input" maxlength="512" autocomplete="off" '
            'id="id_title">',
            html,
        )

    def test_should_prefill_description_from_url_parameter(self):
        response = self.client.get(
            reverse("linkding:bookmarks.new")
            + "?description=Example%20Site%20Description"
        )
        html = response.content.decode()

        self.assertInHTML(
            '<textarea name="description" class="form-input" cols="40" '
            'rows="3" id="id_description">Example Site Description</textarea>',
            html,
        )

    def test_should_prefill_tags_from_url_parameter(self):
        response = self.client.get(
            reverse("linkding:bookmarks.new") + "?tags=tag1%20tag2%20tag3"
        )
        html = response.content.decode()

        self.assertInHTML(
            """
            <input type="text" name="tag_string" value="tag1 tag2 tag3" 
                aria-describedby="id_tag_string_help" autocapitalize="off" autocomplete="off" class="form-input" id="id_tag_string">
            """,
            html,
        )

    def test_should_prefill_notes_from_url_parameter(self):
        response = self.client.get(
            reverse("linkding:bookmarks.new")
            + "?notes=%2A%2AFind%2A%2A%20more%20info%20%5Bhere%5D%28http%3A%2F%2Fexample.com%29"
        )
        html = response.content.decode()

        self.assertInHTML(
            """
            <details class="notes" open>
                <summary>
                    <span class="form-label d-inline-block">Notes</span>
                </summary>
                <label for="id_notes" class="text-assistive">Notes</label>
                <textarea name="notes" cols="40" rows="8" aria-describedby="id_notes_help" class="form-input" id="id_notes">**Find** more info [here](http://example.com)</textarea>
                <div id="id_notes_help" class="form-input-hint">
                    Additional notes. Markdown is supported.
                </div>
            </details>
            """,
            html,
        )

    def test_should_enable_auto_close_when_specified_in_url_parameter(self):
        response = self.client.get(reverse("linkding:bookmarks.new") + "?auto_close")
        html = response.content.decode()

        self.assertInHTML(
            '<input type="hidden" name="auto_close" value="True" id="id_auto_close">',
            html,
        )

    def test_should_not_enable_auto_close_when_not_specified_in_url_parameter(self):
        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()

        self.assertInHTML(
            '<input type="hidden" name="auto_close" value="False" id="id_auto_close">',
            html,
        )

    def test_should_redirect_to_index_view(self):
        form_data = self.create_form_data()

        response = self.client.post(reverse("linkding:bookmarks.new"), form_data)

        self.assertRedirects(response, reverse("linkding:bookmarks.index"))

    def test_should_not_redirect_to_external_url(self):
        form_data = self.create_form_data()

        response = self.client.post(
            reverse("linkding:bookmarks.new") + "?return_url=https://example.com",
            form_data,
        )

        self.assertRedirects(response, reverse("linkding:bookmarks.index"))

    def test_auto_close_should_redirect_to_close_view(self):
        form_data = self.create_form_data({"auto_close": "True"})

        response = self.client.post(reverse("linkding:bookmarks.new"), form_data)

        self.assertRedirects(response, reverse("linkding:bookmarks.close"))

    def test_should_respect_share_profile_setting(self):
        self.user.profile.enable_sharing = False
        self.user.profile.save()
        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()

        self.assertInHTML(
            """
            <div class="form-checkbox">
                <input type="checkbox" name="shared" aria-describedby="id_shared_help" id="id_shared">
                <i class="form-icon"></i>
                <label for="id_shared">Share</label>
            </div>          
            """,
            html,
            count=0,
        )

        self.user.profile.enable_sharing = True
        self.user.profile.save()
        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()

        self.assertInHTML(
            """
            <div class="form-checkbox">
                <input type="checkbox" name="shared" aria-describedby="id_shared_help" id="id_shared">
                <i class="form-icon"></i>
                <label for="id_shared">Share</label>
            </div>              
            """,
            html,
            count=1,
        )

    def test_prefetch_favicon_should_return_cached_stale_file_without_downloading(self):
        self.user.profile.enable_favicons = True
        self.user.profile.save()
        with mock.patch.object(
            favicon_loader, "get_cached_favicon"
        ) as mock_get_cached_favicon, mock.patch.object(
            favicon_loader, "load_favicon"
        ) as mock_load_favicon:
            mock_get_cached_favicon.return_value = favicon_loader.CachedFavicon(
                filename="https_example_com.png",
                is_stale=True,
            )

            response = self.client.get(
                reverse("linkding:bookmarks.prefetch_favicon")
                + "?url=https://example.com"
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["favicon_file"], "https_example_com.png")
            mock_load_favicon.assert_not_called()

    def test_prefetch_favicon_should_download_when_cache_is_missing(self):
        self.user.profile.enable_favicons = True
        self.user.profile.save()
        with mock.patch.object(
            favicon_loader, "get_cached_favicon", return_value=None
        ) as mock_get_cached_favicon, mock.patch.object(
            favicon_loader, "load_favicon", return_value="https_example_com.png"
        ) as mock_load_favicon:
            response = self.client.get(
                reverse("linkding:bookmarks.prefetch_favicon")
                + "?url=https://example.com"
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["favicon_file"], "https_example_com.png")
            mock_get_cached_favicon.assert_called_once_with("https://example.com")
            mock_load_favicon.assert_called_once_with("https://example.com", timeout=5)

    def test_should_show_respective_share_hint(self):
        self.user.profile.enable_sharing = True
        self.user.profile.save()

        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()
        self.assertInHTML(
            """
              <div id="id_shared_help" class="form-input-hint">
                  Share this bookmark with registered users.
              </div>
            """,
            html,
        )

        self.user.profile.enable_public_sharing = True
        self.user.profile.save()

        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()
        self.assertInHTML(
            """
              <div id="id_shared_help" class="form-input-hint">
                  Share this bookmark with registered users and anonymous visitors.
              </div>
            """,
            html,
        )

    def test_should_hide_notes_if_there_are_no_notes(self):
        bookmark = self.setup_bookmark()
        response = self.client.get(
            reverse("linkding:bookmarks.edit", args=[bookmark.id])
        )

        self.assertContains(response, '<details class="notes">', count=1)

    def test_should_not_check_unread_by_default(self):
        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()

        self.assertInHTML(
            '<input type="checkbox" name="unread" id="id_unread" aria-describedby="id_unread_help">',
            html,
        )

    def test_should_check_unread_when_configured_in_profile(self):
        self.user.profile.default_mark_unread = True
        self.user.profile.save()

        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()

        self.assertInHTML(
            '<input type="checkbox" name="unread" id="id_unread" checked="" aria-describedby="id_unread_help">',
            html,
        )

    def test_should_not_check_shared_by_default(self):
        self.user.profile.enable_sharing = True
        self.user.profile.save()

        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()

        self.assertInHTML(
            '<input type="checkbox" name="shared" id="id_shared" aria-describedby="id_shared_help">',
            html,
        )

    def test_should_check_shared_when_configured_in_profile(self):
        self.user.profile.enable_sharing = True
        self.user.profile.default_mark_shared = True
        self.user.profile.save()

        response = self.client.get(reverse("linkding:bookmarks.new"))
        html = response.content.decode()

        self.assertInHTML(
            '<input type="checkbox" name="shared" id="id_shared" checked="" aria-describedby="id_shared_help">',
            html,
        )
