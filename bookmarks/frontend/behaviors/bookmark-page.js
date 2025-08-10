import { Behavior, registerBehavior, applyBehaviors } from "./index";

class BookmarkPagination extends Behavior {
  constructor(element) {
    super(element);

    const isSticky = element.classList.contains('sticky');
    const isEdge = (navigator.userAgent.indexOf("Edg/") !== -1);
    if (isEdge && isSticky) {
      element.classList.add('edge');
      this.setWidth();
      this.onResize = this.onResize.bind(this);
      this.onScroll = this.onScroll.bind(this);
      window.addEventListener('resize', this.onResize);
      window.addEventListener('scroll', this.onScroll, {  });
    }
  }

  destroy() {
    window.removeEventListener('resize', this.onResize);
    window.removeEventListener('scroll', this.onScroll);
  }

  onScroll() {
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    
    // 当滚动到距离底部100px以内时移除edge类
    if (scrollTop + windowHeight >= documentHeight - 100) {
      this.element.classList.remove('edge');
    } else {
      if (!this.element.classList.contains('edge')) {
        this.element.classList.add('edge');
      }
    }
  }

  onResize() {
    this.setWidth();
  }

  setWidth() {
    const bookmarkList = document.querySelector('.bookmark-list');
    if (!bookmarkList) return;
    const width = bookmarkList.offsetWidth;
    this.element.style.width = `${width}px`;
  }
}

registerBehavior("ld-pagination", BookmarkPagination);

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
    const titleAnchor = element.querySelector(".title .title-link");
    if (titleAnchor) {
      const titleSpan = titleAnchor.querySelector("span");
      if (titleSpan) {
        requestAnimationFrame(() => {
          if (titleSpan.offsetWidth > titleAnchor.offsetWidth) {
            titleAnchor.dataset.tooltip = titleSpan.textContent;
          }
        });
      }
    }

    // 如果描述被截断，就添加一个tooltip数据
    const descriptionElement = element.querySelector(".description");
    const descriptionContainer = element.querySelector(".description-container");
    if (descriptionContainer) {
      const descriptionText = descriptionContainer.querySelector(".description-text");
      if (descriptionText) {
        const isInline = descriptionElement.classList.contains("inline");
        requestAnimationFrame(() => {
          // 行内描述
          if (isInline && descriptionText.offsetWidth > descriptionContainer.offsetWidth) {
            descriptionContainer.dataset.tooltip = descriptionText.textContent;
          // 分行描述（单行或多行）  
          } else if (!isInline && descriptionContainer.scrollHeight > descriptionContainer.clientHeight) {
            descriptionContainer.dataset.tooltip = descriptionText.textContent;
          }
        });

        // 分行（多行截断）时，使用 JS 精确定位 tooltip 到“最后可见行”
        if (!isInline) {
          // 记录元素
          this.descriptionElement = descriptionElement; 
          this.descriptionContainer = descriptionContainer;
          this.descriptionText = descriptionText;
          // 绑定事件
          this.onDescriptionEnter = (e) => this.showDescriptionTooltip(e);
          this.onDescriptionLeave = () => this.hideDescriptionTooltip();
          this.onDescriptionMove = () => this.repositionDescriptionTooltip();
          descriptionContainer.addEventListener('mouseenter', this.onDescriptionEnter);
          descriptionContainer.addEventListener('mouseleave', this.onDescriptionLeave);
          descriptionContainer.addEventListener('focus', this.onDescriptionEnter, true);
          descriptionContainer.addEventListener('blur', this.onDescriptionLeave, true);
          window.addEventListener('resize', this.onDescriptionMove, { passive: true });
          window.addEventListener('scroll', this.onDescriptionMove, { passive: true });
        }
      }
    }
  }

  destroy() {
    if (this.notesToggle) {
      this.notesToggle.removeEventListener("click", this.onToggleNotes);
    }
    if (this.editAction) {
      this.editAction.removeEventListener("click", this.onEditClick);
    }

    // 清理描述 tooltip 的事件与元素
    if (this.descriptionContainer) {
      this.descriptionContainer.removeEventListener('mouseenter', this.onDescriptionEnter);
      this.descriptionContainer.removeEventListener('mouseleave', this.onDescriptionLeave);
      this.descriptionContainer.removeEventListener('focus', this.onDescriptionEnter, true);
      this.descriptionContainer.removeEventListener('blur', this.onDescriptionLeave, true);
      window.removeEventListener('resize', this.onDescriptionMove);
      window.removeEventListener('scroll', this.onDescriptionMove);
    }
    this.hideDescriptionTooltip();
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

  // 计算分行截断描述的“最后可见行”矩形
  getLastVisibleLineRect() {
    if (!this.descriptionElement || !this.descriptionText) return null;
    const clipRect = this.descriptionElement.getBoundingClientRect();
    const rectList = Array.from(this.descriptionText.getClientRects());
    if (rectList.length === 0) return null;

    // 过滤与可见区域相交的行框
    const visibleRects = rectList.filter((r) => {
      const verticallyVisible = r.top < clipRect.bottom - 0.5 && r.bottom > clipRect.top + 0.5;
      const horizontallyVisible = r.left < clipRect.right - 0.5 && r.right > clipRect.left + 0.5;
      return verticallyVisible && horizontallyVisible;
    });
    if (visibleRects.length === 0) return null;

    // 取最靠下的一行
    let last = visibleRects[0];
    for (const r of visibleRects) {
      if (r.bottom > last.bottom || (r.bottom === last.bottom && r.left > last.left)) {
        last = r;
      }
    }
    return { lastRect: last, clipRect };
  }

  // 展示 tooltip：对齐到“最后可见行”的左下角
  showDescriptionTooltip() {
    if (!this.descriptionContainer || !this.descriptionContainer.dataset.tooltip) return;
    // 触屏设备不显示
    if (window.matchMedia('(pointer: coarse)').matches) return;
    // 未被截断不显示
    if (this.descriptionContainer.scrollHeight <= this.descriptionContainer.clientHeight) return;

    const calc = this.getLastVisibleLineRect();
    if (!calc) return;

    const { lastRect, clipRect } = calc;
    const containerRect = this.descriptionContainer.getBoundingClientRect();
    const tooltip = document.createElement('div');
    tooltip.className = 'ld-tooltip-floating';
    tooltip.textContent = this.descriptionContainer.dataset.tooltip || '';
    document.body.appendChild(tooltip);
    this.activeTooltip = tooltip;

    // 初次定位
    const viewportLeft = containerRect.left; // 左侧与 description-container 对齐
    const viewportTop = lastRect.bottom;     // 纵向对齐到最后可见行底部
    tooltip.style.left = `${viewportLeft}px`;
    tooltip.style.top = `${viewportTop}px`;
    tooltip.style.width = `${Math.floor(containerRect.width)}px`; // 宽度与 description-container 一致
  }

  // 重新计算位置（窗口尺寸变化等）
  repositionDescriptionTooltip() {
    if (!this.activeTooltip) return;
    const calc = this.getLastVisibleLineRect();
    if (!calc) return;
    const { lastRect, clipRect } = calc;
    const containerRect = this.descriptionContainer.getBoundingClientRect();
    const viewportLeft = containerRect.left;
    const viewportTop = lastRect.bottom;
    this.activeTooltip.style.left = `${viewportLeft}px`;
    this.activeTooltip.style.top = `${viewportTop}px`;
    this.activeTooltip.style.width = `${Math.floor(containerRect.width)}px`;
  }

  // 隐藏并移除 tooltip
  hideDescriptionTooltip() {
    if (this.activeTooltip && this.activeTooltip.parentElement) {
      this.activeTooltip.parentElement.removeChild(this.activeTooltip);
    }
    this.activeTooltip = null;
  }
}

registerBehavior("ld-bookmark-item", BookmarkItem);

// 折叠按钮：通用行为
class CollapseButtonBehavior extends Behavior {
  constructor(element) {
    super(element);
    // 支持通过 data-* 属性自定义
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
    if (this.toggleBtn) {
      this.toggleBtn.removeEventListener('click', this.onClick);
    }
  }

  onClick() {
    if (!this.toggleBtn || !this.content) return;
    const expanded = this.toggleBtn.getAttribute('aria-expanded') === 'true';
    this.toggleBtn.setAttribute('aria-expanded', !expanded);
    this.content.style.display = expanded ? 'none' : '';
    this.setState(!expanded);
  }

  setState(expanded) {
    if (this.storageKey) {
      localStorage.setItem(this.storageKey, expanded ? 'true' : 'false');
    }
  }

  restoreState() {
    if (!this.toggleBtn || !this.content) return;
    let expanded = true;
    if (this.storageKey) {
      expanded = localStorage.getItem(this.storageKey) !== 'false';
    }
    this.toggleBtn.setAttribute('aria-expanded', expanded);
    this.content.style.display = expanded ? '' : 'none';
  }
}

registerBehavior('ld-collapse-button', CollapseButtonBehavior);

// 折叠按钮：Bundle动态绑定
class BundleCollapseButton extends CollapseButtonBehavior {
  constructor(element) {
    super(element);
    this.onBundleClick = this.onBundleClick.bind(this);
    element.addEventListener("click", this.onBundleClick);
    this.restoreBundleState();
  }

  destroy() {
    super.destroy();
    this.element.removeEventListener("click", this.onBundleClick);
  }

  onBundleClick(e) {
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

  restoreBundleState() {
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

registerBehavior('ld-bundle-menu', BundleCollapseButton);

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

// 侧边栏sticky功能
class SidePanel extends Behavior {
  constructor(element) {
    super(element);
    
    this.isSticky = false;
    this.originalStyle = null;
    this.scrollThrottle = null;
    
    this.onScroll = this.onScroll.bind(this);
    this.onResize = this.onResize.bind(this);
    
    this.init();
  }

  init() {
    // 移动端禁用
    if (window.innerWidth <= 840) {
      return;
    }

    this.originalStyle = {
      'top': this.element.style.top,
      'left': this.element.style.left,
      'width': this.element.style.width,
      'position': this.element.style.position,
    }

    window.addEventListener('scroll', this.onScroll, { passive: true });
    window.addEventListener('resize', this.onResize, { passive: true });
    
    this.calculateStickyTrigger();
    this.updateStickyState();
  }

  calculateStickyTrigger() {
    // 临时移除可能存在的fixed定位，获取真实位置
    const originalPosition = this.element.style.position;
    this.element.style.position = '';
    
    // 获取侧边栏在正常文档流中的位置
    const rect = this.element.getBoundingClientRect();
    this.stickyTriggerY = rect.top + window.scrollY;
    
    // 恢复原始position
    this.element.style.position = originalPosition;
  }

  updateStickyState() {
    const scrollY = window.scrollY;
    
    // 当页面滚动超过侧边栏原始位置时启用sticky，否则恢复原始样式
    if (scrollY > this.stickyTriggerY && !this.isSticky) {
      this.enableSticky();
    } else if (scrollY <= this.stickyTriggerY && this.isSticky) {
      this.disableSticky();
    }
  }

  destroy() {
    this.resetStyles();
    
    window.removeEventListener('scroll', this.onScroll);
    window.removeEventListener('resize', this.onResize);
    
    if (this.scrollThrottle) {
      clearTimeout(this.scrollThrottle);
    }
  }

  onScroll() {
    // 使用节流优化性能, 约60fps
    if (this.scrollThrottle) {
      clearTimeout(this.scrollThrottle);
    }
    this.scrollThrottle = setTimeout(() => {
      this.updateStickyState();
    }, 16);
  }

  onResize() {
    // 在移动端禁用sticky功能
    if (window.innerWidth <= 840) {
      this.resetStyles();
      return;
    }
    this.calculateStickyTrigger();
    this.updateStickyState();
  }

  enableSticky() {
    this.isSticky = true;

    const rect = this.element.getBoundingClientRect();
    const parentRect = this.element.parentElement.getBoundingClientRect();
    this.element.style.position = 'fixed';
    this.element.style.top = '20px';
    this.element.style.left = `${parentRect.left + parentRect.width - rect.width}px`; // 保持右侧对齐
    this.element.style.width = `${rect.width}px`; // 保持原始宽度
  }

  disableSticky() {
    this.isSticky = false;
    this.resetStyles();
  }

  resetStyles() {
    Object.assign(this.element.style, this.originalStyle);
  }
}

registerBehavior("ld-side-panel", SidePanel);
