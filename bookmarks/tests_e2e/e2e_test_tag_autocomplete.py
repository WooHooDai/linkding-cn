from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class TagAutocompleteE2ETestCase(LinkdingE2ETestCase):
    """标签候选列表：匹配规则（前缀/子串/拼音）与排序规则（频次→CJK→名称）的端到端断言。

    构造的标签频次：
      python(3) > fastapi(2) = docker-compose(2) > api-docs(1) = docker(1) = k8s-deploy(1) > 最爱(0)
    """

    def setUp(self) -> None:
        super().setUp()
        self.tag_python = self.setup_tag(name="python")
        self.tag_fastapi = self.setup_tag(name="fastapi")
        self.tag_docker_compose = self.setup_tag(name="docker-compose")
        self.tag_api_docs = self.setup_tag(name="api-docs")
        self.tag_docker = self.setup_tag(name="docker")
        self.tag_k8s_deploy = self.setup_tag(name="k8s-deploy")
        self.tag_zui_ai = self.setup_tag(name="最爱")

        for _ in range(3):
            self.setup_bookmark(tags=[self.tag_python])
        for tag in (self.tag_fastapi, self.tag_docker_compose):
            self.setup_bookmark(tags=[tag])
            self.setup_bookmark(tags=[tag])
        for tag in (self.tag_api_docs, self.tag_docker, self.tag_k8s_deploy):
            self.setup_bookmark(tags=[tag])
        # 最爱 无书签（频次 0）

    def open_new_bookmark_page(self, playwright):
        return self.open(reverse("linkding:bookmarks.new"), playwright)

    def suggestions(self, page):
        return page.locator("ld-tag-autocomplete .menu-item a")

    def fill_tag_input(self, page, value: str):
        page.locator("#id_tag_string").fill(value)

    def test_prefix_matches_ordered_by_frequency(self):
        with sync_playwright() as p:
            page = self.open_new_bookmark_page(p)
            self.fill_tag_input(page, "dock")
            expect(self.suggestions(page)).to_have_count(2)
            expect(self.suggestions(page)).to_have_text(["docker-compose", "docker"])

    def test_prefix_match_ranks_before_substring_match(self):
        with sync_playwright() as p:
            page = self.open_new_bookmark_page(p)
            self.fill_tag_input(page, "api")
            # api-docs 前缀命中（频次 1）排前；fastapi 子串命中（频次 2）排后
            expect(self.suggestions(page)).to_have_count(2)
            expect(self.suggestions(page)).to_have_text(["api-docs", "fastapi"])

    def test_substring_match_finds_middle_fragment(self):
        with sync_playwright() as p:
            page = self.open_new_bookmark_page(p)
            self.fill_tag_input(page, "deploy")
            expect(self.suggestions(page)).to_have_count(1)
            expect(self.suggestions(page)).to_have_text(["k8s-deploy"])

    def test_pinyin_substring_does_not_match(self):
        self.setup_tag(name="简历")
        with sync_playwright() as p:
            page = self.open_new_bookmark_page(p)
            # "简历" 全拼 jianli 包含中间片段 "anli"，
            # 但拼音只做前缀匹配，因此不应命中
            self.fill_tag_input(page, "anli")
            expect(self.suggestions(page)).to_have_count(0)

    def test_pinyin_prefix_still_matches(self):
        with sync_playwright() as p:
            page = self.open_new_bookmark_page(p)
            self.fill_tag_input(page, "zui")
            expect(self.suggestions(page)).to_have_count(1)
            expect(self.suggestions(page)).to_have_text(["最爱"])
