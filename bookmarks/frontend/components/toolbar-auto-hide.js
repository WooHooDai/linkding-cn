/**
 * 书签条目工具栏的「自动隐藏」模式。
 *
 * 开关既可能来自页面加载（偏好已开启），也可能来自布局菜单的实时切换，
 * 因此绑定逻辑集中在这里，模板不再内联脚本。
 * 事件委托挂在 document 上，Turbo 换 DOM 后依然有效，只需绑定一次。
 */

const LIST_SELECTOR = "ul.bookmark-list.toolbar-auto-hide";
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
