/**
 * 默认视图的行工具栏开合（「自动隐藏工具栏」模式）。
 *
 * 桌面悬停即显示、触屏点行切换、点击行外收起；同一条目开合会收起其他条目。
 * 表格视图（bookmark-table.js）与紧凑视图（bookmark-compact.js）各自管理，互不干涉。
 */
const LIST_SELECTOR = "ul.bookmark-list.view-list.toolbar-auto-hide";
const INTERACTIVE_SELECTOR =
  "a, button, input, textarea, label, ld-health-indicator";

function hideAllToolbars(keep) {
  document
    .querySelectorAll(`${LIST_SELECTOR} li[ld-bookmark-item].toolbar-visible`)
    .forEach((el) => {
      if (el !== keep) el.classList.remove("toolbar-visible");
    });
}

function onClick(event) {
  const item = event.target.closest(`${LIST_SELECTOR} li[ld-bookmark-item]`);
  if (!item) {
    hideAllToolbars(null);
    return;
  }
  // 交互元素自行处理点击（健康状态指示器自带弹窗），不触发工具栏开合
  if (event.target.closest(INTERACTIVE_SELECTOR)) return;

  hideAllToolbars(item);
  item.classList.toggle("toolbar-visible");
}

function bind() {
  if (window.__ldToolbarAutoHideBound) return;
  window.__ldToolbarAutoHideBound = true;
  document.addEventListener("click", onClick);
}

export function applyToolbarAutoHide(enabled) {
  document
    .querySelectorAll("ul.bookmark-list.view-list")
    .forEach((el) => el.classList.toggle("toolbar-auto-hide", enabled));

  if (enabled) {
    bind();
  } else {
    hideAllToolbars(null);
  }
}

document.addEventListener("turbo:load", () => {
  if (document.querySelector(LIST_SELECTOR)) bind();
});
