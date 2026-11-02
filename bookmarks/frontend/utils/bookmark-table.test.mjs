/**
 * 表格视图笔记列的回归测试。
 *
 * 覆盖笔记保存后的局部刷新：PATCH → notes_html 接口回填渲染层、
 * 同步隐藏原始源与折叠提示；清空笔记时清空渲染层并移除提示。
 * 以及待办勾选框：点它只切换完成态并保存源，不连带展开/折叠行。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  setupDom,
  tableRowHtml,
  mountTable,
  trackFetch,
  flush,
} from "./dom-test-env.mjs";

function cellOf(li) {
  return li.querySelector(".cell-notes");
}

test("笔记保存后通过 notes_html 局部刷新渲染层、隐藏源与提示", async () => {
  const mod = await setupDom(tableRowHtml());
  const calls = trackFetch("<p>new <strong>markdown</strong></p>");
  const table = mountTable(mod);
  const li = document.querySelector("li[ld-bookmark-item]");
  const cell = cellOf(li);

  await table.saveNotes(cell, li, "new **markdown**");

  const patch = calls.find((c) => c.method === "PATCH");
  assert.ok(patch, "应发出保存笔记的 PATCH");
  assert.equal(patch.url, "/api/bookmarks/1/");
  assert.deepEqual(patch.body, { notes: "new **markdown**" });

  assert.ok(
    calls.some((c) => c.url.includes("notes_html")),
    "保存后应请求 notes_html 渲染接口",
  );

  const rendered = cell.querySelector(".note-markdown");
  assert.match(
    rendered.innerHTML,
    /<strong>markdown<\/strong>/,
    "渲染层应回填接口返回的 Markdown HTML",
  );

  assert.equal(
    cell.querySelector(".note-source").value,
    "new **markdown**",
    "隐藏原始源应同步为最新 Markdown",
  );

  const wrap = cell.querySelector(".note.inline");
  assert.equal(
    wrap.getAttribute("title"),
    "new markdown",
    "折叠提示应取渲染结果的纯文本",
  );
});

test("清空笔记时清空渲染层并移除提示", async () => {
  const mod = await setupDom(
    tableRowHtml({
      notes: "**keep**",
      rendered: "<p>keep <strong>me</strong></p>",
    }),
  );
  trackFetch();
  const table = mountTable(mod);
  const li = document.querySelector("li[ld-bookmark-item]");
  const cell = cellOf(li);

  await table.saveNotes(cell, li, "");

  assert.equal(
    cell.querySelector(".note-markdown").innerHTML,
    "",
    "清空后渲染层不应残留旧内容",
  );
  assert.equal(cell.querySelector(".note-source").value, "");
  assert.equal(
    cell.querySelector(".note.inline").hasAttribute("title"),
    false,
    "清空后不应保留折叠提示",
  );
});

/* ── 待办勾选框 ── */

const TASKS = "- [ ] 未完成\n- [x] 已完成";
const TASKS_RENDERED =
  '<ul><li class="task-list-item"><span class="task-checkbox"></span>未完成</li>' +
  '<li class="task-list-item is-done"><span class="task-checkbox checked"></span>已完成</li></ul>';

/** 造一个带待办列表的行，返回真实 DOM 里的复选框与笔记格子 */
async function setupTasks({ notes = TASKS, rendered = TASKS_RENDERED } = {}) {
  const mod = await setupDom(tableRowHtml({ notes, rendered }));
  const calls = trackFetch();
  mountTable(mod);
  const li = document.querySelector("li[ld-bookmark-item]");
  const cell = cellOf(li);
  return {
    calls,
    li,
    cell,
    boxes: [...cell.querySelectorAll(".task-checkbox")],
  };
}

function click(element) {
  element.dispatchEvent(new window.Event("click", { bubbles: true }));
}

test("点未勾选的复选框：只切换完成态并保存源，不展开行", async () => {
  const { calls, li, cell, boxes } = await setupTasks();

  click(boxes[0]);
  await flush();

  assert.deepEqual(
    calls.find((c) => c.method === "PATCH")?.body,
    { notes: "- [x] 未完成\n- [x] 已完成" },
    "应把该待办行改成已完成并 PATCH 整段笔记",
  );
  assert.ok(boxes[0].classList.contains("checked"), "勾选框应就地勾上");
  assert.ok(
    boxes[0].closest("li").classList.contains("is-done"),
    "所属列表项应加上完成态",
  );
  assert.ok(boxes[1].classList.contains("checked"), "其他待办项不应被改动");
  assert.equal(
    cell.querySelector(".note-source").value,
    "- [x] 未完成\n- [x] 已完成",
    "隐藏源应同步为最新 Markdown",
  );

  // 展开是延迟触发的，等过 EXPAND_DELAY 再断言没被牵连
  await new Promise((resolve) => setTimeout(resolve, 260));
  assert.equal(
    li.classList.contains("is-expanded"),
    false,
    "点勾选框不应展开/折叠行",
  );
});

test("点已勾选的复选框：取消完成态并保存源", async () => {
  const { calls, cell, boxes } = await setupTasks();

  click(boxes[1]);
  await flush();

  assert.deepEqual(calls.find((c) => c.method === "PATCH")?.body, {
    notes: "- [ ] 未完成\n- [ ] 已完成",
  });
  assert.equal(boxes[1].classList.contains("checked"), false);
  assert.equal(boxes[1].closest("li").classList.contains("is-done"), false);
  assert.equal(
    cell.querySelector(".note-source").value,
    "- [ ] 未完成\n- [ ] 已完成",
  );
});

test("围栏代码块里的待办写法不算待办，按序数不会被误改", async () => {
  const notes = "```\n- [ ] 代码块里的\n```\n\n- [ ] 真的";
  const rendered =
    "<pre><code>- [ ] 代码块里的\n</code></pre>" +
    '<ul><li class="task-list-item"><span class="task-checkbox"></span>真的</li></ul>';
  const { calls } = await setupTasks({ notes, rendered });

  click(document.querySelector(".task-checkbox"));
  await flush();

  assert.deepEqual(calls.find((c) => c.method === "PATCH")?.body, {
    notes: "```\n- [ ] 代码块里的\n```\n\n- [x] 真的",
  });
});

test("保存失败时勾选框与源回滚到原状态", async () => {
  const { li, cell, boxes } = await setupTasks();
  globalThis.fetch = async (url, options = {}) =>
    options.method === "PATCH"
      ? { ok: false, json: async () => ({}) }
      : { ok: true, json: async () => ({}) };

  click(boxes[0]);
  await flush();

  assert.equal(boxes[0].classList.contains("checked"), false, "应回滚勾选态");
  assert.equal(
    boxes[0].closest("li").classList.contains("is-done"),
    false,
    "应回滚完成态样式",
  );
  assert.equal(cell.querySelector(".note-source").value, TASKS, "应回滚隐藏源");
  assert.equal(li.classList.contains("is-expanded"), false);
});
