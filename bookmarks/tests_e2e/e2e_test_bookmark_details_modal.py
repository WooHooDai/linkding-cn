import re
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.models import Bookmark
from bookmarks.services import website_loader
from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class BookmarkDetailsModalE2ETestCase(LinkdingE2ETestCase):
    def test_show_details(self):
        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)

            details_modal = self.open_details_modal(bookmark)
            title = details_modal.locator("h2")
            expect(title).to_have_text(bookmark.title)

    def test_close_details(self):
        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)

            # close with close button
            details_modal = self.open_details_modal(bookmark)
            details_modal.locator("button.close").click()
            expect(details_modal).to_be_hidden()

            # close with backdrop
            details_modal = self.open_details_modal(bookmark)
            overlay = details_modal.locator(".modal-overlay")
            overlay.click(position={"x": 0, "y": 0})
            expect(details_modal).to_be_hidden()

            # close with escape
            details_modal = self.open_details_modal(bookmark)
            self.page.keyboard.press("Escape")
            expect(details_modal).to_be_hidden()

    def test_toggle_archived(self):
        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            # archive
            url = reverse("linkding:bookmarks.index")
            self.open(url, p)

            details_modal = self.open_details_modal(bookmark)
            details_modal.get_by_role("checkbox", name="Archived").click()
            expect(self.locate_bookmark(bookmark.title)).not_to_be_visible()
            self.assertReloads(0)

            # unarchive
            url = reverse("linkding:bookmarks.archived")
            self.page.goto(self.live_server_url + url)
            self.resetReloads()

            details_modal = self.open_details_modal(bookmark)
            details_modal.get_by_role("checkbox", name="Archived").click()
            expect(self.locate_bookmark(bookmark.title)).not_to_be_visible()
            self.assertReloads(0)

    def test_toggle_unread(self):
        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            # mark as unread
            url = reverse("linkding:bookmarks.index")
            self.open(url, p)

            details_modal = self.open_details_modal(bookmark)

            details_modal.get_by_role("checkbox", name="Unread").click()
            bookmark_item = self.locate_bookmark(bookmark.title)
            expect(bookmark_item).to_have_class("unread")
            self.assertReloads(0)

            # mark as read
            details_modal.get_by_role("checkbox", name="Unread").click()
            bookmark_item = self.locate_bookmark(bookmark.title)
            expect(bookmark_item).not_to_have_class("unread")
            self.assertReloads(0)

    def test_toggle_shared(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.save()

        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            # share bookmark
            url = reverse("linkding:bookmarks.index")
            self.open(url, p)

            details_modal = self.open_details_modal(bookmark)

            details_modal.get_by_role("checkbox", name="Shared").click()
            bookmark_item = self.locate_bookmark(bookmark.title)
            expect(bookmark_item).to_have_class("shared")
            self.assertReloads(0)

            # unshare bookmark
            details_modal.get_by_role("checkbox", name="Shared").click()
            bookmark_item = self.locate_bookmark(bookmark.title)
            expect(bookmark_item).not_to_have_class("shared")
            self.assertReloads(0)

    def test_edit_return_url(self):
        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index") + f"?q={bookmark.title}"
            self.open(url, p)

            details_modal = self.open_details_modal(bookmark)

            # Navigate to edit page
            with self.page.expect_navigation():
                details_modal.get_by_text("Edit").click()

            # Cancel edit, verify return to details url
            details_url = url + f"&details={bookmark.id}"
            with self.page.expect_navigation(url=self.live_server_url + details_url):
                self.page.get_by_text("Cancel").click()

    def test_delete(self):
        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index") + f"?q={bookmark.title}"
            self.open(url, p)

            details_modal = self.open_details_modal(bookmark)

            # Delete bookmark, verify return url
            details_modal.get_by_text("Delete...").click()
            self.page.get_by_text("Confirm").wait_for(state="visible")
            with self.page.expect_navigation(url=self.live_server_url + url):
                self.page.get_by_text("Confirm").click()

            # verify bookmark is deleted
            expect(self.locate_bookmark(bookmark.title)).not_to_be_visible()

        bookmark.refresh_from_db()
        self.assertTrue(bookmark.is_deleted)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_snapshot_remove_snapshot(self):
        bookmark = self.setup_bookmark()

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index") + f"?q={bookmark.title}"
            self.open(url, p)

            details_modal = self.open_details_modal(bookmark)
            asset_list = details_modal.locator(".assets")

            # No snapshots initially
            snapshot = asset_list.get_by_text(
                re.compile("snapshot|快照", re.IGNORECASE)
            )
            expect(snapshot).not_to_be_visible()

            # Create snapshot
            details_modal.get_by_text("Create HTML snapshot", exact=False).click()
            self.assertReloads(0)

            # Has new snapshots
            expect(snapshot).to_be_visible()

            # Remove snapshot
            asset_list.get_by_text("Remove", exact=False).click()
            self.page.get_by_text("Confirm", exact=False).click()

            # Snapshot is removed
            expect(snapshot).not_to_be_visible()
            self.assertReloads(0)

    def test_refresh_metadata_applies_and_can_be_undone(self):
        bookmark = self.setup_bookmark(
            title="Old title", description="Old description"
        )
        refreshed = website_loader.WebsiteMetadata(
            url=bookmark.url,
            title="Site title",
            description="Site description",
            author=None,
            preview_image=None,
        )

        with patch.object(
            website_loader, "load_website_metadata", return_value=refreshed
        ):
            with sync_playwright() as p:
                self.open(reverse("linkding:bookmarks.index"), p)
                modal = self.open_details_modal(bookmark)
                refresh_btn = modal.locator("#refresh-metadata-btn")
                undo_btn = modal.locator("#undo-metadata-btn")
                title = modal.locator(".bookmark-title-input")
                description = modal.locator('.detail-textarea[data-field="description"]')

                # 动作常驻底部操作栏，不再依赖 URL 编辑态
                expect(refresh_btn).to_be_visible()
                expect(undo_btn).to_be_hidden()
                expect(modal.locator(".detail-url-edit #refresh-metadata-btn")).to_have_count(0)

                refresh_btn.click()

                expect(title).to_have_value("Site title")
                expect(description).to_have_value("Site description")
                expect(undo_btn).to_be_visible()
                expect(title).to_have_class(re.compile(r"is-metadata-modified"))
                # 字段行内出现各自的撤销按钮
                expect(modal.locator('[data-undo-field="title"]')).to_be_visible()
                expect(modal.locator('[data-undo-field="description"]')).to_be_visible()
                # 列表项就地同步，不整页刷新
                expect(self.locate_bookmark("Site title")).to_be_visible()
                self.assertReloads(0)

                # 字段级撤销：只回滚标题，描述保持站点值
                modal.locator('[data-undo-field="title"]').click()
                expect(title).to_have_value("Old title")
                expect(description).to_have_value("Site description")
                expect(undo_btn).to_be_visible()
                expect(title).not_to_have_class(re.compile(r"is-metadata-modified"))

                # 整体撤销：剩余字段全部还原
                undo_btn.click()
                expect(description).to_have_value("Old description")
                expect(undo_btn).to_be_hidden()
                expect(modal.locator('[data-undo-field="description"]')).to_be_hidden()

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "Old title")
        self.assertEqual(bookmark.description, "Old description")

    def test_refresh_metadata_writes_to_database(self):
        bookmark = self.setup_bookmark(title="Old title", description="Old description")
        refreshed = website_loader.WebsiteMetadata(
            url=bookmark.url,
            title="Site title",
            description="Site description",
            author=None,
            preview_image=None,
        )

        with patch.object(
            website_loader, "load_website_metadata", return_value=refreshed
        ):
            with sync_playwright() as p:
                self.open(reverse("linkding:bookmarks.index"), p)
                modal = self.open_details_modal(bookmark)
                modal.locator("#refresh-metadata-btn").click()
                expect(modal.locator(".bookmark-title-input")).to_have_value(
                    "Site title"
                )

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "Site title")
        self.assertEqual(bookmark.description, "Site description")

    def test_refresh_metadata_failure_shows_error_toast(self):
        bookmark = self.setup_bookmark(title="Old title")

        with patch.object(
            website_loader,
            "load_website_metadata",
            side_effect=website_loader.NonRetryableMetadataError("boom"),
        ):
            with sync_playwright() as p:
                self.open(reverse("linkding:bookmarks.index"), p)
                modal = self.open_details_modal(bookmark)
                modal.locator("#refresh-metadata-btn").click()
                expect(self.page.locator(".toast-stack .toast-error")).to_be_visible()
                expect(modal.locator("#undo-metadata-btn")).to_be_hidden()

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "Old title")

    def test_edit_url_saves_on_blur_and_refreshes_metadata(self):
        bookmark = self.setup_bookmark(title="Old title", url="https://example.com/old")
        refreshed = website_loader.WebsiteMetadata(
            url="https://example.com/new",
            title="Site title",
            description="Site description",
            author=None,
            preview_image=None,
        )

        with patch.object(
            website_loader, "load_website_metadata", return_value=refreshed
        ):
            with sync_playwright() as p:
                self.open(reverse("linkding:bookmarks.index"), p)
                modal = self.open_details_modal(bookmark)

                modal.locator("#edit-url-btn").click()
                url_input = modal.locator(".detail-url-input")
                expect(url_input).to_be_visible()
                # 旧交互的保存/取消按钮已移除
                expect(modal.locator("#url-save-btn")).to_have_count(0)

                url_input.fill("https://example.com/new")
                url_input.blur()

                # URL 就地更新，且变化触发了重新抓取
                expect(modal.locator(".detail-url-link")).to_have_text(
                    "https://example.com/new"
                )
                expect(modal.locator(".bookmark-title-input")).to_have_value(
                    "Site title"
                )
                expect(modal.locator("#undo-metadata-btn")).to_be_visible()

                # URL 修改本身也可撤销
                url_undo = modal.locator('[data-undo-field="url"]')
                expect(url_undo).to_be_visible()
                url_undo.click()
                expect(modal.locator(".detail-url-link")).to_have_text(
                    "https://example.com/old"
                )
                expect(url_undo).to_be_hidden()

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.url, "https://example.com/old")
        self.assertEqual(bookmark.title, "Site title")
