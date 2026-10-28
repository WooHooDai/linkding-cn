/**
 * 书签条目工具栏的「自动隐藏」模式（默认列表视图）。
 *
 * 表格视图（ul.view-table）由 bookmark-table.js 管理；紧凑视图（ul.view-compact）
 * 由模板内联脚本独立多开管理，二者都不参与这里的单开排他逻辑。
 * 事件委托挂在 document 上，Turbo 换 DOM 后依然有效，只需绑定一次。
 */

import { queryBookmarkLists } from "../utils/bookmark-list.js";

const LIST_SELECTOR =
  "ul.bookmark-list.toolbar-auto-hide:not(.view-table):not(.view-compact)";
const ITEM_SELECTOR = `${LIST_SELECTOR} li[ld-bookmark-item]`;
const INTERACTIVE_SELECTOR =
  "a, button, input, textarea, label, ld-health-indicator";

function hideAllToolbars(keep) {
  document
    .querySelectorAll("li[ld-bookmark-item].toolbar-visible")
    .forEach((el) => {
      // 紧凑视图的开合状态由自身脚本独立管理，不在此排他收起
      if (el === keep || el.closest("ul.view-compact")) return;
      el.classList.remove("toolbar-visible");
    });
}

function onClick(event) {
  // 表格视图由 bookmark-table.js 自己管理；紧凑视图由模板内联脚本独立多开管理
  if (event.target.closest("ul.view-table, ul.view-compact")) return;
  const item = event.target.closest(ITEM_SELECTOR);
  if (!item) {
    hideAllToolbars(null);
    return;
  }
  // 交互元素自行处理点击（健康状态指示器自带弹窗），不触发工具栏开合
  if (event.target.closest(INTERACTIVE_SELECTOR)) return;

  hideAllToolbars(item);
  item.classList.toggle("toolbar-visible");
}

export function bindToolbarAutoHide() {
  if (window.__ldToolbarAutoHideBound) return;
  window.__ldToolbarAutoHideBound = true;
  document.addEventListener("click", onClick);
}

export function applyToolbarAutoHide(enabled) {
  queryBookmarkLists().forEach((el) =>
    el.classList.toggle("toolbar-auto-hide", enabled),
  );

  if (enabled) {
    bindToolbarAutoHide();
  } else {
    hideAllToolbars(null);
  }
}

document.addEventListener("turbo:load", () => {
  if (document.querySelector(LIST_SELECTOR)) bindToolbarAutoHide();
});
