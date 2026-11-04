import { Behavior, registerBehavior } from "./runtime.js";
import { queryBookmarkList } from "../utils/bookmark-list.js";

// ==========================================
// 滚动位置记忆
// ==========================================

function restoreBookmarkListScrollPosition() {
  const scroller = document.scrollingElement;
  if (scroller && queryBookmarkList()) {
    let data = null;
    try {
      data = JSON.parse(localStorage.getItem("ld:bookmark-list:scroll"));
    } catch {}

    if (data && data.returnUrl && data.position != null) {
      if (window.location.pathname === data.returnUrl) {
        scroller.scrollTo(0, Number(data.position) || 0);
      }
      localStorage.removeItem("ld:bookmark-list:scroll");
    }
  }
}

document.addEventListener(
  "DOMContentLoaded",
  restoreBookmarkListScrollPosition,
);
document.addEventListener("turbo:load", restoreBookmarkListScrollPosition);

// ==========================================
// 按日期分组：折叠状态
// ==========================================

// 两层：按组记录（局部覆盖）+ 全局默认（无记录的组遵循）
const GROUP_COLLAPSE_STORAGE_KEY = "ld:bookmarks:group-collapsed";
const GROUP_COLLAPSE_DEFAULT_KEY = "ld:bookmarks:group-default-collapsed";

function loadCollapsedMap() {
  try {
    return JSON.parse(localStorage.getItem(GROUP_COLLAPSE_STORAGE_KEY)) || {};
  } catch {
    return {};
  }
}

function loadGroupDefaultCollapsed() {
  return localStorage.getItem(GROUP_COLLAPSE_DEFAULT_KEY) === "true";
}

function saveGroupDefaultCollapsed(collapsed) {
  try {
    localStorage.setItem(
      GROUP_COLLAPSE_DEFAULT_KEY,
      collapsed ? "true" : "false",
    );
  } catch {}
}

// 合并写：保留其他页的组记录，仅更新当前页各组的折叠状态
function saveCollapsedMap(headers) {
  const map = loadCollapsedMap();
  headers.forEach((header) => {
    map[header.dataset.groupKey] = header.classList.contains("group-collapsed");
  });
  try {
    localStorage.setItem(GROUP_COLLAPSE_STORAGE_KEY, JSON.stringify(map));
  } catch {}
}

// 宽屏布局菜单与窄屏更多菜单各有一份按钮，全部同步
function refreshAllGroupsButton(headers) {
  const expanded = headers.some(
    (header) => !header.classList.contains("group-collapsed"),
  );
  document.querySelectorAll(".group-toggle-all").forEach((btn) => {
    btn.classList.toggle("all-collapsed", !expanded);
    const label = expanded
      ? btn.dataset.labelCollapse
      : btn.dataset.labelExpand;
    btn.setAttribute("aria-label", label);
    btn.setAttribute("title", label);
    // 抽屉里的按钮直接用文字表达当前动作
    if (btn.dataset.labelCollapse) btn.textContent = label;
  });
}

function setGroupCollapsed(header, collapsed) {
  const toggle = header.querySelector(".group-toggle");
  if (!toggle) return;
  header.classList.toggle("group-collapsed", collapsed);
  toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  let el = header.nextElementSibling;
  while (el && !el.classList.contains("group-header")) {
    el.classList.toggle("group-hidden", collapsed);
    el = el.nextElementSibling;
  }
}

/** 按日期分组的折叠状态：恢复记忆 + 组标题/全部折叠按钮的点击。
 *  挂在列表 ul 上，Turbo stream 替换列表时随 Behavior 一起重建。 */
class GroupCollapseBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.onClick = null;
    this.headers = Array.from(
      element.querySelectorAll(":scope > li.group-header"),
    );
    if (!this.headers.length) return;

    this.onClick = (event) => {
      if (event.target.closest(".group-toggle-all")) {
        const collapseAll = this.headers.some(
          (header) => !header.classList.contains("group-collapsed"),
        );
        this.headers.forEach((header) =>
          setGroupCollapsed(header, collapseAll),
        );
        saveCollapsedMap(this.headers);
        // 无记录的组将遵循该全局默认
        saveGroupDefaultCollapsed(collapseAll);
        refreshAllGroupsButton(this.headers);
        return;
      }
      const toggle = event.target.closest(".group-toggle");
      if (!toggle) return;
      const header = toggle.closest(".group-header");
      if (!header || !element.contains(header)) return;
      setGroupCollapsed(header, !header.classList.contains("group-collapsed"));
      saveCollapsedMap(this.headers);
      refreshAllGroupsButton(this.headers);
    };
    // 全部折叠按钮在菜单里，不在 ul 内，故挂 document
    document.addEventListener("click", this.onClick);

    this.restore();
  }

  // 有记录的组按记录，无记录的组遵循全局默认
  restore() {
    const savedMap = loadCollapsedMap();
    const defaultCollapsed = loadGroupDefaultCollapsed();
    this.headers.forEach((header) => {
      const key = header.dataset.groupKey;
      const collapsed =
        key && Object.hasOwn(savedMap, key)
          ? savedMap[key] === true
          : defaultCollapsed;
      if (collapsed) setGroupCollapsed(header, true);
    });
    refreshAllGroupsButton(this.headers);
  }

  destroy() {
    if (this.onClick) document.removeEventListener("click", this.onClick);
  }
}

registerBehavior("ld-bookmark-groups", GroupCollapseBehavior);
