import { Behavior, registerBehavior } from "./runtime.js";

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
  }
}

window.loadSidebarContent = loadSidebarContent;

function openSidebar(page) {
  page.classList.remove("sidebar-closed");
  page.classList.add("sidebar-open");
  loadSidebarContent(page);
  restoreSidebar();
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
      };
      sidebar.addEventListener("transitionend", finalize, { once: true });
      setTimeout(finalize, 300);
      return;
    }
  }

  page.classList.remove("sidebar-open", "sidebar-visible");
  page.classList.add("sidebar-closed");
  document.body.classList.remove("sidebar-overlay-active");
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
});
document.addEventListener("turbo:load", () => {
  bindSidebarScrollListener();
  setupSidebarContentObserver();
  restoreSidebar();
});
document.addEventListener("DOMContentLoaded", () => {
  bindSidebarScrollListener();
  setupSidebarContentObserver();
  restoreSidebar();
});
