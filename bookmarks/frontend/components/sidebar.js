import Sortable from "sortablejs";
import { Behavior, registerBehavior } from "./runtime.js";
import { getCSRFToken } from "../utils/csrf.js";
import { gettext } from "../utils/i18n.js";
import { showToast } from "./toast.js";
import {
  getSidebarScope,
  setSidebarState,
  syncSidebarStateCache,
} from "../state/sidebar-state.js";
import { panelsFitTogether, syncPanelSpace } from "../utils/panel-space.js";

// ==========================================
// 展开折叠按钮
// ==========================================

// 模板 data-toggle-storage-key → ld:sidebar:collapse JSON 字段名
const COLLAPSE_KEY_MAP = {
  userSummarySectionState: "summary",
  userSummaryActivityState: "activity",
  tagSectionState: "tags",
  authorSectionState: "authors",
  domainSectionState: "domains",
  bundleSectionState: "bundles",
};

const COLLAPSE_STORAGE_KEY = "ld:sidebar:collapse";

function _readCollapseMap() {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

const BUNDLES_KEY = "ld:sidebar:bundles";
const DOMAINS_KEY = "ld:sidebar:domains";

class CollapseButtonBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.collapseField =
      COLLAPSE_KEY_MAP[element.dataset.toggleStorageKey] || null;
    this.targetSelector =
      element.dataset.toggleTargetSelector || ".section-content";
    this.toggleBtn = element.querySelector("button");
    this.content = element.querySelector(this.targetSelector);

    this.onClick = this.onClick.bind(this);

    if (this.toggleBtn) {
      this.toggleBtn.addEventListener("click", this.onClick);
      this.restoreState();
    }
  }

  destroy() {
    if (this.toggleBtn)
      this.toggleBtn.removeEventListener("click", this.onClick);
  }

  onClick() {
    if (!this.toggleBtn || !this.content) return;

    const expanded = this.toggleBtn.getAttribute("aria-expanded") === "true";
    const newState = !expanded;
    this.toggleBtn.setAttribute("aria-expanded", newState);
    this.content.style.display = newState ? "" : "none";

    if (this.collapseField) {
      const map = _readCollapseMap();
      map[this.collapseField] = newState;
      localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(map));
    }
  }

  restoreState() {
    if (!this.toggleBtn || !this.content) return;
    const expanded = this.collapseField
      ? _readCollapseMap()[this.collapseField] !== false
      : true;
    this.toggleBtn.setAttribute("aria-expanded", expanded);
    this.content.style.display = expanded ? "" : "none";
  }
}
registerBehavior("ld-collapse-button", CollapseButtonBehavior);

class BundleCollapseButton extends Behavior {
  constructor(element) {
    super(element);
    this.onBundleClick = this.onBundleClick.bind(this);
    element.addEventListener("click", this.onBundleClick);
    this.restoreBundleState();
  }

  destroy() {
    this.element.removeEventListener("click", this.onBundleClick);
  }

  onBundleClick(e) {
    const btn = e.target.closest(".folder-toggle");
    if (!btn) return;

    const folderItem = btn.closest("li");
    const bundleId = folderItem.dataset.bundleId;

    const expanded = btn.getAttribute("aria-expanded") === "true";
    const newState = !expanded;

    btn.setAttribute("aria-expanded", newState);
    this.setBundleState(bundleId, newState);

    let next = folderItem.nextElementSibling;
    while (next && next.dataset.folder !== "true") {
      next.style.display = newState ? "" : "none";
      next = next.nextElementSibling;
    }
  }

  setBundleState(bundleId, expanded) {
    if (!bundleId) return;
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(BUNDLES_KEY) || "{}");
    } catch {}
    state[bundleId] = expanded;
    localStorage.setItem(BUNDLES_KEY, JSON.stringify(state));
  }

  restoreBundleState() {
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(BUNDLES_KEY) || "{}");
    } catch {}

    this.element.querySelectorAll(".folder-toggle").forEach((btn) => {
      const folderItem = btn.closest("li");
      const bundleId = folderItem.dataset.bundleId;
      if (!bundleId) return;

      const expanded = state[bundleId] !== false;
      btn.setAttribute("aria-expanded", expanded);

      let next = folderItem.nextElementSibling;
      while (next && next.dataset.folder !== "true") {
        next.style.display = expanded ? "" : "none";
        next = next.nextElementSibling;
      }
    });
  }
}
registerBehavior("ld-bundle-menu", BundleCollapseButton);

class DomainTreeBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.onTreeClick = this.onTreeClick.bind(this);
    this.element.addEventListener("click", this.onTreeClick);
    this.restoreTreeState();
  }

  destroy() {
    this.element.removeEventListener("click", this.onTreeClick);
  }

  onTreeClick(event) {
    const button = event.target.closest(".folder-toggle");
    if (button && this.element.contains(button)) {
      this.toggleTreeItem(button.closest(".domain-menu-item"), event);
      return;
    }

    const row = event.target.closest(".domain-row");
    if (row && this.element.contains(row)) {
      const item = row.closest(".domain-menu-item");
      if (
        item?.dataset.domainGroup === "true" &&
        item?.dataset.domainHasChildren === "true"
      ) {
        this.toggleTreeItem(item, event);
      }
    }
  }

  async toggleTreeItem(item, event) {
    if (!item) return;
    const button = item.querySelector(":scope > .domain-row .folder-toggle");
    if (!button) return;
    if (event) event.preventDefault();

    // P0: guard against rapid clicks while loading
    if (item.dataset.loading === "true") return;

    const expanded = button.getAttribute("aria-expanded") === "true";
    const newState = !expanded;

    button.setAttribute("aria-expanded", newState);

    const nodeId = item.dataset.domainNodeId;

    if (newState && item.dataset.loaded !== "true") {
      // Children not yet loaded — fetch from server.
      await this.loadChildren(item, nodeId);
    } else {
      // Already loaded — just show/hide.
      const childList = item.querySelector(":scope > ul.domain-children");
      if (childList) childList.style.display = newState ? "" : "none";
    }

    this.setNodeState(nodeId, newState);
  }

  _buildSearchParams(nodeId) {
    // Forward all search/filter params that affect bookmark querying (and thus domain counts).
    // Exclude pure UI state params that don't affect data: sort, details.
    const SKIP = new Set(["node_id", "ctx", "sort", "details", "view_mode"]);
    const sp = new URLSearchParams();
    sp.set("node_id", nodeId);
    const page = document.querySelector("[data-ctx]");
    sp.set("ctx", page?.dataset.ctx || "active");
    // Include view_mode so browser HTTP cache differentiates icon/full mode responses
    const viewMode = this.element?.dataset.domainViewMode || "full";
    sp.set("view_mode", viewMode);
    // Include domain config fingerprint to bust sessionStorage cache when
    // custom domain normalization rules change
    const fingerprint = this.element?.dataset.domainConfigFingerprint;
    if (fingerprint) sp.set("fp", fingerprint);
    for (const [key, val] of new URLSearchParams(window.location.search)) {
      if (!SKIP.has(key) && val) sp.set(key, val);
    }
    return sp;
  }

  async loadChildren(item, nodeId) {
    // P0: loading guard
    if (item.dataset.loading === "true") return;
    item.dataset.loading = "true";

    const sp = this._buildSearchParams(nodeId);
    // sessionStorage cache key matches the URL params (which now include view_mode)
    const cacheKey = `domain-tree:${sp.toString()}`;
    const button = item.querySelector(":scope > .domain-row .folder-toggle");

    // 1. Check sessionStorage cache first
    let html = null;
    try {
      html = sessionStorage.getItem(cacheKey);
    } catch {}

    if (html === null) {
      // 2. Cache miss — fetch from server
      try {
        const resp = await fetch(`/domain-tree/children?${sp.toString()}`);
        if (!resp.ok) {
          this._loadChildrenFailed(item, button);
          return;
        }
        html = await resp.text();
        try {
          sessionStorage.setItem(cacheKey, html);
        } catch {}
      } catch {
        this._loadChildrenFailed(item, button);
        return;
      }
    }

    item.dataset.loading = "false";

    // 3. Insert into DOM
    if (html && html.trim()) {
      const ul = document.createElement("ul");
      // Match icon mode: if the domain tree is in icon mode, add grid layout class
      const menu = item.closest(".domain-menu");
      const isIconMode = menu?.dataset.domainViewMode === "icon";
      ul.className = isIconMode
        ? "domain-children domain-children-icon"
        : "domain-children";
      ul.innerHTML = html;
      item.appendChild(ul);
      item.dataset.loaded = "true";
    } else {
      item.dataset.domainHasChildren = "false";
      if (button) button.remove();
    }
  }

  _loadChildrenFailed(item, button) {
    item.dataset.loading = "false";
    // Revert aria-expanded to collapsed state
    if (button) button.setAttribute("aria-expanded", "false");
  }

  setNodeState(nodeId, expanded) {
    if (!nodeId) return;
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(DOMAINS_KEY) || "{}");
    } catch {}
    state[nodeId] = expanded;
    localStorage.setItem(DOMAINS_KEY, JSON.stringify(state));
  }

  restoreTreeState() {
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(DOMAINS_KEY) || "{}");
    } catch {}

    this.element
      .querySelectorAll('.domain-menu-item[data-domain-has-children="true"]')
      .forEach((item) => {
        const button = item.querySelector(
          ":scope > .domain-row .folder-toggle",
        );
        if (!button) return;

        const nodeId = item.dataset.domainNodeId;
        const hasSelectedDescendant = item.querySelector(
          ".domain-menu-item.selected",
        );
        const expanded = hasSelectedDescendant ? true : state[nodeId] === true;

        button.setAttribute("aria-expanded", expanded);

        if (item.dataset.loaded === "true") {
          // Already loaded (server-rendered group nodes) — toggle display
          const childList = item.querySelector(":scope > ul.domain-children");
          if (childList) childList.style.display = expanded ? "" : "none";
        } else if (expanded) {
          // Not loaded yet but should be expanded — fetch from server
          this.loadChildren(item, nodeId);
        }
      });
  }
}
registerBehavior("ld-domain-tree", DomainTreeBehavior);

const TAG_TREE_KEY = "ld:sidebar:tag-tree";

/**
 * Build the ancestor path (root → … → this node) by walking up the DOM.
 * Returns a comma-separated string of tag names.
 */
function tagTreePath(node) {
  const parts = [];
  let el = node;
  while (el) {
    const name = el.dataset.tagName;
    if (name) parts.unshift(name);
    el = el.parentElement?.closest(".tag-tree-node");
  }
  return parts.join(",");
}

class TagTreeBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.onTreeClick = this.onTreeClick.bind(this);
    this.element.addEventListener("click", this.onTreeClick);
    this.restoreTreeState();
  }

  destroy() {
    this.element.removeEventListener("click", this.onTreeClick);
  }

  onTreeClick(event) {
    const button = event.target.closest(".tag-tree-toggle");
    if (button && this.element.contains(button)) {
      const node = button.closest(".tag-tree-node");
      if (node) {
        this.toggleNode(node);
        return;
      }
    }
    const row = event.target.closest(".tag-tree-row");
    if (row && this.element.contains(row)) {
      const node = row.closest(".tag-tree-node");
      if (node?.dataset.hasChildren === "true" && !event.target.closest("a")) {
        this.toggleNode(node);
      }
    }
  }

  async toggleNode(node) {
    const expanded = node.dataset.expanded === "true";
    const newState = !expanded;
    node.dataset.expanded = newState;

    const button = node.querySelector(
      ":scope > .tag-tree-row .tag-tree-toggle",
    );
    if (button) button.setAttribute("aria-expanded", newState);

    const path = tagTreePath(node);

    if (newState && node.dataset.loaded !== "true") {
      // Children not yet loaded — fetch from server.
      await this.loadChildren(node, path);
    } else {
      // Already loaded — just show/hide.
      const childList = node.querySelector(":scope > ul.tag-tree-children");
      if (childList) childList.style.display = newState ? "" : "none";
    }

    this.setNodeState(path, newState);
  }

  _buildSearchParams(path) {
    const sp = new URLSearchParams(window.location.search);
    sp.set("path", path);
    const p = window.location.pathname;
    let ctx = "active";
    if (p.includes("/highlights")) ctx = "highlights";
    else if (p.includes("/shared")) ctx = "shared";
    else if (p.includes("/archived")) ctx = "archived";
    else if (p.includes("/trash")) ctx = "trash";
    sp.set("ctx", ctx);
    return sp;
  }

  async loadChildren(node, path) {
    const sp = this._buildSearchParams(path);
    const cacheKey = `tag-tree:${sp.toString()}`;
    const button = node.querySelector(
      ":scope > .tag-tree-row .tag-tree-toggle",
    );

    // 1. Check sessionStorage cache first
    let html = null;
    try {
      html = sessionStorage.getItem(cacheKey);
    } catch {}

    if (html === null) {
      // 2. Cache miss — fetch from server
      try {
        const resp = await fetch(`/tag-tree/children?${sp.toString()}`);
        if (!resp.ok) return;
        html = await resp.text();
        try {
          sessionStorage.setItem(cacheKey, html);
        } catch {}
      } finally {
        // no-op
      }
    }

    // 3. Insert into DOM
    if (html && html.trim()) {
      const ul = document.createElement("ul");
      ul.className = "tag-tree-children";
      ul.innerHTML = html;
      node.appendChild(ul);
      node.dataset.loaded = "true";
    } else {
      node.dataset.hasChildren = "false";
      if (button) button.remove();
    }
  }

  setNodeState(path, expanded) {
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(TAG_TREE_KEY) || "{}");
    } catch {}
    state[path] = expanded;
    localStorage.setItem(TAG_TREE_KEY, JSON.stringify(state));
  }

  restoreTreeState() {
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(TAG_TREE_KEY) || "{}");
    } catch {}

    this.element
      .querySelectorAll(".tag-tree-node[data-has-children='true']")
      .forEach((node) => {
        const path = tagTreePath(node);
        if (state[path]) {
          node.dataset.expanded = "true";
          const button = node.querySelector(
            ":scope > .tag-tree-row .tag-tree-toggle",
          );
          if (button) button.setAttribute("aria-expanded", "true");
          this.loadChildren(node, path);
        }
      });
  }
}
registerBehavior("ld-tag-tree", TagTreeBehavior);

// ==========================================
// 侧边栏滚动位置记忆
// ==========================================

function readScrollData(key) {
  try {
    return JSON.parse(localStorage.getItem(key));
  } catch {
    return null;
  }
}

function saveScrollPosition(key, selector) {
  const el = document.querySelector(selector);
  if (!el) return;

  const scrollTop = el.scrollTop;
  const scrollHeight = el.scrollHeight;
  const prev = readScrollData(key);
  const slots = prev?.slots || {};

  // 每个可滚动高度记录一个滚动位置
  // 滚动高度变化时，直接匹配得到滚动位置，实现精准匹配
  slots[scrollHeight] = scrollTop;

  // 最多保留 50 条，淘汰最早插入的
  const keys = Object.keys(slots);
  while (keys.length > 50) {
    delete slots[keys[0]];
    keys.shift();
  }

  localStorage.setItem(
    key,
    JSON.stringify({ s: scrollTop, h: scrollHeight, slots }),
  );
}

function applyScrollPosition(key, selector) {
  const el = document.querySelector(selector);
  if (!el) return;

  const data = readScrollData(key);
  if (!data) return;

  // 双重 rAF：第一帧绘制完成后再读 scrollHeight，此时布局已算完，无强制重排
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const target = data.slots?.[el.scrollHeight] ?? data.s;
      el.scrollTop = Math.min(target, el.scrollHeight - el.clientHeight);
    });
  });
}

function createScrollHandler(saveFn, delay) {
  let timer;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(saveFn, delay);
  };
}

const SIDEBAR_KEY = "ld:sidebar:scroll";
const SIDEBAR_SEL = ".sidebar";

// 问题 3 修复：独立变量替代在布尔值上挂属性
let sidebarRestoring = false;
let sidebarContentChanging = false;
let sidebarContentChangingTimer = null;

const saveSidebar = () => {
  // 恢复期间或内容变化期间，跳过保存
  if (sidebarRestoring || sidebarContentChanging) return;
  saveScrollPosition(SIDEBAR_KEY, SIDEBAR_SEL);
};
const restoreSidebar = () => {
  sidebarRestoring = true;
  applyScrollPosition(SIDEBAR_KEY, SIDEBAR_SEL);
  // 问题 6 修复：双重 rAF + setTimeout 兜底，确保 scrollTop 设置完成且对应 scroll 事件已处理
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      setTimeout(() => {
        sidebarRestoring = false;
      }, 50);
    });
  });
};
const onSidebarScroll = createScrollHandler(saveSidebar, 300);

function bindSidebarScrollListener() {
  const el = document.querySelector(SIDEBAR_SEL);
  if (el) el.addEventListener("scroll", onSidebarScroll, { passive: true });
}

function unbindSidebarScrollListener() {
  const el = document.querySelector(SIDEBAR_SEL);
  if (el) el.removeEventListener("scroll", onSidebarScroll);
}

// 监听侧边栏内容变化（如筛选/取消筛选），当内容变长时恢复滚动位置
let sidebarContentObserver = null;
let prevSidebarScrollHeight = 0;

function setupSidebarContentObserver() {
  const el = document.querySelector(SIDEBAR_SEL);
  if (!el) return;

  prevSidebarScrollHeight = el.scrollHeight;

  sidebarContentObserver = new MutationObserver(() => {
    const newHeight = el.scrollHeight;
    const prevHeight = prevSidebarScrollHeight;
    prevSidebarScrollHeight = newHeight;

    if (newHeight === prevHeight) return;

    // 标记内容正在变化，阻止 scroll 事件覆盖已保存的滚动位置
    sidebarContentChanging = true;
    clearTimeout(sidebarContentChangingTimer);
    sidebarContentChangingTimer = setTimeout(() => {
      sidebarContentChanging = false;
    }, 500);

    // 内容变长 → 可能是取消筛选导致内容恢复，尝试恢复滚动位置
    if (newHeight > prevHeight) {
      restoreSidebar();
    }
  });

  sidebarContentObserver.observe(el, { childList: true, subtree: true });
}

function disconnectSidebarContentObserver() {
  if (sidebarContentObserver) {
    sidebarContentObserver.disconnect();
    sidebarContentObserver = null;
  }
  prevSidebarScrollHeight = 0;
  clearTimeout(sidebarContentChangingTimer);
  sidebarContentChanging = false;
}

// ==========================================
// 侧边栏模块：拖拽排序与「设置」模块
// ==========================================

// 两种侧边栏字段名不同，保存入口相同
const SIDEBAR_PREF_FIELDS = {
  bookmarks: {
    show: "show_sidebar",
    sticky: "sticky_side_panel",
    modules: "sidebar_modules",
  },
  highlights: {
    show: "show_highlights_sidebar",
    sticky: "highlights_sticky_side_panel",
    modules: "highlights_sidebar_modules",
  },
};

function sidebarModuleWrappers(sidebar) {
  return Array.from(sidebar.children).filter((el) =>
    el.matches("[data-sidebar-module]"),
  );
}

function findModuleWrapper(sidebar, key) {
  return (
    sidebarModuleWrappers(sidebar).find(
      (el) => el.dataset.sidebarModule === key,
    ) || null
  );
}

function sidebarModuleKeys(sidebar) {
  return sidebarModuleWrappers(sidebar).map((el) => el.dataset.sidebarModule);
}

function moduleListItems(list) {
  return Array.from(list.querySelectorAll("[data-module-key]"));
}

// 按 keys 重排容器内的直接子元素，不在 keys 里的保持原位（如侧边栏的关闭栏）
function reorderChildren(container, keys, selector, keyOf) {
  const items = new Map(
    Array.from(container.querySelectorAll(`:scope > ${selector}`)).map((el) => [
      keyOf(el),
      el,
    ]),
  );
  keys.forEach((key) => {
    const el = items.get(key);
    if (el) container.appendChild(el);
  });
}

// 模块显隐与顺序是同一个 JSON 字段，统一从列表读状态：列表包含全部模块，
// 懒加载时没渲染出来的模块也不会在保存时丢失。
function readModuleState(panel, list) {
  const modules = moduleListItems(list).map((item) => ({
    key: item.dataset.moduleKey,
    enabled: Boolean(item.querySelector("[data-module-enabled]")?.checked),
  }));
  panel.querySelectorAll("[data-sidebar-hidden-module]").forEach((input) => {
    modules.push({
      key: input.dataset.moduleKey,
      enabled: input.value === "1",
    });
  });
  return modules;
}

async function saveSidebarPrefs(url, scope, fields) {
  const names = SIDEBAR_PREF_FIELDS[scope] || SIDEBAR_PREF_FIELDS.bookmarks;
  const body = new FormData();
  body.append("csrfmiddlewaretoken", getCSRFToken());
  body.append("form_id", "profile_quick");
  body.append(
    "form_fields",
    Object.keys(fields)
      .map((key) => names[key])
      .join(","),
  );
  Object.entries(fields).forEach(([key, value]) => {
    // 复选框未勾选不能提交空值，缺失即由服务端置为 False
    if (value === false || value === null || value === undefined) return;
    body.append(names[key], value);
  });

  const response = await fetch(url, {
    method: "POST",
    body,
    headers: {
      "X-CSRFToken": getCSRFToken(),
      "X-Requested-With": "XMLHttpRequest",
    },
  });
  if (!response.ok) {
    throw new Error(`sidebar settings save failed: ${response.status}`);
  }
}

function reportSaveFailure(error) {
  console.error(error);
  showToast(gettext("Couldn't save settings. Please try again."), {
    tone: "error",
  });
}

async function persistModuleState(panel, list, url, scope) {
  try {
    await saveSidebarPrefs(url, scope, {
      modules: JSON.stringify(readModuleState(panel, list)),
    });
  } catch (error) {
    reportSaveFailure(error);
  }
}

async function fetchModuleHtml(key) {
  const params = new URLSearchParams();
  params.set("ctx", document.querySelector("[data-ctx]")?.dataset.ctx || "active");
  params.set("modules", key);
  try {
    const response = await fetch(`/sidebar-content?${params.toString()}`);
    if (!response.ok) return "";
    return await response.text();
  } catch (error) {
    console.error(error);
    return "";
  }
}

// 懒加载时未启用的模块没有 DOM，重新打开需要单独取一次，并插回列表指定的位置
async function showSidebarModule(sidebar, list, key) {
  const existing = findModuleWrapper(sidebar, key);
  if (existing) {
    existing.hidden = false;
    return;
  }

  const html = (await fetchModuleHtml(key)).trim();
  if (!html) return;

  const template = document.createElement("template");
  template.innerHTML = html;
  const wrapper = template.content.firstElementChild;
  if (!wrapper) return;

  const keys = moduleListItems(list).map((item) => item.dataset.moduleKey);
  const nextKey = keys
    .slice(keys.indexOf(key) + 1)
    .find((candidate) => findModuleWrapper(sidebar, candidate));
  sidebar.insertBefore(
    wrapper,
    nextKey ? findModuleWrapper(sidebar, nextKey) : null,
  );
}

function hideSidebarModule(sidebar, key) {
  const wrapper = findModuleWrapper(sidebar, key);
  if (wrapper) wrapper.hidden = true;
}

function initSidebarSortables(sidebar, panel, list, url, scope) {
  const instances = [];

  // 拖模块本体：整条标题行都是抓手，排除其中的按钮与菜单，
  // 图标不再占位，标题才能和下方内容左对齐
  if (sidebarModuleWrappers(sidebar).length > 1) {
    instances.push(
      new Sortable(sidebar, {
        animation: 150,
        draggable: "[data-sidebar-module]",
        handle: ".section-header",
        filter: "button, a, input, select, label, ld-dropdown",
        delay: 200,
        delayOnTouchOnly: true,
        ghostClass: "sidebar-module-ghost",
        fallbackOnBody: true,
        onStart: () => sidebar.classList.add("is-sorting-modules"),
        onEnd: () => {
          sidebar.classList.remove("is-sorting-modules");
          reorderChildren(
            list,
            sidebarModuleKeys(sidebar),
            "[data-module-key]",
            (el) => el.dataset.moduleKey,
          );
          persistModuleState(panel, list, url, scope);
        },
      }),
    );
  }

  // 拖设置里的列表行：反推侧边栏顺序，两个入口共用同一份持久化状态
  if (moduleListItems(list).length > 1) {
    instances.push(
      new Sortable(list, {
        animation: 150,
        draggable: "[data-module-key]",
        handle: ".sidebar-module-handle",
        ghostClass: "sidebar-module-ghost",
        onEnd: () => {
          const keys = moduleListItems(list).map(
            (item) => item.dataset.moduleKey,
          );
          reorderChildren(
            sidebar,
            keys,
            "[data-sidebar-module]",
            (el) => el.dataset.sidebarModule,
          );
          persistModuleState(panel, list, url, scope);
        },
      }),
    );
  }

  sidebar.__sidebarSortables = instances;
}

function setupSidebarModules() {
  const sidebar = document.querySelector(".sidebar");
  if (!sidebar || sidebar.dataset.sidebarModulesReady === "1") return;

  const panel = sidebar.querySelector("[data-sidebar-settings]");
  const list = panel?.querySelector("[data-sidebar-module-list]");
  const url = panel?.dataset.sidebarSaveUrl;
  // 懒加载时模块尚未取回，等加载完成后再初始化
  if (!panel || !list || !url) return;

  sidebar.dataset.sidebarModulesReady = "1";
  const scope = panel.dataset.sidebarScope || getSidebarScope();

  // Turbo 会缓存 DOM 并复用：用 controller 统一收回监听，缓存恢复后重新绑一份
  sidebar.__sidebarListeners?.abort();
  const controller = new AbortController();
  sidebar.__sidebarListeners = controller;

  panel.querySelectorAll("[data-sidebar-option]").forEach((input) =>
    input.addEventListener("change", onSidebarOptionChange, {
      signal: controller.signal,
    }),
  );

  list.addEventListener(
    "change",
    (event) => {
      const input = event.target.closest("[data-module-enabled]");
      if (!input || input.disabled) return;
      const item = input.closest("[data-module-key]");
      if (!item) return;
      const key = item.dataset.moduleKey;
      if (input.checked) {
        showSidebarModule(sidebar, list, key);
      } else {
        hideSidebarModule(sidebar, key);
      }
      persistModuleState(panel, list, url, scope);
    },
    { signal: controller.signal },
  );

  initSidebarSortables(sidebar, panel, list, url, scope);
}

async function onSidebarOptionChange(event) {
  const input = event.target;
  const panel = input.closest("[data-sidebar-settings]");
  const sidebar = panel?.closest(".sidebar");
  const url = panel?.dataset.sidebarSaveUrl;
  if (!panel || !sidebar || !url) return;

  const scope = panel.dataset.sidebarScope || getSidebarScope();
  const enabled = Boolean(input.checked);

  if (input.dataset.sidebarOption === "show") {
    // 「常驻」只改默认值：同步 cookie 与清掉本地开合记忆，当前页面保持原样
    const page = document.querySelector(".bookmarks-page, .highlights-page");
    if (page) page.dataset.sidebarDefault = enabled ? "1" : "0";
    syncSidebarStateCache(scope, enabled);
    try {
      await saveSidebarPrefs(url, scope, { show: enabled });
    } catch (error) {
      reportSaveFailure(error);
    }
  } else if (input.dataset.sidebarOption === "sticky") {
    sidebar.dataset.stickyOn = enabled ? "true" : "false";
    try {
      await saveSidebarPrefs(url, scope, { sticky: enabled });
    } catch (error) {
      reportSaveFailure(error);
    }
  }
}

function teardownSidebarModules() {
  document.querySelectorAll(".sidebar[data-sidebar-modules-ready]").forEach(
    (sidebar) => {
      (sidebar.__sidebarSortables || []).forEach((instance) =>
        instance.destroy(),
      );
      sidebar.__sidebarSortables = [];
      sidebar.__sidebarListeners?.abort();
      sidebar.__sidebarListeners = null;
      delete sidebar.dataset.sidebarModulesReady;
    },
  );
}

// ==========================================
// Sidebar Toggle
// ==========================================

function getSidebarPage() {
  return document.querySelector(".bookmarks-page, .highlights-page");
}

function isMobile() {
  return window.innerWidth <= 840;
}

// localStorage key 按页面区分：书签页和高亮页各自独立
function getSidebarStateKey() {
  const page = getSidebarPage();
  if (page && page.classList.contains("highlights-page"))
    return "ld:sidebar-state:highlights";
  return "ld:sidebar-state:bookmarks";
}

let sidebarJustToggled = false;

function saveSidebarState(isOpen) {
  try {
    localStorage.setItem(getSidebarStateKey(), isOpen ? "1" : "0");
  } catch {}
  // 同步到 Cookie，让服务端判断是否需要懒加载
  try {
    const page = getSidebarPage();
    const isHighlights = page && page.classList.contains("highlights-page");
    const cookieName = isHighlights
      ? "ld_sidebar_highlights"
      : "ld_sidebar_bookmarks";
    document.cookie = `${cookieName}=${isOpen ? "1" : "0"}; path=/; max-age=31536000; SameSite=Lax`;
  } catch {}
}

let sidebarLazyLoaded = false;

async function loadSidebarContent(page) {
  const sidebar = page.querySelector(".sidebar");
  if (!sidebar || sidebarLazyLoaded) return;

  const placeholder = sidebar.querySelector("[data-sidebar-lazy-placeholder]");
  if (!placeholder) return;

  sidebarLazyLoaded = true;

  const ctxEl = document.querySelector("[data-ctx]");
  const ctx = ctxEl?.dataset.ctx || "active";

  const modules =
    placeholder.dataset.sidebarLazyModules || "domains,tags,bundles,summary";
  const fingerprint = placeholder.dataset.domainConfigFingerprint || "";
  const cacheKey =
    `sidebar-content:${ctx}:${modules}` +
    (fingerprint ? `:${fingerprint}` : "");

  let html = null;
  try {
    html = sessionStorage.getItem(cacheKey);
  } catch {}

  if (html === null) {
    const sp = new URLSearchParams();
    sp.set("ctx", ctx);
    sp.set("modules", modules);
    try {
      const resp = await fetch(`/sidebar-content?${sp.toString()}`);
      if (!resp.ok) return;
      html = await resp.text();
      try {
        sessionStorage.setItem(cacheKey, html);
      } catch {}
    } catch (e) {
      console.error("Failed to load sidebar content:", e);
      return;
    }
  }

  if (html && html.trim()) {
    placeholder.outerHTML = html;
    document.querySelectorAll("[ld-domain-tree]").forEach((el) => {
      el.behaviors?.forEach(
        (b) => b instanceof DomainTreeBehavior && b.restoreTreeState(),
      );
    });
    setupSidebarModules();
  }
}

window.loadSidebarContent = loadSidebarContent;

function openSidebar(page) {
  page.classList.remove("sidebar-closed");
  page.classList.add("sidebar-open");
  loadSidebarContent(page);
  restoreSidebar();

  // 与左侧布局抽屉互斥：放不下就只留一个
  if (!isMobile()) {
    const drawer = document.querySelector(".layout-drawer.is-open");
    if (drawer && !panelsFitTogether()) {
      drawer.querySelector("[data-layout-drawer-close]")?.click();
    }
  }

  // 记住这次开合，刷新后照旧
  setSidebarState(getSidebarScope(), true);
  // 宽度够就让位（内容平移不重排），不够就浮层覆盖
  syncPanelSpace();
  if (isMobile()) {
    document.body.classList.add("sidebar-overlay-active");
    page.classList.add("sidebar-animate");
    requestAnimationFrame(() => page.classList.add("sidebar-visible"));
    sidebarJustToggled = true;
    setTimeout(() => {
      sidebarJustToggled = false;
      page.classList.remove("sidebar-animate");
    }, 400);
  }
}

function closeSidebar(page) {
  if (page.classList.contains("sidebar-open") && isMobile()) {
    page.classList.add("sidebar-closing");
    void page.offsetWidth;
    page.classList.remove("sidebar-visible");

    const sidebar = page.querySelector(".sidebar");
    if (sidebar) {
      let done = false;
      const finalize = () => {
        if (done) return;
        done = true;
        page.classList.remove("sidebar-open", "sidebar-closing");
        page.classList.add("sidebar-closed");
        document.body.classList.remove("sidebar-overlay-active");
        setSidebarState(getSidebarScope(), false);
        syncPanelSpace();
      };
      sidebar.addEventListener("transitionend", finalize, { once: true });
      setTimeout(finalize, 300);
      return;
    }
  }

  page.classList.remove("sidebar-open", "sidebar-visible");
  page.classList.add("sidebar-closed");
  document.body.classList.remove("sidebar-overlay-active");
  setSidebarState(getSidebarScope(), false);
  syncPanelSpace();
}

// 交互 handler
function handleSidebarInteraction(e) {
  const page = getSidebarPage();
  if (!page) return;

  // Toggle 按钮
  if (e.target.closest("[data-sidebar-toggle]")) {
    if (e.type === "touchstart") {
      e.preventDefault();
    }
    const wasOpen = page.classList.contains("sidebar-open");
    wasOpen ? closeSidebar(page) : openSidebar(page);
    saveSidebarState(!wasOpen);
    return;
  }

  // 关闭按钮（移动端）
  if (e.target.closest("[data-sidebar-close]")) {
    if (e.type === "touchstart") {
      e.preventDefault();
    }
    closeSidebar(page);
    saveSidebarState(false);
    return;
  }

  // 遮罩点击（移动端 sidebar 外部区域）
  if (
    e.type === "click" &&
    !sidebarJustToggled &&
    page.classList.contains("sidebar-visible") &&
    !e.target.closest(".sidebar") &&
    isMobile()
  ) {
    closeSidebar(page);
    saveSidebarState(false);
  }
}
document.addEventListener("click", handleSidebarInteraction);
document.addEventListener("touchstart", handleSidebarInteraction, {
  passive: false,
});

// Turbo 生命周期
document.addEventListener("turbo:before-cache", () => {
  saveSidebar();
  unbindSidebarScrollListener();
  disconnectSidebarContentObserver();
  teardownSidebarModules();
});
document.addEventListener("turbo:load", () => {
  bindSidebarScrollListener();
  setupSidebarContentObserver();
  restoreSidebar();
  setupSidebarModules();
});
document.addEventListener("DOMContentLoaded", () => {
  bindSidebarScrollListener();
  setupSidebarContentObserver();
  restoreSidebar();
  setupSidebarModules();
});
