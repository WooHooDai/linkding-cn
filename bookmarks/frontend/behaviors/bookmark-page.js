import { Behavior, registerBehavior, applyBehaviors } from "./index";

class BookmarkItem extends Behavior {
  constructor(element) {
    super(element);

    // Toggle notes
    this.onToggleNotes = this.onToggleNotes.bind(this);
    this.notesToggle = element.querySelector(".toggle-notes");
    if (this.notesToggle) {
      this.notesToggle.addEventListener("click", this.onToggleNotes);
    }

    // 记录滚动位置（点击编辑按钮时）
    this.editAction = element.querySelector(".edit-action");
    if (this.editAction) {
      this.onEditClick = this.onEditClick.bind(this);
      this.editAction.addEventListener("click", this.onEditClick);
    }

    // Add tooltip to title if it is truncated
    const titleAnchor = element.querySelector(".title > a");
    const titleSpan = titleAnchor.querySelector("span");
    requestAnimationFrame(() => {
      if (titleSpan.offsetWidth > titleAnchor.offsetWidth) {
        titleAnchor.dataset.tooltip = titleSpan.textContent;
      }
    });
  }

  destroy() {
    if (this.notesToggle) {
      this.notesToggle.removeEventListener("click", this.onToggleNotes);
    }
    if (this.editAction) {
      this.editAction.removeEventListener("click", this.onEditClick);
    }
  }

  onToggleNotes(event) {
    event.preventDefault();
    event.stopPropagation();
    this.element.classList.toggle("show-notes");
  }

  onEditClick() {
    localStorage.setItem('bookmarkListScrollPosition', window.scrollY);
    localStorage.setItem('bookmarkListReturnUrl', window.location.pathname);
  }
}

registerBehavior("ld-bookmark-item", BookmarkItem);

// Toggle: BundleMenuItem
class BundleMenuFolderToggle extends Behavior {
  constructor(element) {
    super(element);
    this.onClick = this.onClick.bind(this);
    element.addEventListener("click", this.onClick);
    this.restoreState();
  }

  destroy() {
    this.element.removeEventListener("click", this.onClick);
  }

  onClick(e) {
    const btn = e.target.closest('.folder-toggle');
    if (!btn) return;
    const folderItem = btn.closest('li');
    const bundleId = folderItem.dataset.bundleId;
    let next = folderItem.nextElementSibling;
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', !expanded);
    this.setBundleState(bundleId, !expanded);
    while (next && next.dataset.folder !== 'true') {
      next.style.display = expanded ? 'none' : '';
      next = next.nextElementSibling;
    }
  }

  setBundleState(bundleId, expanded) {
    if (!bundleId) return;
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem('bundleFolderState') || '{}');
    } catch {}
    state[bundleId] = expanded;
    localStorage.setItem('bundleFolderState', JSON.stringify(state));
  }

  restoreState() {
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem('bundleFolderState') || '{}');
    } catch {}
    const bundleMenu = this.element;
    bundleMenu.querySelectorAll('.folder-toggle').forEach(btn => {
      const folderItem = btn.closest('li');
      const bundleId = folderItem.dataset.bundleId;
      if (!bundleId) return;
      const expanded = state[bundleId] !== false; // 默认展开
      btn.setAttribute('aria-expanded', expanded);
      let next = folderItem.nextElementSibling;
      while (next && next.dataset.folder !== 'true') {
        next.style.display = expanded ? '' : 'none';
        next = next.nextElementSibling;
      }
    });
  }
}

registerBehavior('ld-bundle-menu', BundleMenuFolderToggle);

function bindBundleMenuBehaviors() {
  document.querySelectorAll("[ld-bundle-menu]").forEach((el) => {
    applyBehaviors(el, ["ld-bundle-menu"]);
  });
}

document.addEventListener("DOMContentLoaded", bindBundleMenuBehaviors);
document.addEventListener("turbo:load", bindBundleMenuBehaviors);

// 页面加载时恢复滚动位置
// TODO：可以更细化记录与恢复位置的页面&时机
function restoreBookmarkListScrollPosition() {
  // 只在有书签列表主容器的页面恢复滚动
  if (document.querySelector('.bookmark-list')) {
    var scroll = localStorage.getItem('bookmarkListScrollPosition');
    var returnUrl = localStorage.getItem('bookmarkListReturnUrl');
    if (
      scroll !== null &&
      returnUrl !== null
    ) {
      if (window.location.pathname === returnUrl) {
        window.scrollTo(0, parseInt(scroll, 10));
      }
      localStorage.removeItem('bookmarkListScrollPosition');
      localStorage.removeItem('bookmarkListReturnUrl');
    }
  }
}
document.addEventListener('DOMContentLoaded', restoreBookmarkListScrollPosition);
document.addEventListener('turbo:load', restoreBookmarkListScrollPosition);
