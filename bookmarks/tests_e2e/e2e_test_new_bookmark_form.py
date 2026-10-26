from unittest.mock import patch
from urllib.parse import quote

from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.models import Bookmark
from bookmarks.services import website_loader
from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase

mock_website_metadata = website_loader.WebsiteMetadata(
    url="https://example.com",
    title="Example Domain",
    description="This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission.",
    preview_image=None,
)


class BookmarkFormE2ETestCase(LinkdingE2ETestCase):
    def setUp(self) -> None:
        super().setUp()
        self.website_loader_patch = patch.object(
            website_loader, "load_website_metadata", return_value=mock_website_metadata
        )
        self.website_loader_mock = self.website_loader_patch.start()

    def tearDown(self) -> None:
        super().tearDown()
        self.website_loader_patch.stop()

    def test_enter_url_prefills_title_and_description(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url = page.get_by_label("URL")
            title = page.get_by_label("Title")
            description = page.locator("#id_description")

            url.fill("https://example.com")
            expect(title).to_have_value("Example Domain")
            expect(description).to_have_value(
                "This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission."
            )

    def test_prefilled_bookmarklet_metadata_is_rewritten_by_server(self):
        rewriter_metadata = website_loader.WebsiteMetadata(
            url="https://example.com",
            title="Rewritten Title",
            description="Rewritten description.",
            preview_image=None,
        )
        with patch.object(
            website_loader, "rewrite_website_metadata", return_value=rewriter_metadata
        ) as mock_rewrite:
            with sync_playwright() as p:
                bookmarklet_url = (
                    reverse("linkding:bookmarks.new")
                    + "?url=https%3A%2F%2Fexample.com"
                    + "&title=Client%20title"
                    + "&description=Client%20description"
                    + "&from_client=1"
                )
                page = self.open(bookmarklet_url, p)
                title = page.get_by_label("Title")
                description = page.locator("#id_description")

                page.wait_for_timeout(timeout=1000)

                # 浏览器 bookmarklet 预填的元数据应交给服务器重写后使用
                expect(title).to_have_value("Rewritten Title")
                expect(description).to_have_value("Rewritten description.")
                mock_rewrite.assert_called()

    def test_enter_url_does_not_overwrite_modified_title_and_description(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url = page.get_by_label("URL")
            title = page.get_by_label("Title")
            description = page.locator("#id_description")

            title.fill("Modified title")
            # Open the description details element so the textarea is visible
            page.locator("details.description summary").click()
            description.fill("Modified description")
            url.fill("https://example.com")
            page.wait_for_timeout(timeout=1000)

            expect(title).to_have_value("Modified title")
            expect(description).to_have_value("Modified description")

    def test_with_initial_url_prefills_title_and_description(self):
        with sync_playwright() as p:
            page_url = (
                reverse("linkding:bookmarks.new")
                + f"?url={quote('https://example.com')}"
            )
            page = self.open(page_url, p)
            url = page.get_by_label("URL")
            title = page.get_by_label("Title")
            description = page.locator("#id_description")

            page.wait_for_timeout(timeout=1000)

            expect(url).to_have_value("https://example.com")
            expect(title).to_have_value("Example Domain")
            expect(description).to_have_value(
                "This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission."
            )

    def test_with_initial_url_title_description_does_not_overwrite_title_and_description(
        self,
    ):
        with sync_playwright() as p:
            page_url = (
                reverse("linkding:bookmarks.new")
                + f"?url={quote('https://example.com')}&title=Initial+title&description=Initial+description"
            )
            page = self.open(page_url, p)
            url = page.get_by_label("URL")
            title = page.get_by_label("Title")
            description = page.locator("#id_description")

            page.wait_for_timeout(timeout=1000)

            expect(url).to_have_value("https://example.com")
            expect(title).to_have_value("Initial title")
            expect(description).to_have_value("Initial description")

    def test_create_should_check_for_existing_bookmark(self):
        existing_bookmark = self.setup_bookmark(
            title="Existing title",
            description="Existing description",
            notes="Existing notes",
            tags=[self.setup_tag(name="tag1"), self.setup_tag(name="tag2")],
            unread=True,
        )
        tag_names = " ".join(existing_bookmark.tag_names)

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)

            # Enter bookmarked URL
            page.get_by_label("URL").fill(existing_bookmark.url)
            # Already bookmarked hint should be visible
            page.get_by_text("This URL is already bookmarked.").wait_for(timeout=2000)
            # Form should be pre-filled with data from existing bookmark
            self.assertEqual(
                existing_bookmark.title, page.get_by_label("Title").input_value()
            )
            self.assertEqual(
                existing_bookmark.description,
                page.locator("#id_description").input_value(),
            )
            self.assertEqual(
                existing_bookmark.notes, page.get_by_label("Notes").input_value()
            )
            self.assertEqual(tag_names, page.get_by_label("Tags").input_value())
            self.assertTrue(tag_names, page.get_by_label("Mark as unread").is_checked())

            # Enter non-bookmarked URL
            page.get_by_label("URL").fill("https://example.com/unknown")
            # Already bookmarked hint should be hidden
            page.get_by_text("This URL is already bookmarked.").wait_for(
                state="hidden", timeout=2000
            )

    def test_enter_url_of_existing_bookmark_should_show_notes(self):
        bookmark = self.setup_bookmark(
            notes="Existing notes", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)

            details = page.locator("details.notes")
            expect(details).not_to_have_attribute("open", value="")

            page.get_by_label("URL").fill(bookmark.url)
            expect(details).to_have_attribute("open", value="")

    def test_create_should_preview_auto_tags(self):
        profile = self.get_or_create_test_user().profile
        profile.auto_tagging_rules = "github.com dev github"
        profile.save()

        with sync_playwright() as p:
            # Open page with URL that should have auto tags
            url = (
                reverse("linkding:bookmarks.new")
                + "?url=https%3A%2F%2Fgithub.com%2Fsissbruecker%2Flinkding"
            )
            page = self.open(url, p)

            auto_tags_hint = page.locator(".form-input-hint.auto-tags")
            expect(auto_tags_hint).to_be_visible()
            expect(auto_tags_hint).to_have_text("Auto tags: dev github")

            # Change to URL without auto tags
            page.get_by_label("URL").fill("https://example.com")

            expect(auto_tags_hint).to_be_hidden()

    def test_clear_buttons_only_shown_when_fields_have_content(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)

            title_field = page.get_by_label("Title")
            title_clear_button = page.locator("ld-clear-button[data-for='id_title']")
            description_field = page.locator("#id_description")
            description_clear_button = page.locator(
                "ld-clear-button[data-for='id_description']"
            )

            # Initially, clear buttons should be hidden because fields are empty
            expect(title_clear_button).to_be_hidden()
            expect(description_clear_button).to_be_hidden()

            # Add content to title field, its clear button should become visible
            title_field.fill("Test title")
            expect(title_clear_button).to_be_visible()

            # Open the description details so the textarea is visible
            page.locator("details.description summary").click()
            # Add content to description field, its clear button should become visible
            description_field.fill("Test description")
            expect(description_clear_button).to_be_visible()

            # Clear title field, its clear button should be hidden again
            title_field.fill("")
            expect(title_clear_button).to_be_hidden()

            # Clear description field, its clear button should be hidden again
            description_field.fill("")
            expect(description_clear_button).to_be_hidden()

    def test_refresh_button_shown_once_url_is_entered(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            refresh_button = page.locator("#refresh-button")

            # 没有 URL 时没有可抓取的对象，按钮不出现
            expect(refresh_button).to_be_hidden()

            # 未收藏的新 URL 也能重抓（自动预填失败或想强制刷新时的手动入口）
            url_field.fill("https://example.com/not-bookmarked")
            expect(refresh_button).to_be_visible()

            # 清空 URL 后回到隐藏
            url_field.fill("")
            expect(refresh_button).to_be_hidden()

    def test_refresh_from_website_button_updates_title_and_description(self):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            title_field = page.get_by_label("Title")
            description_field = page.locator("#id_description")
            refresh_button = page.locator("#refresh-button")

            # Enter the URL of the existing bookmark to make refresh button visible
            url_field.fill(existing_bookmark.url)

            # Wait for metadata to be loaded and for refresh button to be visible
            expect(refresh_button).to_be_visible()
            expect(title_field).to_have_value("Existing title")
            expect(description_field).to_have_value("Existing description")

            # Update the mock to return different metadata when refresh is requested
            self.website_loader_mock.reset_mock()
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url="https://example.com",
                title="Updated Example Domain",
                description="This is a refreshed description for the example domain.",
                preview_image=None,
            )

            # Click the refresh button
            refresh_button.click()

            # Verify that title and description have been updated with new values
            expect(title_field).to_have_value("Updated Example Domain")
            expect(description_field).to_have_value(
                "This is a refreshed description for the example domain."
            )

            # Verify that the fields are visually marked as modified
            expect(title_field).to_have_class("form-input modified")
            expect(description_field).to_have_class("form-input modified")

    def test_refresh_from_website_button_does_not_modify_fields_if_metadata_is_same(
        self,
    ):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            title_field = page.get_by_label("Title")
            description_field = page.locator("#id_description")
            refresh_button = page.locator("#refresh-button")

            # Update mock to use the bookmark URL so checkUrl doesn't change the URL input
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url=existing_bookmark.url,
                title="Example Domain",
                description="This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission.",
                preview_image=None,
            )

            # Enter the URL of the existing bookmark to make refresh button visible
            url_field.fill(existing_bookmark.url)

            # Wait for metadata to be loaded and for refresh button to be visible
            expect(refresh_button).to_be_visible()
            expect(title_field).to_have_value("Existing title")
            expect(description_field).to_have_value("Existing description")

            # Update the mock to return same metadata when refresh is requested
            self.website_loader_mock.reset_mock()
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url=existing_bookmark.url,
                title="Existing title",
                description="Existing description",
                preview_image=None,
            )

            # Click the refresh button
            refresh_button.click()
            page.wait_for_timeout(timeout=1000)

            # Verify that title and description values are still the same
            expect(title_field).to_have_value("Existing title")
            expect(description_field).to_have_value("Existing description")

            # Verify that the fields are NOT visually marked as modified
            expect(title_field).to_have_class("form-input")
            expect(description_field).to_have_class("form-input")

    def test_refresh_from_website_button_keeps_preview_image_when_metadata_has_none(
        self,
    ):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )
        existing_bookmark.preview_image_remote_url = "https://example.com/original.png"
        existing_bookmark.save(update_fields=["preview_image_remote_url"])
        self.get_or_create_test_user().profile.enable_preview_images = True
        self.get_or_create_test_user().profile.save()

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            preview_image_field = page.locator("#id_preview_image_remote_url")
            refresh_button = page.locator("#refresh-button")

            url_field.fill(existing_bookmark.url)
            expect(refresh_button).to_be_visible()
            expect(preview_image_field).to_have_value(
                "https://example.com/original.png"
            )

            self.website_loader_mock.reset_mock()
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url=existing_bookmark.url,
                title="Existing title",
                description="Existing description",
                preview_image=None,
            )

            refresh_button.click()
            page.wait_for_timeout(timeout=1000)

            # 抓不到预览图时保留旧图，不清空
            expect(preview_image_field).to_have_value(
                "https://example.com/original.png"
            )

    def test_refresh_from_website_button_allows_undoing_overwritten_field(self):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            title_field = page.get_by_label("Title")
            refresh_button = page.locator("#refresh-button")
            undo_button = page.locator('[data-restore-for="id_title"]')

            url_field.fill(existing_bookmark.url)
            expect(refresh_button).to_be_visible()
            expect(undo_button).to_be_hidden()

            title_field.fill("Manually typed title")
            expect(undo_button).to_be_hidden()

            self.website_loader_mock.reset_mock()
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url=existing_bookmark.url,
                title="Site title",
                description="Site description",
                preview_image=None,
            )

            refresh_button.click()
            expect(title_field).to_have_value("Site title")
            expect(undo_button).to_be_visible()

            undo_button.click()
            expect(title_field).to_have_value("Manually typed title")
            expect(undo_button).to_be_hidden()
            # 撤销后不再是被抓取内容覆盖的状态
            expect(title_field).to_have_class("form-input")

            # 撤销后再次手改，按钮不应重新出现（快照已失效）
            title_field.fill("Another manual title")
            expect(undo_button).to_be_hidden()

    def test_refresh_from_website_button_allows_undoing_overwritten_description(self):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            description_field = page.locator("#id_description")
            description_details = page.locator("details.description")
            refresh_button = page.locator("#refresh-button")
            undo_button = page.locator('[data-restore-for="id_description"]')

            url_field.fill(existing_bookmark.url)
            expect(refresh_button).to_be_visible()
            expect(description_details).to_have_js_property("open", True)

            description_field.fill("Manually typed description")

            self.website_loader_mock.reset_mock()
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url=existing_bookmark.url,
                title="Existing title",
                description="Site description",
                preview_image=None,
            )

            refresh_button.click()
            expect(description_field).to_have_value("Site description")
            expect(undo_button).to_be_visible()

            # 撤销按钮在 summary 里，点击不应顺带折叠描述区块
            undo_button.click()
            expect(description_field).to_have_value("Manually typed description")
            expect(undo_button).to_be_hidden()
            expect(description_details).to_have_js_property("open", True)

    def test_refresh_actions_are_placed_in_page_header(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)

            # 抓取覆盖多个字段，动作放在页头；字段行里只保留字段级撤销
            expect(page.locator(".section-header #refresh-button")).to_have_count(1)
            expect(page.locator(".section-header #undo-refresh-button")).to_have_count(1)
            expect(page.locator(".form-group #refresh-button")).to_have_count(0)

    def test_undo_refresh_button_reverts_all_overwritten_fields(self):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            title_field = page.get_by_label("Title")
            description_field = page.locator("#id_description")
            refresh_button = page.locator("#refresh-button")
            undo_refresh_button = page.locator("#undo-refresh-button")

            url_field.fill(existing_bookmark.url)
            expect(refresh_button).to_be_visible()
            expect(undo_refresh_button).to_be_hidden()

            title_field.fill("Manually typed title")
            description_field.fill("Manually typed description")

            self.website_loader_mock.reset_mock()
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url=existing_bookmark.url,
                title="Site title",
                description="Site description",
                preview_image=None,
            )

            refresh_button.click()
            expect(title_field).to_have_value("Site title")
            expect(description_field).to_have_value("Site description")
            expect(undo_refresh_button).to_be_visible()

            undo_refresh_button.click()
            expect(title_field).to_have_value("Manually typed title")
            expect(description_field).to_have_value("Manually typed description")
            expect(undo_refresh_button).to_be_hidden()
            expect(page.locator('[data-restore-for="id_title"]')).to_be_hidden()
            expect(page.locator('[data-restore-for="id_description"]')).to_be_hidden()

    def test_undo_refresh_button_hides_when_undoing_single_field(self):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            title_field = page.get_by_label("Title")
            refresh_button = page.locator("#refresh-button")
            undo_refresh_button = page.locator("#undo-refresh-button")

            url_field.fill(existing_bookmark.url)
            expect(refresh_button).to_be_visible()
            # 等自动预填落定，否则抓取会在标题还空着时先跑，撤销回去的是空串
            expect(title_field).to_have_value("Existing title")

            self.website_loader_mock.reset_mock()
            # URL 用站点检查后输入框里的值，避免抓取顺带改写 URL 又产生一份快照
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url="https://example.com",
                title="Site title",
                description="Existing description",
                preview_image=None,
            )

            refresh_button.click()
            expect(title_field).to_have_value("Site title")
            expect(undo_refresh_button).to_be_visible()

            # 只有标题被覆盖，字段级撤销后页头按钮随之消失
            page.locator('[data-restore-for="id_title"]').click()
            expect(title_field).to_have_value("Existing title")
            expect(undo_refresh_button).to_be_hidden()

    def test_refresh_from_website_button_compares_against_form_values(
        self,
    ):
        existing_bookmark = self.setup_bookmark(
            title="Existing title", description="Existing description"
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            title_field = page.get_by_label("Title")
            refresh_button = page.locator("#refresh-button")

            url_field.fill(existing_bookmark.url)
            expect(refresh_button).to_be_visible()
            expect(title_field).to_have_value("Existing title")

            # 用户手改标题后，站点值虽与库中一致，也应恢复为站点值
            title_field.fill("Manually edited")

            self.website_loader_mock.reset_mock()
            self.website_loader_mock.return_value = website_loader.WebsiteMetadata(
                url=existing_bookmark.url,
                title="Existing title",
                description="Existing description",
                preview_image=None,
            )

            refresh_button.click()
            page.wait_for_timeout(timeout=1000)

            expect(title_field).to_have_value("Existing title")

    def test_ctrl_enter_submits_form_from_description(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)
            url_field = page.get_by_label("URL")
            description_field = page.locator("#id_description")

            url_field.fill("https://example.com")
            description_field.fill("Test description")
            description_field.focus()

            # Press Ctrl+Enter to submit form
            description_field.press("Control+Enter")

            # Should navigate away from new bookmark page after successful submission
            expect(page).not_to_have_url(
                self.live_server_url + reverse("linkding:bookmarks.new")
            )

        self.assertEqual(1, Bookmark.objects.count())
        bookmark = Bookmark.objects.first()
        self.assertEqual("https://example.com", bookmark.url)
        self.assertEqual("Example Domain", bookmark.title)

    def test_save_navigates_to_bookmark_index(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.new"), p)

            page.get_by_label("URL").fill("https://example.com")
            page.locator("input[type='submit']").click()

            expect(page).to_have_url(
                self.live_server_url + reverse("linkding:bookmarks.index")
            )
