/**
 * 浮层收起后补发 click 的拦截器：只吞原矩形内的那一次，不误伤后续点击。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHTML } from "linkedom";

import { swallowNextClickIn } from "./swallow-click.js";

function setup() {
  const { window } = parseHTML(
    `<!doctype html><html><body><a id="link" href="#">x</a></body></html>`,
  );
  globalThis.window = window;
  globalThis.document = window.document;
  return window;
}

function clickAt(window, x, y) {
  // linkedom 没有 MouseEvent 构造器，坐标直接挂到 Event 实例上
  const event = new window.Event("click", { bubbles: true, cancelable: true });
  event.clientX = x;
  event.clientY = y;
  window.document.querySelector("#link").dispatchEvent(event);
  return event;
}

test("只吞掉落在原矩形内的那一次 click", () => {
  const window = setup();
  swallowNextClickIn({ left: 0, top: 0, right: 100, bottom: 40 });

  assert.equal(clickAt(window, 50, 20).defaultPrevented, true, "矩形内应被拦下");
  assert.equal(clickAt(window, 50, 20).defaultPrevented, false, "守卫只拦一次");
});

test("矩形外的 click 不拦，也不消耗守卫", () => {
  const window = setup();
  swallowNextClickIn({ left: 0, top: 0, right: 100, bottom: 40 }, 60);

  assert.equal(clickAt(window, 300, 300).defaultPrevented, false);
  assert.equal(clickAt(window, 10, 10).defaultPrevented, true, "守卫仍应生效");
});
