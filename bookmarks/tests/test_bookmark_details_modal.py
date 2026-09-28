import datetime

import re

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import formats, timezone

from bookmarks.models import BookmarkAsset, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class BookmarkDetailsModalTestCase(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def get_index_details_modal(self, bookmark):
        url = reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        response = self.client.get(url)
        soup = self.make_soup(response.content.decode())
        return soup.select_one("ld-details-modal")

    def get_shared_details_modal(self, bookmark):
        url = reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        response = self.client.get(url)
        soup = self.make_soup(response.content.decode())
        return soup.select_one("ld-details-modal")

    def has_details_modal(self, response):
        soup = self.make_soup(response.content.decode())
        return soup.select_one("ld-details-modal") is not None

    def find_section(self, soup, label_text):
        """Find a detail-section by its label text."""
        for label in soup.find_all(["div", "span"], {"class": "detail-label"}):
            if label.text.strip() == label_text:
                return label.find_parent("div", {"class": "detail-section"})
        return None

    def find_weblink(self, soup, url):
        return soup.find("a", {"class": "weblink", "href": url})

    def count_weblinks(self, soup):
        return len(soup.find_all("a", {"class": "weblink"}))

    def find_asset(self, soup, asset):
        return soup.find("div", {"data-asset-id": asset.id})

    # ---- Access ----

    def test_access(self):
        # own bookmark
        bookmark = self.setup_bookmark()
        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.has_details_modal(response))

        # other user's bookmark
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(user=other_user)
        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.has_details_modal(response))

        # non-existent bookmark
        response = self.client.get(
            reverse("linkding:bookmarks.index") + "?details=9999"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.has_details_modal(response))

        # guest user
        self.client.logout()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.has_details_modal(response))

    def test_access_with_sharing(self):
        other_user = self.setup_user()
        bookmark = self.setup_bookmark(shared=True, user=other_user)

        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertFalse(self.has_details_modal(response))

        profile = other_user.profile
        profile.enable_sharing = True
        profile.save()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertTrue(self.has_details_modal(response))

        self.client.logout()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertFalse(self.has_details_modal(response))

        profile.enable_public_sharing = True
        profile.save()
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + f"?details={bookmark.id}"
        )
        self.assertTrue(self.has_details_modal(response))

    # ---- Title ----

    def test_displays_title(self):
        # with title
        bookmark = self.setup_bookmark(title="Test title")
        soup = self.get_index_details_modal(bookmark)
        title_el = soup.find("textarea", {"class": "bookmark-title-input"})
        self.assertIsNotNone(title_el)
        self.assertEqual(title_el.text.strip(), bookmark.title)

        # with URL only
        bookmark = self.setup_bookmark(title="")
        soup = self.get_index_details_modal(bookmark)
        title_el = soup.find("textarea", {"class": "bookmark-title-input"})
        self.assertIsNotNone(title_el)
        self.assertEqual(title_el.text.strip(), bookmark.url)

    # ---- Weblinks ----

    def test_website_link(self):
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        link = soup.find("a", {"class": "detail-url-link"})
        self.assertIsNotNone(link)
        self.assertEqual(link["href"], bookmark.url)
        self.assertIn(bookmark.url, link.text)

        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        wrapper = soup.find("div", {"class": "detail-url-view"})
        image = wrapper.select_one("img.favicon")
        self.assertIsNotNone(image)
        self.assertIn("/favicon/", image["src"])

    def test_reader_mode_link(self):
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        # URL is on its own line, weblinks has reader mode + internet archive
        self.assertEqual(self.count_weblinks(soup), 2)
        reader_mode_url = reverse("linkding:bookmarks.read", args=[bookmark.id])
        link = self.find_weblink(soup, reader_mode_url)
        self.assertIsNotNone(link)

    def test_internet_archive_link_with_snapshot_url(self):
        bookmark = self.setup_bookmark(web_archive_snapshot_url="https://example.com/")
        soup = self.get_index_details_modal(bookmark)
        link = self.find_weblink(soup, bookmark.web_archive_snapshot_url)
        self.assertIsNotNone(link)
        self.assertEqual(link["href"], bookmark.web_archive_snapshot_url)
        self.assertEqual(link.text.strip(), "Internet Archive")

    def test_internet_archive_link_with_fallback_url(self):
        date_added = timezone.datetime(2023, 8, 11, 21, 45, 11, tzinfo=datetime.UTC)
        bookmark = self.setup_bookmark(url="https://example.com/", added=date_added)
        fallback_url = "https://web.archive.org/web/20230811214511/https://example.com/"
        soup = self.get_index_details_modal(bookmark)
        link = self.find_weblink(soup, fallback_url)
        self.assertIsNotNone(link)

    def test_weblinks_respect_target_setting(self):
        bookmark = self.setup_bookmark(web_archive_snapshot_url="https://example.com/")
        profile = self.get_or_create_test_user().profile
        profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_BLANK
        profile.save()
        soup = self.get_index_details_modal(bookmark)
        website_link = soup.find("a", {"class": "detail-url-link"})
        self.assertEqual(website_link["target"], UserProfile.BOOKMARK_LINK_TARGET_BLANK)

        web_archive_link = self.find_weblink(soup, bookmark.web_archive_snapshot_url)
        self.assertEqual(web_archive_link["target"], UserProfile.BOOKMARK_LINK_TARGET_BLANK)

        profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_SELF
        profile.save()
        soup = self.get_index_details_modal(bookmark)
        website_link = soup.find("a", {"class": "detail-url-link"})
        self.assertEqual(website_link["target"], UserProfile.BOOKMARK_LINK_TARGET_SELF)

    # ---- Preview image ----

    def test_preview_image(self):
        # without image
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNone(image)

        # with image, preview disabled
        bookmark = self.setup_bookmark(preview_image_file="example.png")
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNone(image)

        # preview enabled, no image
        profile = self.get_or_create_test_user().profile
        profile.enable_preview_images = True
        profile.save()
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNone(image)

        # preview enabled, image present
        bookmark = self.setup_bookmark(preview_image_file="example.png")
        soup = self.get_index_details_modal(bookmark)
        image = soup.select_one(".info-preview-image")
        self.assertIsNotNone(image)
        self.assertEqual(image["src"], "/static/example.png")

    # ---- Tags ----

    def test_tags(self):
        # without tags: shows placeholder
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        placeholder = soup.find("span", {"class": "info-placeholder"})
        self.assertIsNotNone(placeholder)

        # with tags: shows tag names
        bookmark = self.setup_bookmark(tags=[self.setup_tag(), self.setup_tag()])
        soup = self.get_index_details_modal(bookmark)
        for tag in bookmark.tags.all():
            tag_el = soup.find("span", {"class": "info-tag"}, string=tag.name)
            self.assertIsNotNone(tag_el)

    # ---- Description ----

    def test_description(self):
        # without description — textarea exists but empty
        bookmark = self.setup_bookmark(description="")
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "description"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "")

        # with description
        bookmark = self.setup_bookmark(description="Test description")
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "description"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "Test description")

    # ---- Notes ----

    def test_notes(self):
        # without notes — textarea exists but empty
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "notes"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "")

        # with notes
        bookmark = self.setup_bookmark(notes="Test notes")
        soup = self.get_index_details_modal(bookmark)
        textarea = soup.find("textarea", {"data-field": "notes"})
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.text.strip(), "Test notes")

    # ---- Actions ----

    def test_delete_button(self):
        bookmark = self.setup_bookmark()
        modal = self.get_index_details_modal(bookmark)
        delete_button = modal.find("button", {"name": "trash"})
        self.assertIsNotNone(delete_button)
        self.assertEqual(delete_button["value"], str(bookmark.id))
        self.assertTrue(delete_button.has_attr("ld-confirm-button"))

    def test_actions_visibility(self):
        # own bookmark — has footer actions
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        trash_btn = soup.find("button", {"name": "trash"})
        self.assertIsNotNone(trash_btn)

        # other user's bookmark — no footer actions
        other_user = self.setup_user(enable_sharing=True)
        bookmark = self.setup_bookmark(user=other_user, shared=True)
        soup = self.get_shared_details_modal(bookmark)
        trash_btn = soup.find("button", {"name": "trash"})
        self.assertIsNone(trash_btn)

    def test_status_buttons(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.save()

        # own bookmark — has status chips
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        archive_chip = soup.find(attrs={"data-chip-field": "is_archived"})
        shared_chip = soup.find(attrs={"data-chip-field": "shared"})
        unread_chip = soup.find(attrs={"data-chip-field": "unread"})
        self.assertIsNotNone(archive_chip)
        self.assertIsNotNone(unread_chip)
        self.assertIsNotNone(shared_chip)

        # not archived → uses #ld-icon-archive
        use = archive_chip.find("use")
        self.assertIn("ld-icon-archive", use.get("xlink:href", ""))

        # archived
        bookmark = self.setup_bookmark(is_archived=True)
        soup = self.get_index_details_modal(bookmark)
        archive_chip = soup.find(attrs={"data-chip-field": "is_archived"})
        use = archive_chip.find("use")
        self.assertIn("ld-icon-archive", use.get("xlink:href", ""))

    # ---- Date ----

    def test_date_added(self):
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)

        expected_date = timezone.localtime(bookmark.date_added).strftime("%Y/%m/%d")
        dates = soup.find_all("span", {"class": "info-date"})
        date_texts = [d.get_text() for d in dates]
        self.assertTrue(any(expected_date in t for t in date_texts), f"Expected {expected_date} in {date_texts}")

    # ---- Assets ----

    def test_asset_list_visibility(self):
        # no assets
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        section = self.find_section(soup, "Files")
        asset_list = section.find("div", {"class": "info-files"}) if section else None
        self.assertIsNone(asset_list)

        # with assets
        bookmark = self.setup_bookmark()
        self.setup_asset(bookmark)
        soup = self.get_index_details_modal(bookmark)
        section = self.find_section(soup, "Files")
        self.assertIsNotNone(section)
        asset_list = section.find("div", {"class": "info-files"})
        self.assertIsNotNone(asset_list)

    def test_asset_list(self):
        bookmark = self.setup_bookmark()
        assets = [self.setup_asset(bookmark) for _ in range(3)]
        soup = self.get_index_details_modal(bookmark)
        section = self.find_section(soup, "Files")
        asset_list = section.find("div", {"class": "info-files"})
        for asset in assets:
            asset_item = self.find_asset(asset_list, asset)
            self.assertIsNotNone(asset_item)

    def test_asset_actions_visibility(self):
        bookmark = self.setup_bookmark()
        asset = self.setup_asset(bookmark)
        soup = self.get_index_details_modal(bookmark)
        asset_item = self.find_asset(soup, asset)
        file_link = asset_item.find("a", {"class": "info-file-link"})
        delete_button = asset_item.find("button", {"name": "remove_asset"})
        self.assertIsNotNone(file_link)
        self.assertIsNotNone(delete_button)

        # shared bookmark — no delete
        other_user = self.setup_user(enable_sharing=True, enable_public_sharing=True)
        bookmark = self.setup_bookmark(shared=True, user=other_user)
        asset = self.setup_asset(bookmark)
        soup = self.get_shared_details_modal(bookmark)
        asset_item = self.find_asset(soup, asset)
        delete_button = asset_item.find("button", {"name": "remove_asset"})
        self.assertIsNone(delete_button)

    # ---- Non-editable (shared view) ----

    def test_non_editable_fields(self):
        other_user = self.setup_user(enable_sharing=True)
        bookmark = self.setup_bookmark(
            user=other_user, shared=True,
            description="Test desc", notes="Test notes",
        )
        soup = self.get_shared_details_modal(bookmark)
        # Title textarea should be disabled
        title_el = soup.find("textarea", {"class": "bookmark-title-input"})
        self.assertIsNotNone(title_el)
        self.assertTrue(title_el.has_attr("disabled"))
        # No textareas for description/notes (readonly divs instead)
        textarea = soup.find("textarea", {"data-field": "description"})
        self.assertIsNone(textarea)

    # ---- Health check 区块 ----

    def test_health_chip_in_status_row(self):
        from bookmarks.models import HEALTH_STATUS_DEAD

        bookmark = self.setup_bookmark()
        bookmark.health_status = HEALTH_STATUS_DEAD
        bookmark.health_details = {
            "checked_at": "2026-09-15T16:18:00",
            "http_status": 404,
            "reason": "HTTP 404",
        }
        bookmark.save()

        soup = self.get_index_details_modal(bookmark)
        chips = soup.select_one("div.detail-status-chips")
        self.assertIsNotNone(chips)
        chip = chips.select_one("span.detail-health-chip")
        self.assertIsNotNone(chip)
        # 健康 chip 位于最后（分享 chip 之后）
        all_chips = list(chips.select("span.detail-health-chip, button[data-chip-field]"))
        self.assertEqual(all_chips[-1], chip)

    def test_health_checked_state(self):
        from bookmarks.models import HEALTH_STATUS_BLOCKED

        bookmark = self.setup_bookmark()
        bookmark.health_status = HEALTH_STATUS_BLOCKED
        bookmark.health_details = {
            "checked_at": "2026-09-15T16:18:00",
            "http_status": 403,
            "reason": "HTTP 403",
        }
        bookmark.save()

        soup = self.get_index_details_modal(bookmark)
        chip = soup.select_one("span.detail-health-chip")
        # 低调指示：小圆点 + 文字
        self.assertIsNotNone(chip.select_one("span.health-dot--blocked"))
        self.assertEqual(chip.select_one("span.health-text").text.strip(), "Blocked")
        # 详情弹层：状态 / HTTP 码（reason 与 HTTP 码重复时不显示）/ 检查时间
        popover = chip.select_one("span.health-popover")
        self.assertIsNotNone(popover)
        self.assertIn("Blocked", popover.get_text())
        self.assertIn("HTTP 403", popover.get_text())
        # reason "HTTP 403" 与 HTTP 码重复 → 去重，不重复出现
        self.assertEqual(popover.get_text().count("HTTP 403"), 1)
        # reason 行始终渲染（无内容时隐藏），保证重检后就地更新
        reason = popover.select_one("[data-popover-reason]")
        self.assertIsNotNone(reason)
        self.assertTrue(reason.has_attr("hidden"))
        self.assertEqual(reason.get_text(strip=True), "")
        checked = popover.select_one("[data-popover-checked]")
        self.assertIsNotNone(checked)
        # 检查时间：本地时区格式 检查于 2026/09/15 ...
        self.assertIn("Checked at", checked.get_text())
        self.assertRegex(checked.get_text(), r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}")
        # 刷新按钮（图标按钮，title 为"重新检查"）
        btn = chip.select_one("button[data-health-check]")
        self.assertIsNotNone(btn)
        self.assertEqual(btn.get("title"), "Recheck")
        self.assertEqual(btn.get_text(strip=True), "")

    def test_health_stale_chip_shows_unknown(self):
        """过期结果：chip 状态显示 unknown，弹层下方保留上次检查信息并标注已过期。"""
        from datetime import timedelta

        from django.utils import formats, timezone

        bookmark = self.setup_bookmark()
        bookmark.health_status = "blocked"
        bookmark.health_details = {
            "checked_at": (timezone.now() - timedelta(days=30)).isoformat(),
            "http_status": 403,
            "reason": "requires login",
        }
        bookmark.save()

        soup = self.get_index_details_modal(bookmark)
        chip = soup.select_one("span.detail-health-chip")
        self.assertIsNotNone(chip.select_one("span.health-dot--unknown"))
        self.assertEqual(
            chip.select_one("span.health-text").text.strip(), "Unknown"
        )
        popover = chip.select_one("span.health-popover")
        self.assertEqual(
            popover.select_one("[data-popover-status]").text.strip(), "Unknown"
        )
        # 上次检查信息保留在弹层下方
        self.assertEqual(
            popover.select_one("[data-popover-http]").text.strip(), "HTTP 403"
        )
        self.assertEqual(
            popover.select_one("[data-popover-reason]").text.strip(),
            "requires login",
        )
        checked = popover.select_one("[data-popover-checked]")
        self.assertIn("expired", checked.get_text())

    def test_health_reason_displayed_when_not_duplicate(self):
        bookmark = self.setup_bookmark()
        bookmark.health_status = "blocked"
        bookmark.health_details = {
            "checked_at": "2026-09-15T16:18:00",
            "http_status": 200,
            "reason": "redirected to blocked location: login",
        }
        bookmark.save()

        soup = self.get_index_details_modal(bookmark)
        chip = soup.select_one("span.detail-health-chip")
        popover = chip.select_one("span.health-popover")
        reason = popover.select_one("[data-popover-reason]")
        self.assertIsNotNone(reason)
        self.assertEqual(reason.text.strip(), "redirected to blocked location: login")
        self.assertIn("HTTP 200", popover.get_text())

    def test_health_unchecked_state(self):
        bookmark = self.setup_bookmark()
        soup = self.get_index_details_modal(bookmark)
        chip = soup.select_one("span.detail-health-chip")
        self.assertIsNotNone(chip)
        # 未检查：派生为 unknown 状态显示（不持久化）
        self.assertIsNotNone(chip.select_one("span.health-dot--unknown"))
        self.assertEqual(
            chip.select_one("span.health-text").text.strip(), "Unknown"
        )
        # 无检查时间：检查时间行始终渲染但隐藏（重检后就地更新依赖此行存在）
        popover = chip.select_one("span.health-popover")
        checked = popover.select_one("[data-popover-checked]")
        self.assertIsNotNone(checked)
        self.assertTrue(checked.has_attr("hidden"))
        # HTTP/原因行同样始终渲染且隐藏
        self.assertTrue(popover.select_one("[data-popover-http]").has_attr("hidden"))
        self.assertTrue(popover.select_one("[data-popover-reason]").has_attr("hidden"))
        # 刷新按钮 title 为"立即检查"
        btn = chip.select_one("button[data-health-check]")
        self.assertIsNotNone(btn)
        self.assertEqual(btn.get("title"), "Check now")

    def test_trash_modal_shows_status_without_check_button(self):
        from bookmarks.models import HEALTH_STATUS_DEAD

        bookmark = self.setup_bookmark()
        bookmark.is_deleted = True
        bookmark.date_deleted = timezone.now()
        bookmark.health_status = HEALTH_STATUS_DEAD
        bookmark.health_details = {
            "checked_at": "2026-09-15T16:18:00",
            "http_status": 404,
        }
        bookmark.save()

        url = reverse("linkding:bookmarks.trashed") + f"?details={bookmark.id}"
        response = self.client.get(url)
        soup = self.make_soup(response.content.decode())
        modal = soup.select_one("ld-details-modal")
        self.assertIsNotNone(modal)
        chip = modal.select_one("span.detail-health-chip")
        # 软删除书签仍呈现历史检查状态
        self.assertIsNotNone(chip)
        self.assertIsNotNone(chip.select_one("span.health-dot--dead"))
        self.assertIn("Checked at", chip.get_text())
        # 但不提供检查按钮（软删除书签不检查）
        self.assertIsNone(chip.select_one("button[data-health-check]"))
