/**
 * 双击进入就地编辑（默认/紧凑视图）的回归测试。
 *
 * 判定走「第二次 click 的 detail」，不是 dblclick 事件：第一下就会改版面（描述展开、
 * 折叠区重排），第二下的命中元素常常已经不是同一个了。所以这里也照真实浏览器那样
 * 发两下 click，而不是发一个 dblclick。
 *
 * 重点在三处易坏的地方：标题/标签的单击要挂起等双击、标题末尾空白区不能被接管，
 * 以及表格格子行不能被这套逻辑重复接管。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { setupDom, rowHtml, mountRow } from "./dom-test-env.mjs";

function fire(element, type, props = {}) {
  // linkedom 没有 MouseEvent：用 Event 兜底，再把行为要读的字段挂上去
  const event = new window.Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, { button: 0, detail: 1, clientX: 0, clientY: 0 }, props);
  element.dispatchEvent(event);
  return event;
}

/** 真实双击：两下 click，第二下带 detail=2 */
function doubleClick(element, props = {}) {
  fire(element, "click", { ...props, detail: 1 });
  return fire(element, "click", { ...props, detail: 2 });
}

function tick(ms = 0) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 略大于 NAV_DELAY（350）：挂起的跳转到点后才算数
const AFTER_WINDOW = 400;

test("双击标题文字进入标题编辑", async () => {
  const mod = await setupDom(rowHtml({ title: "Hello" }));
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".title-link span"));

  assert.ok(li.querySelector(".quick-edit-title-input"), "应打开标题编辑器");
});

test("标题文字的单击被挂起，等双击窗口过去才跳转", async () => {
  const mod = await setupDom(rowHtml());
  const visited = [];
  window.Turbo = { visit: (url) => visited.push(url) };
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  const event = fire(li.querySelector(".title-link span"), "click");

  assert.equal(event.defaultPrevented, true, "单击要先接住");
  assert.deepEqual(visited, [], "不能立刻跳转");

  await tick(AFTER_WINDOW);
  assert.equal(visited.length, 1, "窗口过后照常跳转");
  assert.match(visited[0], /example\.com/);
});

test("标题末尾的空白区保持原生即时跳转", async () => {
  const mod = await setupDom(rowHtml());
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  // 落点是链接本身而不是文字 span，等于点在链接末尾的空白上
  const event = fire(li.querySelector(".title-link"), "click");

  assert.equal(event.defaultPrevented, false);
});

test("第二下落点换了元素，仍按第一下记住的字段进编辑", async () => {
  const mod = await setupDom(rowHtml());
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");
  const title = li.querySelector(".title");
  const span = li.querySelector(".title-link span");

  // 第一下在文字上，第二下落到标题空白处（现实里版面重排后常见）
  fire(span, "click", { detail: 1 });
  const second = fire(title, "click", { detail: 2 });

  assert.equal(second.defaultPrevented, true, "第二下不能再走原生跳转");
  assert.ok(li.querySelector(".quick-edit-title-input"), "应打开标题编辑器");
});

test("双击标题后不再有挂起的跳转", async () => {
  const mod = await setupDom(rowHtml());
  const visited = [];
  window.Turbo = { visit: (url) => visited.push(url) };
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".title-link span"));
  await tick(AFTER_WINDOW);

  assert.ok(li.querySelector(".quick-edit-title-input"), "双击应进编辑");
  assert.deepEqual(visited, [], "双击不应把书签打开");
});

test("双击同行描述进入描述编辑", async () => {
  const html = rowHtml({ description: "Some text" }).replace(
    '<div class="description">',
    '<div class="description inline">',
  );
  const mod = await setupDom(html);
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".description-text"));

  assert.ok(
    li.querySelector(".quick-edit-description-textarea"),
    "应打开描述编辑器",
  );
});

test("进描述编辑时把第一下单击展开的态还原", async () => {
  const html = rowHtml({ description: "Some text" }).replace(
    '<div class="description">',
    '<div class="description inline">',
  );
  const mod = await setupDom(html);
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");
  const text = li.querySelector(".description-text");
  const container = li.querySelector(".description-container");

  // linkedom 既量不出截断、也没有捕获阶段，表达不了「第一下展开、第二下改判编辑」
  // 这一串真实时序：直接摆成展开态再走开编辑器那一步
  text.classList.add("expanded");
  container.classList.add("expanded");

  item._openEditor("description");

  assert.ok(li.querySelector(".quick-edit-description-textarea"));
  assert.equal(container.classList.contains("expanded"), false);
});

test("双击笔记进入笔记编辑", async () => {
  const mod = await setupDom(
    rowHtml({ notes: "old", notesHtml: "<p>old</p>" }),
  );
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".inline-edit-notes .markdown"));

  assert.equal(
    li.querySelector(".inline-edit-textarea").style.display,
    "block",
    "应切到笔记编辑框",
  );
});

test("笔记里的链接双击不进入编辑", async () => {
  const mod = await setupDom(
    rowHtml({
      notes: "see https://example.com",
      notesHtml: '<p>see <a href="https://example.com">link</a></p>',
    }),
  );
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".inline-edit-notes .markdown a"));

  assert.equal(
    li.querySelector(".inline-edit-textarea").style.display,
    "none",
    "链接是独立交互目标，双击不转成编辑",
  );
});

test("分行样式下双击标签进入标签编辑", async () => {
  const mod = await setupDom(rowHtml({ tags: ["a"] }));
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".tags a"));

  assert.ok(li.querySelector(".quick-edit-tags-container"), "应打开标签编辑器");
});

test("同行样式下双击标签芯片不进入编辑", async () => {
  const html = rowHtml({ tags: ["a"] }).replace(
    '<div class="description">',
    '<div class="description inline">',
  );
  const mod = await setupDom(html);
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".tags a"));

  assert.equal(
    li.querySelector(".quick-edit-tags-container"),
    null,
    "同行样式的标签芯片是筛选链接",
  );
});

test("带修饰键的连击不接管（留给系统的打开方式）", async () => {
  const mod = await setupDom(rowHtml());
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".title-link span"), { metaKey: true });

  assert.equal(li.querySelector(".quick-edit-title-input"), null);
});

test("表格格子行不接管双击（交给表格行为）", async () => {
  const mod = await setupDom(rowHtml({ withCell: true }));
  mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  doubleClick(li.querySelector(".title-link span"));

  assert.equal(
    li.querySelector(".quick-edit-title-input"),
    null,
    "格子行双击由表格行为处理，不应再开一个编辑器",
  );
});
