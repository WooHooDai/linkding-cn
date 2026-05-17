import { Behavior, registerBehavior } from "./runtime.js";

// ==========================================
// 书签列表
// ==========================================

class BookmarkItem extends Behavior {
  constructor(element) {
    super(element);

    // 绑定基础事件
    this.onToggleNotes = this.onToggleNotes.bind(this);
    this.onEditClick = this.onEditClick.bind(this);
    this.onTitleClick = this.onTitleClick.bind(this);
    
    this.scroller = document.scrollingElement;
    
    // 初始化 Notes
    this.notesToggle = element.querySelector(".toggle-notes");
    if (this.notesToggle) {
      this.notesToggle.addEventListener("click", this.onToggleNotes);
    }

    // 初始化 Edit Action
    this.editAction = element.querySelector(".edit-action");
    if (this.editAction) {
      this.editAction.addEventListener("click", this.onEditClick);
    }

    // 初始化标题浮窗
    this.initTitleTooltip();
    
    // 初始化描述浮窗
    this.initDescriptionTooltip();
  }

  destroy() {
    if (this.notesToggle) this.notesToggle.removeEventListener("click", this.onToggleNotes);
    if (this.editAction) this.editAction.removeEventListener("click", this.onEditClick);

    if (this.titleElement) {
      this.titleElement.removeEventListener('mouseenter', this.showTitleTooltip);
      this.titleElement.removeEventListener('mouseleave', this.hideTitleTooltip);
      this.titleElement.removeEventListener('focus', this.showTitleTooltip);
      this.titleElement.removeEventListener('blur', this.hideTitleTooltip);
      this.titleElement.removeEventListener("click", this.onTitleClick);
    }

    if (this.descriptionContainer) {
      this.descriptionContainer.removeEventListener('mouseenter', this.showDescriptionTooltip);
      this.descriptionContainer.removeEventListener('mouseleave', this.hideDescriptionTooltip);
      this.descriptionContainer.removeEventListener('focus', this.showDescriptionTooltip);
      this.descriptionContainer.removeEventListener('blur', this.hideDescriptionTooltip);
      this.descriptionContainer.removeEventListener('click', this.showDescriptionTooltip);
    }
  }

  onToggleNotes(event) {
    event.preventDefault();
    event.stopPropagation();
    this.element.classList.toggle("show-notes");
  }

  onEditClick() {
    if(this.scroller) {
      localStorage.setItem('bookmarkListScrollPosition', this.scroller.scrollTop);
      localStorage.setItem('bookmarkListReturnUrl', window.location.pathname);
    }
  }

  onTitleClick(event) {
    if (event.target.closest('a.favicon-link') || event.target.closest('label.bulk-edit-checkbox')) return;

    const link = this.titleElement.querySelector('a.title-link');
    if (!link || !link.href) return;

    const target = link.getAttribute('target');
    if (target === '_blank') {
      window.open(link.href, target, 'noopener noreferrer');
    } else {
      window.open(link.href, target);
    }
  }

  showFloatTooltip(targetEl) {
    if (!targetEl || !targetEl.dataset.tooltip) return;

    let tooltip = targetEl.querySelector('.float-tooltip');
    if (tooltip) {
      tooltip.style.display = tooltip.style.display === 'none' ? 'block' : 'none';
      return;
    }

    tooltip = document.createElement('div');
    tooltip.className = 'float-tooltip';
    tooltip.textContent = targetEl.dataset.tooltip;
    targetEl.appendChild(tooltip);
  }

  hideFloatTooltip(targetEl) {
    const tooltip = targetEl.querySelector('.float-tooltip');
    if (tooltip) tooltip.style.display = 'none';
  }

  initTitleTooltip() {
    this.titleElement = this.element.querySelector(".title");
    if (!this.titleElement) return;

    const titleSpan = this.titleElement.querySelector("span");
    if (titleSpan) {
      requestAnimationFrame(() => {
        const availableWidth = this.titleElement.offsetWidth - 24; // 16px favicon + 8px gap
        if (titleSpan.offsetWidth > availableWidth) {
          this.titleElement.dataset.tooltip = titleSpan.textContent;
        }
      });
    }

    this.showTitleTooltip = () => this.showFloatTooltip(this.titleElement);
    this.hideTitleTooltip = () => this.hideFloatTooltip(this.titleElement);

    const isTouch = window.matchMedia('(pointer: coarse)').matches;
    if (!isTouch) {
      this.titleElement.addEventListener('mouseenter', this.showTitleTooltip, { passive: true });
      this.titleElement.addEventListener('mouseleave', this.hideTitleTooltip, { passive: true });
    }
    this.titleElement.addEventListener('focus', this.showTitleTooltip, { passive: true });
    this.titleElement.addEventListener('blur', this.hideTitleTooltip, { passive: true });

    // Safari 特殊处理
    const isSafari = navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome');
    if (isSafari) {
      const titleLinkElement = this.element.querySelector("a.title-link");
      if (titleLinkElement) titleLinkElement.style.pointerEvents = 'none';
      this.titleElement.style.cursor = "pointer";
      this.titleElement.addEventListener("click", this.onTitleClick);
    }
  }

  initDescriptionTooltip() {
    this.descriptionContainer = this.element.querySelector(".description-container");
    if (!this.descriptionContainer) return;

    const descriptionElement = this.element.querySelector(".description");
    const descriptionText = this.descriptionContainer.querySelector(".description-text");
    const isDescriptionInline = descriptionElement?.classList.contains("inline");

    if (descriptionText) {
      requestAnimationFrame(() => {
        if (isDescriptionInline) {
          const tagsElement = this.descriptionContainer.querySelector('.tags');
          let availableWidth = this.descriptionContainer.offsetWidth - 7;
          if (tagsElement) availableWidth -= tagsElement.offsetWidth;
          
          if (window.matchMedia('(pointer: coarse)').matches && availableWidth <= 0) return;
          if (descriptionText.offsetWidth > availableWidth) {
            this.descriptionContainer.dataset.tooltip = descriptionText.textContent;
          }
        } else if (this.descriptionContainer.scrollHeight > this.descriptionContainer.clientHeight) {
          this.descriptionContainer.dataset.tooltip = descriptionText.textContent;
        }
      });
    }

    this.showDescriptionTooltip = () => this.showFloatTooltip(this.descriptionContainer);
    this.hideDescriptionTooltip = () => this.hideFloatTooltip(this.descriptionContainer);
    
    this.descriptionContainer.addEventListener('focus', this.showDescriptionTooltip, { passive: true });
    this.descriptionContainer.addEventListener('blur', this.hideDescriptionTooltip, { passive: true });
    
    const isTouch = window.matchMedia('(pointer: coarse)').matches;
    if (isTouch) {
      this.descriptionContainer.addEventListener('click', this.showDescriptionTooltip, { passive: true });
    } else {
      this.descriptionContainer.addEventListener('mouseenter', this.showDescriptionTooltip, { passive: true });
      this.descriptionContainer.addEventListener('mouseleave', this.hideDescriptionTooltip, { passive: true });
    }
  }
}
registerBehavior("ld-bookmark-item", BookmarkItem);


// ==========================================
// 展开折叠按钮
// ==========================================

class CollapseButtonBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.storageKey = element.dataset.toggleStorageKey;
    this.targetSelector = element.dataset.toggleTargetSelector || '.section-content';
    this.toggleBtn = element.querySelector('button');
    this.content = element.querySelector(this.targetSelector);
    
    this.onClick = this.onClick.bind(this);
    
    if (this.toggleBtn) {
      this.toggleBtn.addEventListener('click', this.onClick);
      this.restoreState();
    }
  }

  destroy() {
    if (this.toggleBtn) this.toggleBtn.removeEventListener('click', this.onClick);
  }

  onClick() {
    if (!this.toggleBtn || !this.content) return;
    
    const expanded = this.toggleBtn.getAttribute('aria-expanded') === 'true';
    const newState = !expanded; 
    this.toggleBtn.setAttribute('aria-expanded', newState);
    this.content.style.display = newState ? '' : 'none';
    
    // 记忆状态
    if (this.storageKey) {
      localStorage.setItem(this.storageKey, newState ? 'true' : 'false');
    }
  }

  restoreState() {
    if (!this.toggleBtn || !this.content) return;
    const expanded = this.storageKey ? localStorage.getItem(this.storageKey) !== 'false' : true;
    this.toggleBtn.setAttribute('aria-expanded', expanded);
    this.content.style.display = expanded ? '' : 'none';
  }
}
registerBehavior('ld-collapse-button', CollapseButtonBehavior);


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
    const btn = e.target.closest('.folder-toggle');
    if (!btn) return;
    
    const folderItem = btn.closest('li');
    const bundleId = folderItem.dataset.bundleId;
    
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    const newState = !expanded;
    
    btn.setAttribute('aria-expanded', newState);
    this.setBundleState(bundleId, newState);
    
    let next = folderItem.nextElementSibling;
    while (next && next.dataset.folder !== 'true') {
      next.style.display = newState ? '' : 'none';
      next = next.nextElementSibling;
    }
  }

  setBundleState(bundleId, expanded) {
    if (!bundleId) return;
    let state = {};
    try { state = JSON.parse(localStorage.getItem('bundleFolderState') || '{}'); } catch {}
    state[bundleId] = expanded;
    localStorage.setItem('bundleFolderState', JSON.stringify(state));
  }

  restoreBundleState() {
    let state = {};
    try { state = JSON.parse(localStorage.getItem('bundleFolderState') || '{}'); } catch {}
    
    this.element.querySelectorAll('.folder-toggle').forEach(btn => {
      const folderItem = btn.closest('li');
      const bundleId = folderItem.dataset.bundleId;
      if (!bundleId) return;
      
      const expanded = state[bundleId] !== false;
      btn.setAttribute('aria-expanded', expanded);
      
      let next = folderItem.nextElementSibling;
      while (next && next.dataset.folder !== 'true') {
        next.style.display = expanded ? '' : 'none';
        next = next.nextElementSibling;
      }
    });
  }
}
registerBehavior('ld-bundle-menu', BundleCollapseButton);


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
      if (item?.dataset.domainGroup === "true" && item?.dataset.domainHasChildren === "true") {
        this.toggleTreeItem(item, event);
      }
    }
  }

  toggleTreeItem(item, event) {
    if (!item) return;
    const childList = item.querySelector(":scope > ul.domain-children");
    const button = item.querySelector(":scope > .domain-row .folder-toggle");
    
    if (!childList || !button) return;
    if (event) event.preventDefault();

    const expanded = button.getAttribute("aria-expanded") === "true";
    const newState = !expanded;
    
    button.setAttribute("aria-expanded", newState);
    childList.style.display = newState ? "" : "none";
    this.setNodeState(item.dataset.domainNodeId, newState);
  }

  setNodeState(nodeId, expanded) {
    if (!nodeId) return;
    let state = {};
    try { state = JSON.parse(localStorage.getItem("domainTreeState") || "{}"); } catch {}
    state[nodeId] = expanded;
    localStorage.setItem("domainTreeState", JSON.stringify(state));
  }

  restoreTreeState() {
    let state = {};
    try { state = JSON.parse(localStorage.getItem("domainTreeState") || "{}"); } catch {}

    this.element.querySelectorAll('.domain-menu-item[data-domain-has-children="true"]').forEach((item) => {
      const button = item.querySelector(":scope > .domain-row .folder-toggle");
      const childList = item.querySelector(":scope > ul.domain-children");
      if (!button || !childList) return;

      const nodeId = item.dataset.domainNodeId;
      const hasSelectedDescendant = childList.querySelector(".domain-menu-item.selected");
      const expanded = hasSelectedDescendant ? true : state[nodeId] !== false;

      button.setAttribute("aria-expanded", expanded);
      childList.style.display = expanded ? "" : "none";
    });
  }
}
registerBehavior("ld-domain-tree", DomainTreeBehavior);

// ==========================================
// 滚动位置记忆
// ==========================================

function restoreBookmarkListScrollPosition() {
  const scroller = document.scrollingElement;
  if (scroller && document.querySelector('.bookmark-list')) {
    const scroll = localStorage.getItem('bookmarkListScrollPosition');
    const returnUrl = localStorage.getItem('bookmarkListReturnUrl');

    if (scroll !== null && returnUrl !== null) {
      if (window.location.pathname === returnUrl) {
        scroller.scrollTo(0, parseInt(scroll, 10));
      }
      localStorage.removeItem('bookmarkListScrollPosition');
      localStorage.removeItem('bookmarkListReturnUrl');
    }
  }
}

document.addEventListener('DOMContentLoaded', restoreBookmarkListScrollPosition);
document.addEventListener('turbo:load', restoreBookmarkListScrollPosition);

// ==========================================
// 侧边栏滚动位置记忆
// ==========================================

// 显示侧边栏开启（sidebar）、关闭（drawer），滚动位置记忆各自独立

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

  localStorage.setItem(key, JSON.stringify({ s: scrollTop, h: scrollHeight, slots }));
}

function applyScrollPosition(key, selector) {
  const el = document.querySelector(selector);
  if (!el) return;

  const data = readScrollData(key);
  if (!data) return;

  // 精确匹配当前 scrollHeight 的记忆位置
  // 无匹配则用最近一次
  const target = data.slots?.[el.scrollHeight] ?? data.s;

  requestAnimationFrame(() => {
    el.scrollTop = Math.min(target, el.scrollHeight - el.clientHeight);
  });
}

function createScrollHandler(saveFn, delay) {
  let timer;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(saveFn, delay);
  };
}

// --- 显示侧边栏开启（sidebar） ---

const SIDEBAR_KEY = 'sidebarScrollPosition';
const SIDEBAR_SEL = '.sidebar';

const saveSidebar = () => saveScrollPosition(SIDEBAR_KEY, SIDEBAR_SEL);
const restoreSidebar = () => applyScrollPosition(SIDEBAR_KEY, SIDEBAR_SEL);
const onSidebarScroll = createScrollHandler(saveSidebar, 300);

function bindSidebarScrollListener() {
  const el = document.querySelector(SIDEBAR_SEL);
  if (el) el.addEventListener('scroll', onSidebarScroll, { passive: true });
}

function unbindSidebarScrollListener() {
  const el = document.querySelector(SIDEBAR_SEL);
  if (el) el.removeEventListener('scroll', onSidebarScroll);
}

// --- 显示侧边栏关闭（drawer） ---

const DRAWER_KEY = 'drawerScrollPosition';
const DRAWER_SEL = 'ld-filter-drawer .modal-body';

const saveDrawer = () => saveScrollPosition(DRAWER_KEY, DRAWER_SEL);
const restoreDrawer = () => applyScrollPosition(DRAWER_KEY, DRAWER_SEL);
const onDrawerScroll = createScrollHandler(saveDrawer, 150);

function setupDrawerObserver() {
  const modals = document.querySelector('.modals');
  if (!modals) return;

  new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.tagName === 'LD-FILTER-DRAWER') {
          requestAnimationFrame(() => {
            const body = node.querySelector('.modal-body');
            if (body) {
              body.addEventListener('scroll', onDrawerScroll, { passive: true });
              restoreDrawer();
            }
          });
        }
      }
    }
  }).observe(modals, { childList: true });
}

// 抽屉关闭前保存（捕获阶段，先于 Modal 自身的 close handler）
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-close-modal]') && e.target.closest('ld-filter-drawer')) {
    saveDrawer();
  }
}, true);

// --- 生命周期 ---

document.addEventListener('turbo:before-cache', () => {
  saveSidebar();
  unbindSidebarScrollListener();
  saveDrawer();
});
document.addEventListener('turbo:load', restoreSidebar);
document.addEventListener('turbo:load', bindSidebarScrollListener);
document.addEventListener('turbo:load', setupDrawerObserver);
document.addEventListener('DOMContentLoaded', restoreSidebar);
document.addEventListener('DOMContentLoaded', bindSidebarScrollListener);
document.addEventListener('DOMContentLoaded', setupDrawerObserver);