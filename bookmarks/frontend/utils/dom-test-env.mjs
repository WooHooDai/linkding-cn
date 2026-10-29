/**
 * 行行为测试用的最小 DOM 环境（linkedom + 浏览器 API 垫片）。
 *
 * bookmark-row.js 是 ESM 语法、顶层依赖 document/window，且注册表在 runtime.js
 * 里——两者必须打进同一个 bundle 才共享注册表，所以走 esbuild stdin 入口。
 */
import { buildSync } from "esbuild";
import { parseHTML } from "linkedom";

const SHIMS = `
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
  window.gettext = (s) => s;
  window.scrollTo = () => {};
  window.scrollY = 0;
  window.pageYOffset = 0;
  // 行行为滚动到顶部时会读 scrollingElement，linkedom 未实现
  Object.defineProperty(window.document, "scrollingElement", {
    get: () => window.document.documentElement,
    configurable: true,
  });
  window.open = () => null;
  const proto = window.HTMLElement.prototype;
  proto.setSelectionRange = function () {};
  proto.focus = function () {};
  proto.scrollIntoView = function () {};
  // 布局相关 API 在 linkedom 里恒为 0，测试只关心结构不关心像素
  Object.defineProperty(proto, "clientWidth", { get: () => 400, configurable: true });
  Object.defineProperty(proto, "offsetWidth", { get: () => 400, configurable: true });
  Object.defineProperty(proto, "offsetHeight", { get: () => 20, configurable: true });
  Object.defineProperty(proto, "scrollHeight", { get: () => 20, configurable: true });
`;

// 每个用例都要一份全新的模块实例：行行为里的 activeEditor / editorOverlay
// 是模块级状态，复用同一 data: URL 会让上一个用例的编辑器残留。
let moduleSeq = 0;

/**
 * 在给定 HTML 上建立全局 DOM，返回打包后的 runtime + 行行为模块。
 * @param {string} html 页面片段（应包含 ul.bookmark-list 与 li[ld-bookmark-item]）
 */
export async function setupDom(html) {
  const { window } = parseHTML(`<!doctype html><html><body>${html}</body></html>`);

  globalThis.window = window;
  globalThis.document = window.document;
  // Node 的 navigator 是只读 getter，需要用 defineProperty 覆盖
  Object.defineProperty(globalThis, "navigator", {
    value: window.navigator,
    configurable: true,
    writable: true,
  });
  for (const name of [
    "MutationObserver",
    "HTMLElement",
    "CustomEvent",
    "Node",
    "Element",
    "Event",
    "DocumentFragment",
  ]) {
    globalThis[name] = window[name];
  }
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
  globalThis.getComputedStyle = () => ({ maxHeight: "240px" });
  globalThis.localStorage = {
    store: {},
    getItem(k) {
      return this.store[k] ?? null;
    },
    setItem(k, v) {
      this.store[k] = String(v);
    },
    removeItem(k) {
      delete this.store[k];
    },
  };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

  // getCSRFToken 会读 document.cookie
  Object.defineProperty(window.document, "cookie", {
    value: "",
    configurable: true,
    writable: true,
  });

  // ld-tag-autocomplete 在浏览器里是 lit 组件，linkedom 不执行它；
  // 用最小桩渲染出 input 并提供 commit 事件，行为层代码即可照常驱动
  class FakeTagAutocomplete extends window.HTMLElement {
    connectedCallback() {
      if (this._rendered) return;
      this._rendered = true;
      const input = this.ownerDocument.createElement("textarea");
      input.value = this.getAttribute("input-value") || "";
      this.appendChild(input);
      this.updateComplete = Promise.resolve();
    }
  }
  window.customElements.define("ld-tag-autocomplete", FakeTagAutocomplete);

  // SHIMS 在 window 就绪后求值
  new Function("window", SHIMS)(window);

  const entry = `
    export * from "./bookmarks/frontend/components/runtime.js";
    export * from "./bookmarks/frontend/components/bookmark-row.js";
    export const __seq = ${moduleSeq++};
  `;
  const result = buildSync({
    stdin: { contents: entry, resolveDir: process.cwd(), loader: "js" },
    bundle: true,
    format: "esm",
    platform: "node",
    write: false,
  });
  const code = result.outputFiles[0].text;
  return import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
}

/** 构造一个最小的书签行；withCell 模拟表格视图的格子行 */
export function rowHtml({
  id = "1",
  title = "Hello",
  description = "Desc",
  tags = ["a"],
  withCell = false,
} = {}) {
  const tagLinks = tags
    .map((t) => `<a href="?q=%23${t}">${t}</a>`)
    .join(" ");
  const inner = `
    <div class="title"><a class="title-link" href="https://example.com"><span>${title}</span></a></div>
    <div class="description">
      <div class="description-container truncate"><span class="description-text">${description}</span></div>
    </div>
    <div class="actions">
      <button class="quick-edit-btn" data-quick-edit="title">t</button>
      <button class="quick-edit-btn" data-quick-edit="description">d</button>
      <button class="quick-edit-btn" data-quick-edit="tags">g</button>
      <span class="tags">${tagLinks}</span>
    </div>`;
  const body = withCell
    ? `<div class="cell cell-title">${inner}</div>`
    : `<div class="content">${inner}</div>`;
  return `
    <ul class="bookmark-list ${withCell ? "view-table" : "view-list"}">
      <li ld-bookmark-item data-bookmark-id="${id}">${body}</li>
    </ul>`;
}

/** 挂载行为并返回行行为实例 */
export function mountRow(mod, selector = "li[ld-bookmark-item]") {
  mod.applyBehaviors(document.body, ["ld-bookmark-item"]);
  const element = document.querySelector(selector);
  return element.__behaviors[0];
}
