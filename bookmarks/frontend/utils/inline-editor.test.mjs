/**
 * 就地编辑器统一接线（wireInlineEditor）的测试。
 *
 * 列表行与表格格子共用这段键盘/失焦逻辑，行为必须一致：
 * Escape 撤销、回车保存（多行用 Cmd/Ctrl+Enter）、失焦保存、值未变不保存。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHTML } from "linkedom";

import { wireInlineEditor } from "../components/bookmark-row-editor.js";

const { window } = parseHTML("<html><body></body></html>");
globalThis.document = window.document;
globalThis.window = window;

/** 造一个编辑器元素并接上 wireInlineEditor，记录回调 */
function setup({ value = "旧", multiline = false } = {}) {
  const editor = document.createElement(multiline ? "textarea" : "input");
  editor.value = value;
  const events = [];
  const close = wireInlineEditor(editor, {
    originalValue: value,
    multiline,
    onClose: (info) => events.push({ type: "close", ...info }),
    onSave: (next) => {
      events.push({ type: "save", value: next });
    },
    onError: () => events.push({ type: "error" }),
  });
  const key = (k, extra = {}) => {
    const event = new window.Event("keydown", { bubbles: true });
    event.key = k;
    Object.assign(event, extra);
    editor.dispatchEvent(event);
  };
  return { editor, events, close, key };
}

test("回车保存改动后的值", () => {
  const { editor, events, key } = setup();
  editor.value = " 新 ";
  key("Enter");

  assert.deepEqual(events, [
    { type: "close", save: true, value: "新", changed: true },
    { type: "save", value: "新" },
  ]);
});

test("值未变时不保存", () => {
  const { events, key } = setup();
  key("Enter");

  assert.deepEqual(events, [
    { type: "close", save: true, value: "旧", changed: false },
  ]);
});

test("Escape 撤销并还原输入框内容", () => {
  const { editor, events, key } = setup();
  editor.value = "新";
  key("Escape");

  assert.equal(editor.value, "旧", "Escape 应把输入框还原");
  assert.deepEqual(events, [
    { type: "close", save: false, value: "旧", changed: false },
  ]);
});

test("多行编辑器回车换行，Cmd+Enter 才保存", () => {
  const { editor, events, key } = setup({ multiline: true });
  editor.value = "第一行\n第二行";

  key("Enter");
  assert.deepEqual(events, [], "多行下回车不应关闭编辑器");

  key("Enter", { metaKey: true });
  assert.equal(events.length, 2);
  assert.deepEqual(events[1], { type: "save", value: "第一行\n第二行" });
});

test("失焦保存", () => {
  const { editor, events } = setup();
  editor.value = "新";
  editor.dispatchEvent(new window.Event("blur"));

  assert.deepEqual(events[1], { type: "save", value: "新" });
});

test("重复关闭只生效一次", () => {
  const { editor, events, key } = setup();
  editor.value = "新";
  key("Enter");
  editor.dispatchEvent(new window.Event("blur"));

  assert.equal(events.length, 2, "失焦不应重复触发一次关闭");
});
