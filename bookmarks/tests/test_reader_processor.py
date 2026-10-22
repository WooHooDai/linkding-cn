from unittest import mock

from django.test import TestCase

from bookmarks.services import reader_processor


class ReaderProcessorTestCase(TestCase):
    def test_normalize_html_for_reader_unwraps_shadow_templates(self):
        html = """
        <div>
          <my-widget>
            <template shadowrootmode="open">
              <img src="/image.jpg" alt="gallery">
            </template>
          </my-widget>
        </div>
        """

        normalized = reader_processor._normalize_html_for_reader(html)

        self.assertNotIn("shadowrootmode", normalized)
        self.assertIn('<img alt="gallery" src="/image.jpg"/>', normalized)

    def test_parse_html_normalizes_before_calling_defuddle(self):
        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=None,
            ),
            mock.patch(
                "bookmarks.services.reader_processor._normalize_html_for_reader",
                return_value="<p>normalized</p>",
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_html("<p>original</p>", "https://example.com")

        self.assertEqual(result, {"title": "ok"})
        mock_defuddle.assert_called_once_with("<p>normalized</p>", url="https://example.com", options=None)

    def test_parse_content_converts_xml_before_calling_defuddle(self):
        xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Feed title</title>
          <entry>
            <title>Post title</title>
            <content type="html">&lt;p&gt;Hello from XML&lt;/p&gt;</content>
          </entry>
        </feed>
        """

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=None,
            ),
            mock.patch(
                "bookmarks.services.reader_processor._normalize_html_for_reader",
                side_effect=lambda value: value,
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_content(
                xml, "application/atom+xml", "https://example.com/feed.xml"
            )

        self.assertEqual(result, {"title": "ok"})
        html = mock_defuddle.call_args.args[0]
        self.assertIn("<h1>Feed title</h1>", html)
        self.assertIn("<article>", html)
        self.assertIn("<p>Hello from XML</p>", html)

    def test_parse_content_converts_json_before_calling_defuddle(self):
        payload = '{"title": "JSON title", "content": "<p>Hello from JSON</p>"}'

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=None,
            ),
            mock.patch(
                "bookmarks.services.reader_processor._normalize_html_for_reader",
                side_effect=lambda value: value,
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_content(
                payload, "application/json", "https://example.com/api/post"
            )

        self.assertEqual(result, {"title": "ok"})
        html = mock_defuddle.call_args.args[0]
        self.assertIn("<h1>JSON title</h1>", html)
        self.assertIn("<p>Hello from JSON</p>", html)

    def test_parse_content_uses_json_path_content_selector(self):
        payload = """
        [
          {
            "data": {
              "children": [
                {
                  "data": {
                    "title": "Peanut butter cookies",
                    "selftext": "It turns out they are trivially easy."
                  }
                }
              ]
            }
          }
        ]
        """
        config = {
            "defuddle_args": {
                "contentSelector": "[0].data.children[0].data.selftext"
            }
        }

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_content(
                payload, "application/json", "https://www.reddit.com/r/test"
            )

        self.assertEqual(result, {"title": "ok"})
        html = mock_defuddle.call_args.args[0]
        self.assertIn("trivially easy", html)
        self.assertNotIn("Peanut butter cookies", html)
        self.assertEqual(
            mock_defuddle.call_args.kwargs["options"],
            {"contentSelector": "article"},
        )

    def test_parse_content_uses_xml_xpath_content_selector(self):
        xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Feed title</title>
          <entry>
            <title>Post title</title>
            <content type="html">&lt;p&gt;Hello from selected XML&lt;/p&gt;</content>
          </entry>
        </feed>
        """
        config = {
            "defuddle_args": {
                "contentSelector": "//atom:entry/atom:content"
            }
        }

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_content(
                xml, "application/atom+xml", "https://example.com/feed.xml"
            )

        self.assertEqual(result, {"title": "ok"})
        html = mock_defuddle.call_args.args[0]
        self.assertIn("Hello from selected XML", html)
        self.assertNotIn("Post title", html)
        self.assertEqual(
            mock_defuddle.call_args.kwargs["options"],
            {"contentSelector": "article"},
        )

    def test_parse_html_uses_xpath_content_selector(self):
        html = """
        <html>
          <body>
            <header>Ignored</header>
            <div id="content"><p>Hello from selected HTML</p></div>
          </body>
        </html>
        """
        config = {
            "defuddle_args": {
                "contentSelector": "//*[@id='content']"
            }
        }

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_html(
                html, "https://example.com/post"
            )

        self.assertEqual(result, {"title": "ok"})
        parsed_html = mock_defuddle.call_args.args[0]
        self.assertIn("Hello from selected HTML", parsed_html)
        self.assertNotIn("Ignored", parsed_html)
        self.assertEqual(
            mock_defuddle.call_args.kwargs["options"],
            {"contentSelector": "article"},
        )

    def test_parse_html_preserves_css_content_selector(self):
        html = '<html><body><article class="post"><p>Hello</p></article></body></html>'
        config = {
            "defuddle_args": {
                "contentSelector": [".post", "article"]
            }
        }

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_defuddle,
        ):
            result = reader_processor.parse_html(
                html, "https://example.com/post"
            )

        self.assertEqual(result, {"title": "ok"})
        self.assertEqual(
            mock_defuddle.call_args.kwargs["options"],
            {"contentSelector": [".post", "article"]},
        )

    def test_restore_missing_carousels_keeps_carousel_before_text(self):
        html = """
        <main>
          <figure aria-label="ld-carousel"><img src="/image.jpg"></figure>
          <p>article text</p>
        </main>
        """
        extracted = "<article><p>article text</p></article>"

        carousels = reader_processor._collect_carousels(html)
        restored = reader_processor._restore_missing_carousels(
            html, extracted, carousels
        )

        self.assertIn('<figure aria-label="ld-carousel"', restored)
        self.assertLess(
            restored.index('<figure aria-label="ld-carousel"'),
            restored.index("<p>article text</p>"),
        )

    def test_reader_defuddle_args_are_passed_to_defuddle(self):
        config = {"defuddle_args": {"contentSelector": ".article", "ignored": True}}
        html = "<html><body><article><p>text</p></article></body></html>"

        with (
            mock.patch(
                "bookmarks.services.reader_processor.get_reader_config",
                return_value=config,
            ),
            mock.patch(
                "bookmarks.services.website_loader.fetch_page_html",
                return_value=html,
            ) as mock_fetch,
            mock.patch(
                "bookmarks.services.defuddle.parse_html",
                return_value={"title": "ok"},
            ) as mock_parse,
        ):
            result = reader_processor.parse_url("https://example.com")

        self.assertEqual(result, {"title": "ok"})
        # 抓取复用 loader 通路（http_engine / auto cookie / 403 自愈）
        mock_fetch.assert_called_once_with(
            "https://example.com", config, username=""
        )
        # defuddle_args 中仅已知参数传递给 defuddle（ignored 被过滤）
        mock_parse.assert_called_once_with(
            html, url="https://example.com", options={"contentSelector": ".article"}
        )
