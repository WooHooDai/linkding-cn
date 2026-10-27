/**
 * 书签条目工具栏的「自动隐藏」模式（列表/紧凑视图）。
 *
 * 表格视图（ul.view-table）不参与：它由 bookmark-table.js 提供行尾入口按钮。
 * 事件委托挂在 document 上，Turbo 换 DOM 后依然有效，只需绑定一次。
 */

const LIST_SELECTOR = "ul.bookmark-list.toolbar-auto-hide:not(.view-table)";
const ITEM_SELECTOR = `${LIST_SELECTOR} li[ld-bookmark-item]`;
const INTERACTIVE_SELECTOR =
  "a, button, input, textarea, label, ld-health-indicator";

function hideAllToolbars(keep) {
  document
    .querySelectorAll("li[ld-bookmark-item].toolbar-visible")
    .forEach((el) => {
      if (el !== keep) el.classList.remove("toolbar-visible");
    });
}

function onClick(event) {
  // 表格视图的操作栏由 bookmark-table.js 自己管理
  if (event.target.closest("ul.view-table")) return;
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
  document
    .querySelectorAll("ul.bookmark-list")
    .forEach((el) => el.classList.toggle("toolbar-auto-hide", enabled));

  if (enabled) {
    bindToolbarAutoHide();
  } else {
    hideAllToolbars(null);
  }
}

document.addEventListener("turbo:load", () => {
  if (document.querySelector(LIST_SELECTOR)) bindToolbarAutoHide();
});
