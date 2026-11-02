/**
 * 行视图（默认 / 紧凑）里笔记待办勾选框的行为。
 *
 * 表格视图的同类用例在 bookmark-table.test.mjs；三者共用
 * bookmark-notes-task.js。这里守住行视图这条链路：点勾选框只切完成态、
 * 同步隐藏源与实例里的笔记副本，不连带触发行展开。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { setupDom, rowHtml, trackFetch, flush } from "./dom-test-env.mjs";

const NOTES = "- [ ] 未完成\n- [x] 已完成";
const NOTES_HTML =
  '<ul><li class="task-list-item"><span class="task-checkbox"></span>未完成</li>' +
  '<li class="task-list-item is-done"><span class="task-checkbox checked"></span>已完成</li></ul>';

/** 建一条带笔记的行，行行为与紧凑视图行为都挂上 */
async function setupRow() {
  const mod = await setupDom(rowHtml({ notes: NOTES, notesHtml: NOTES_HTML }));
  const calls = trackFetch();
  mod.applyBehaviors(document.body, [
    "ld-bookmark-compact",
    "ld-bookmark-item",
  ]);
  const li = document.querySelector("li[ld-bookmark-item]");
  const row = li.__behaviors.find((behavior) => behavior.bookmarkId);
  return { calls, li, row, boxes: [...li.querySelectorAll(".task-checkbox")] };
}

function click(element) {
  element.dispatchEvent(new window.Event("click", { bubbles: true }));
}

test("默认视图点勾选框：切换完成态并保存笔记源", async () => {
  const { calls, li, row, boxes } = await setupRow();

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
  assert.equal(
    li.querySelector(".inline-edit-textarea").value,
    "- [x] 未完成\n- [x] 已完成",
    "隐藏源应同步",
  );
  assert.equal(
    row.originalNotes,
    "- [x] 未完成\n- [x] 已完成",
    "实例里的笔记副本要同步，否则之后编辑笔记会把勾选改回去",
  );
});

test("紧凑视图点勾选框只切完成态，不展开该行", async () => {
  const { li, boxes } = await setupRow();

  click(boxes[1]);
  await flush();

  assert.equal(boxes[1].classList.contains("checked"), false);
  assert.equal(
    boxes[1].closest("li").classList.contains("is-done"),
    false,
    "取消完成态后应去掉删除线样式",
  );
  assert.equal(
    li.classList.contains("toolbar-visible"),
    false,
    "点勾选框不应连带触发行展开",
  );
});
