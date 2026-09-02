import io
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase

from bookmarks.services import preview_image_loader

mock_image_data = b"mock_image"


class MockStreamingResponse:
    def __init__(
        self,
        url,
        data=mock_image_data,
        content_type="image/png",
        content_length=None,
        status_code=200,
    ):
        self.url = url
        self.chunks = [data]
        self.status_code = status_code
        if not content_length:
            content_length = len(data)
        self.headers = {"Content-Type": content_type, "Content-Length": content_length}

    def iter_content(self, **kwargs):
        return self.chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class PreviewImageLoaderTestCase(TestCase):
    def setUp(self) -> None:
        self.temp_folder = tempfile.TemporaryDirectory()
        self.settings_override = self.settings(LD_PREVIEW_FOLDER=self.temp_folder.name)
        self.settings_override.enable()
        self.mock_load_website_metadata_patcher = mock.patch(
            "bookmarks.services.website_loader.load_website_metadata"
        )
        self.mock_load_website_metadata = (
            self.mock_load_website_metadata_patcher.start()
        )
        self.mock_load_website_metadata.return_value = mock.Mock(
            preview_image="https://example.com/image.png"
        )
        self.mock_get_metadata_config_patcher = mock.patch(
            "bookmarks.services.website_loader.get_metadata_config",
            return_value=None,
        )
        self.mock_get_metadata_config = self.mock_get_metadata_config_patcher.start()

    def tearDown(self) -> None:
        self.temp_folder.cleanup()
        self.settings_override.disable()
        self.mock_get_metadata_config_patcher.stop()
        self.mock_load_website_metadata_patcher.stop()

    def create_mock_response(
        self,
        url="https://example.com/image.png",
        icon_data=mock_image_data,
        content_type="image/png",
        content_length=None,
        status_code=200,
    ):
        if content_length is None:
            content_length = len(icon_data)
        mock_response = mock.Mock()
        mock_response.raw = io.BytesIO(icon_data)
        return MockStreamingResponse(
            url, icon_data, content_type, content_length, status_code
        )

    def get_image_path(self, filename):
        return Path(os.path.join(settings.LD_PREVIEW_FOLDER, filename))

    def assertImageExists(self, filename, data):
        self.assertTrue(self.get_image_path(filename).exists())
        self.assertEqual(self.get_image_path(filename).read_bytes(), data)

    def assertNoImageExists(self):
        self.assertFalse(os.listdir(settings.LD_PREVIEW_FOLDER))

    def test_load_preview_image(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()

            file = preview_image_loader.load_preview_image("https://example.com")

            self.assertIsNotNone(file)
            self.assertImageExists(file, mock_image_data)

    def test_load_preview_image_returns_none_if_no_preview_image_detected(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()
            self.mock_load_website_metadata.return_value = mock.Mock(preview_image=None)

            file = preview_image_loader.load_preview_image("https://example.com")

            self.assertIsNone(file)
            self.assertNoImageExists()

    def test_load_preview_image_returns_none_if_metadata_loader_returns_none(self):
        self.mock_load_website_metadata.return_value = None

        file = preview_image_loader.load_preview_image("https://example.com")

        self.assertIsNone(file)
        self.assertNoImageExists()

    def test_load_preview_image_returns_none_for_invalid_status_code(self):
        invalid_status_codes = [199, 300, 400, 500]

        for status_code in invalid_status_codes:
            with mock.patch("requests.get") as mock_get:
                mock_get.return_value = self.create_mock_response(
                    status_code=status_code
                )

                file = preview_image_loader.load_preview_image("https://example.com")

                self.assertIsNone(file)
                self.assertNoImageExists()

    def test_load_preview_image_prefers_request_without_referer(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.side_effect = [self.create_mock_response()]

            file = preview_image_loader._download_and_save_image(
                "https://example.com/image.png",
                referer_url="https://example.com/page",
            )

            self.assertIsNotNone(file)
            temp_image_path = Path(settings.LD_PREVIEW_FOLDER, "tmp", file)
            self.assertTrue(temp_image_path.exists())
            self.assertEqual(temp_image_path.read_bytes(), mock_image_data)
            self.assertEqual(mock_get.call_count, 1)
            headers = mock_get.call_args_list[0].kwargs["headers"]
            self.assertNotIn("Referer", headers)

    def test_load_preview_image_uses_site_adapter_user_agent(self):
        config = {
            "headers": {
                "User-Agent": "Configured Browser UA",
                "X-Site-Adapter": "enabled",
            }
        }
        with mock.patch("requests.get") as mock_get, mock.patch(
            "bookmarks.services.website_loader.get_metadata_config",
            return_value=config,
        ) as mock_get_config:
            mock_get.return_value = self.create_mock_response()

            file = preview_image_loader._download_and_save_image(
                "https://example.com/image.png",
                referer_url="https://example.com/page",
                username="alice",
            )

            self.assertIsNotNone(file)
            self.assertEqual(mock_get.call_count, 1)
            headers = mock_get.call_args_list[0].kwargs["headers"]
            self.assertEqual(headers["User-Agent"], "Configured Browser UA")
            self.assertEqual(headers["X-Site-Adapter"], "enabled")
            self.assertNotIn("Referer", headers)
            mock_get_config.assert_called_once_with(
                "https://example.com/page", username="alice"
            )

    def test_load_preview_image_retries_with_referer_on_403(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self.create_mock_response(status_code=403),
                self.create_mock_response(),
            ]

            file = preview_image_loader._download_and_save_image(
                "https://example.com/image.png",
                referer_url="https://example.com/page",
            )

            self.assertIsNotNone(file)
            temp_image_path = Path(settings.LD_PREVIEW_FOLDER, "tmp", file)
            self.assertTrue(temp_image_path.exists())
            self.assertEqual(temp_image_path.read_bytes(), mock_image_data)
            self.assertEqual(mock_get.call_count, 2)

            first_headers = mock_get.call_args_list[0].kwargs["headers"]
            second_headers = mock_get.call_args_list[1].kwargs["headers"]
            self.assertNotIn("Referer", first_headers)
            self.assertEqual(second_headers["Referer"], "https://example.com/page")

    def test_load_preview_image_returns_none_if_content_length_exceeds_limit(self):
        # exceeds max size
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(
                content_length=settings.LD_PREVIEW_MAX_SIZE + 1
            )

            file = preview_image_loader.load_preview_image("https://example.com")

            self.assertIsNone(file)
            self.assertNoImageExists()

        # equals max size
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(
                content_length=settings.LD_PREVIEW_MAX_SIZE
            )

            file = preview_image_loader.load_preview_image("https://example.com")

            self.assertIsNotNone(file)
            self.assertImageExists(file, mock_image_data)

    def test_load_preview_image_returns_none_for_invalid_content_type(self):
        invalid_content_types = ["text/html", "application/json"]

        for content_type in invalid_content_types:
            with mock.patch("requests.get") as mock_get:
                mock_get.return_value = self.create_mock_response(
                    content_type=content_type
                )

                file = preview_image_loader.load_preview_image("https://example.com")

                self.assertIsNone(file)
                self.assertNoImageExists()

        valid_content_types = ["image/png", "image/jpeg", "image/gif"]

        for content_type in valid_content_types:
            with mock.patch("requests.get") as mock_get:
                mock_get.return_value = self.create_mock_response(
                    content_type=content_type
                )

                file = preview_image_loader.load_preview_image("https://example.com")

                self.assertIsNotNone(file)
                self.assertImageExists(file, mock_image_data)

    def test_load_preview_image_returns_none_if_download_exceeds_content_length(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(content_length=1)

            file = preview_image_loader.load_preview_image("https://example.com")

            self.assertIsNone(file)
            self.assertNoImageExists()

    def test_load_preview_image_creates_folder_if_not_exists(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response()

            folder = Path(settings.LD_PREVIEW_FOLDER)
            folder.rmdir()

            self.assertFalse(folder.exists())

            preview_image_loader.load_preview_image("https://example.com")

            self.assertTrue(folder.exists())

    def test_guess_file_extension(self):
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(content_type="image/png")

            file = preview_image_loader.load_preview_image("https://example.com")

            self.assertImageExists(file, mock_image_data)
            self.assertEqual("png", file.split(".")[-1])

        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = self.create_mock_response(content_type="image/jpeg")

            file = preview_image_loader.load_preview_image("https://example.com")

            self.assertImageExists(file, mock_image_data)
            self.assertEqual("jpg", file.split(".")[-1])
