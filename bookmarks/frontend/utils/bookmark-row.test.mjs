/**
 * 行行为（BookmarkItem）的回归测试。
 *
 * 覆盖拆分前最容易坏的几处：编辑器单例互斥、标签编辑的保存与回滚、
 * 快捷标签的乐观更新与回滚、表格格子行不创建移动端遮罩。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { setupDom, rowHtml, mountRow } from "./dom-test-env.mjs";

/** 记录所有 PATCH 请求；failOnce 可让下一次请求失败以验证回滚 */
function trackFetch() {
  const calls = [];
  let failNext = false;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, body: options.body ? JSON.parse(options.body) : null });
    if (failNext) {
      failNext = false;
      return { ok: false, json: async () => ({}) };
    }
    return { ok: true, json: async () => ({ html: "" }) };
  };
  return {
    calls,
    failOnce() {
      failNext = true;
    },
  };
}

/** 等一轮宏任务，让 requestAnimationFrame / blur 的 setTimeout 回调跑完 */
function tick(ms = 0) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function tagEditorInput(li) {
  return li.querySelector(
    ".quick-edit-tags-container textarea, .quick-edit-tags-container input",
  );
}

function displayedTags(li) {
  return Array.from(li.querySelectorAll(".tags a")).map((a) => a.textContent);
}

test("同一行只允许一个编辑器处于打开状态", async () => {
  const mod = await setupDom(rowHtml());
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("title");
  assert.ok(li.querySelector(".quick-edit-title-input"), "标题编辑器应已打开");

  item.startQuickEdit("tags");
  assert.equal(
    li.querySelector(".quick-edit-title-input"),
    null,
    "打开标签编辑器时应关闭标题编辑器",
  );
  assert.ok(li.querySelector(".quick-edit-tags-container"));
});

test("再次触发同一字段会关闭编辑器", async () => {
  const mod = await setupDom(rowHtml());
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("title");
  assert.ok(li.querySelector(".quick-edit-title-input"));

  item.startQuickEdit("title");
  assert.equal(
    li.querySelector(".quick-edit-title-input"),
    null,
    "重复触发同一字段应切换为关闭",
  );
});

test("标签编辑保存时发出 PATCH 并就地更新显示", async () => {
  const mod = await setupDom(rowHtml({ tags: ["a"] }));
  const api = trackFetch();
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("tags");
  await tick();

  const input = tagEditorInput(li);
  assert.ok(input, "标签编辑器应有输入框");
  input.value = "a b";
  input.dispatchEvent(new window.Event("blur"));
  await tick(200);

  const patch = api.calls.find((c) => c.body?.tag_names);
  assert.ok(patch, "应发出 tag_names 的 PATCH 请求");
  assert.deepEqual(patch.body.tag_names, ["a", "b"]);
  assert.deepEqual(displayedTags(li), ["a", "b"], "保存后应就地更新标签显示");
});

test("按下补全框导致的失焦不收编辑态", async () => {
  // 触屏：按下候选后浏览器会把焦点移走，兼容 mousedown 更晚才到；
  // 这段窗口里若收掉编辑器，刚落下的候选就一起丢了
  const mod = await setupDom(rowHtml({ tags: [], withCell: true }));
  const api = trackFetch();
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("tags");
  await tick();

  const input = tagEditorInput(li);
  assert.ok(input);
  li.querySelector(".quick-edit-tags-container").dispatchEvent(
    new window.Event("pointerdown", { bubbles: true }),
  );
  input.value = "a b";
  input.dispatchEvent(new window.Event("blur"));
  await tick(200);

  assert.ok(
    li.querySelector(".quick-edit-tags-container"),
    "按下补全框后失焦不应关闭编辑器",
  );
  assert.equal(api.calls.length, 0, "不应发出请求");
});

test("标签保存失败时回滚到原标签", async () => {
  const mod = await setupDom(rowHtml({ tags: ["a"] }));
  const api = trackFetch();
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("tags");
  await tick();
  api.failOnce();

  const input = tagEditorInput(li);
  input.value = "a b";
  input.dispatchEvent(new window.Event("blur"));
  await tick(200);

  assert.deepEqual(displayedTags(li), ["a"], "请求失败后应回滚为原标签");
});

test("按 Escape 取消标签编辑不发出请求", async () => {
  const mod = await setupDom(rowHtml({ tags: ["a"] }));
  const api = trackFetch();
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("tags");
  await tick();

  const input = tagEditorInput(li);
  input.value = "a b";
  const event = new window.Event("keydown", { bubbles: true });
  event.key = "Escape";
  input.dispatchEvent(event);
  await tick(10);

  assert.equal(
    api.calls.filter((c) => c.body?.tag_names).length,
    0,
    "取消编辑不应发出请求",
  );
  assert.deepEqual(displayedTags(li), ["a"]);
});

test("快捷标签点击先乐观更新", async () => {
  const html = rowHtml({ tags: [] }).replace(
    '<span class="tags"></span>',
    '<span class="tags"></span><button class="quick-tag-btn" data-quick-tag-name="x">x</button>',
  );
  const mod = await setupDom(html);
  trackFetch();
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");
  const btn = li.querySelector(".quick-tag-btn");

  item.onQuickTagClick({
    preventDefault() {},
    stopPropagation() {},
    currentTarget: btn,
  });
  assert.deepEqual(displayedTags(li), ["x"], "点击后应立即显示新标签");

  await tick(10);
  assert.deepEqual(displayedTags(li), ["x"], "请求成功应保留新标签");
});

test("快捷标签请求失败后回滚为原状态", async () => {
  const html = rowHtml({ tags: [] }).replace(
    '<span class="tags"></span>',
    '<span class="tags"></span><button class="quick-tag-btn" data-quick-tag-name="x">x</button>',
  );
  const mod = await setupDom(html);
  const api = trackFetch();
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");
  const btn = li.querySelector(".quick-tag-btn");
  api.failOnce();

  item.onQuickTagClick({
    preventDefault() {},
    stopPropagation() {},
    currentTarget: btn,
  });
  await tick(20);

  assert.deepEqual(displayedTags(li), [], "请求失败后应清空回滚");
});

test("表格格子行进入标签编辑时用开洞遮罩", async () => {
  // 遮罩挂 body，而 .table-body-scroll 有 z-index:0 自成层叠上下文，格内编辑器
  // 抬不到遮罩之上——改用四条遮条把编辑框挖出来
  const mod = await setupDom(rowHtml({ withCell: true }));
  window.matchMedia = (query) => ({
    matches: query.includes("coarse"),
    media: query,
    addEventListener() {},
    removeEventListener() {},
  });
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("tags");
  await tick();

  assert.ok(li.querySelector(".quick-edit-tags-container"), "应打开标签编辑器");
  const overlay = document.querySelector(".editor-overlay");
  assert.ok(overlay, "格子行也应有遮罩");
  assert.ok(overlay.classList.contains("is-spotlight"), "应为开洞版");
  assert.equal(
    overlay.querySelectorAll(".editor-overlay-strip").length,
    4,
    "四条遮条分别盖住上下左右",
  );
});

test("列表行在触屏设备上进入编辑时创建遮罩，关闭后移除", async () => {
  const mod = await setupDom(rowHtml());
  window.matchMedia = (query) => ({
    matches: query.includes("coarse"),
    media: query,
    addEventListener() {},
    removeEventListener() {},
  });
  const item = mountRow(mod);

  item.startQuickEdit("tags");
  await tick();
  assert.ok(document.querySelector(".editor-overlay"), "列表行应有遮罩");

  item.startQuickEdit("tags");
  await tick();
  assert.equal(
    document.querySelector(".editor-overlay"),
    null,
    "关闭后遮罩应移除",
  );
});

test("行行为在原型上装齐各职责模块的方法", async () => {
  const mod = await setupDom(rowHtml());
  const item = mountRow(mod);

  // 拆分后各职责模块靠 Object.assign 装到原型，漏装一个就是静默失效
  for (const name of [
    "_startEditTitle",
    "_startEditDescription",
    "_startEditAuthor",
    "initTitleTooltip",
    "initDescriptionToggle",
    "_startEditNotes",
    "onNotesBlur",
    "_startEditTags",
    "onQuickTagClick",
    "_updateTagsDisplay",
    "_initActionButtons",
    "_applyStateToRow",
  ]) {
    assert.equal(typeof item[name], "function", `缺少方法 ${name}`);
  }
});

test("标题编辑保存时发出 PATCH 并就地更新显示", async () => {
  const mod = await setupDom(rowHtml({ title: "旧标题" }));
  const api = trackFetch();
  const item = mountRow(mod);
  const li = document.querySelector("li[ld-bookmark-item]");

  item.startQuickEdit("title");
  const input = li.querySelector(".quick-edit-title-input");
  assert.ok(input, "标题编辑器应已打开");

  input.value = "新标题";
  input.dispatchEvent(new window.Event("blur"));
  await tick(50);

  const patch = api.calls.find((c) => c.body?.title);
  assert.ok(patch, "应发出 title 的 PATCH 请求");
  assert.equal(patch.body.title, "新标题");
  assert.equal(
    li.querySelector(".title-link span").textContent,
    "新标题",
    "保存后应就地更新标题",
  );
});

/** 文字模式的状态按钮：没有 svg，文案表示当前状态，另一状态的文案存在 data-toggle-label */
function statusTextRowHtml() {
  return `
    <ul class="bookmark-list view-list">
      <li ld-bookmark-item data-bookmark-id="1" class="unread">
        <div class="content">
          <div class="actions">
            <button ld-confirm-button ld-confirm-question="Share?" type="submit" name="share"
                    data-action="share" class="btn btn-link btn-sm"
                    data-toggle-label="Shared">Share</button>
            <button type="button" data-action="mark_as_read" class="btn btn-link btn-sm"
                    data-toggle-label="Read">Unread</button>
          </div>
        </div>
      </li>
    </ul>`;
}

test("文字模式下未读按钮点后即时换文案与动作，失败回滚", async () => {
  const mod = await setupDom(statusTextRowHtml());
  const api = trackFetch();
  const li = document.querySelector("li[ld-bookmark-item]");
  const item = mountRow(mod);
  const readButton = li.querySelector('[data-action="mark_as_read"]');

  // 失败回滚：文案、动作、行上的 unread 类都要回到原样
  api.failOnce();
  readButton.click();
  assert.equal(readButton.textContent.trim(), "Read", "点后应先换成已读文案");
  assert.equal(readButton.dataset.action, "mark_as_unread");
  assert.equal(readButton.dataset.toggleLabel, "Unread");
  assert.equal(li.classList.contains("unread"), false);
  await tick(10);
  assert.equal(readButton.textContent.trim(), "Unread", "失败后应还原文案");
  assert.equal(readButton.dataset.action, "mark_as_read");
  assert.equal(li.classList.contains("unread"), true);

  // 成功路径：文案保持切换后的状态
  readButton.click();
  assert.equal(readButton.textContent.trim(), "Read");
  await tick(10);
  assert.equal(readButton.textContent.trim(), "Read");
});

test("文字模式下分享按钮点后即时换文案与确认语", async () => {
  const mod = await setupDom(statusTextRowHtml());
  const li = document.querySelector("li[ld-bookmark-item]");
  const item = mountRow(mod);
  const shareButton = li.querySelector('[data-action="share"]');

  // 分享按钮走确认弹窗，直接驱动行行为的乐观更新/回滚
  item._applyStateToRow("share", true);
  assert.equal(shareButton.textContent.trim(), "Shared");
  assert.equal(shareButton.dataset.action, "unshare");
  assert.equal(shareButton.dataset.toggleLabel, "Share");
  assert.equal(
    shareButton.getAttribute("ld-confirm-question"),
    "Unshare?",
    "确认语应跟着按钮语义走",
  );

  item._applyStateToRow("share", false);
  assert.equal(shareButton.textContent.trim(), "Share");
  assert.equal(shareButton.dataset.action, "share");
  assert.equal(shareButton.getAttribute("ld-confirm-question"), "Share?");
});

test("图标模式的状态按钮不会被写入文案", async () => {
  const html = `
    <ul class="bookmark-list view-list">
      <li ld-bookmark-item data-bookmark-id="1">
        <div class="content">
          <div class="actions">
            <button type="button" data-action="mark_as_read" class="btn btn-link btn-sm" title="Mark as read">
              <svg class="action-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16"><use xlink:href="#ld-icon-unread-x"></use></svg>
            </button>
          </div>
        </div>
      </li>
    </ul>`;
  const mod = await setupDom(html);
  const li = document.querySelector("li[ld-bookmark-item]");
  const item = mountRow(mod);
  const button = li.querySelector('[data-action="mark_as_read"]');

  item._applyStateToRow("mark_as_read", true);
  assert.equal(button.textContent.trim(), "", "图标模式不应插入文案");
  assert.equal(
    button.querySelector("svg.action-icon use").getAttribute("href"),
    "#ld-icon-read-check",
    "图标模式应换成已读图标",
  );
});
