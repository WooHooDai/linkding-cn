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

class HeaderControls extends Behavior {
  constructor(element) {
    super(element);

    // 搜索栏粘性吸顶
    const isStickyOn = element.dataset.stickyOn === 'true' || element.classList.contains('sticky-on');
    if (!isStickyOn) return;

    this.onScroll = this.onScroll.bind(this);
    this.onResize = this.onResize.bind(this);
    window.addEventListener('scroll', this.onScroll, { passive: true });
    window.addEventListener('resize', this.onResize, { passive: true });
    this.updateStickyState(); // 初始化状态
  }

  destroy() {
    window.removeEventListener('scroll', this.onScroll);
    window.removeEventListener('resize', this.onResize);
  }

  onScroll() {
    this.updateStickyState();
  }

  onResize() {
    this.updateStickyState();
  }

  openSticky() {
    this.element.classList.add('sticky');

    // 与 .section-header 容器对齐并不超出宽度
    const sectionHeader = this.element.closest('.section-header');
    if (!sectionHeader) return;
    const r = sectionHeader.getBoundingClientRect();
    this.element.style.left = `${r.left}px`;
    this.element.style.width = `${r.width}px`;
  }

  closeSticky() {
    this.element.classList.remove('sticky');
    this.element.style.left = '';
    this.element.style.width = '';
  }

  updateStickyState() {
    const sectionHeader = this.element.closest('.section-header');
    if (!sectionHeader) return;
    const rect = sectionHeader.getBoundingClientRect();
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const isInViewport = rect.top < viewportHeight && rect.bottom > (rect.bottom-rect.top);

    if (isInViewport) {
      if (this.element.classList.contains('sticky')) {
        this.closeSticky();
      }
    } else {
      if (!this.element.classList.contains('sticky')) {
        this.openSticky();
      }
    }
  }
}

registerBehavior('ld-header-controls', HeaderControls);

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

    // 标题浮窗：当标题被截断时显示完整标题
    const titleElement = element.querySelector(".title");
    if (titleElement) {
      this.titleElement = titleElement;
      this.titleElement = titleElement;
      const titleSpan = titleElement.querySelector("span");
      if (titleSpan) {
        requestAnimationFrame(() => {
          if (titleSpan.offsetWidth > titleElement.offsetWidth) {
            titleElement.dataset.tooltip = titleSpan.textContent;
          }
        });
      }

      this.showTitleTooltip = () => {this.showFloatTooltip(this.titleElement)};
      this.hideTitleTooltip = () => this.hideFloatTooltip(this.titleElement);

      this.clickTimer = null;
      const DOUBLE_CLICK_DELAY = 300;
      if (window.matchMedia('(pointer: coarse)').matches) {
        // TODO: 移动端尚未确定标题浮窗交互方式
      }
      if (!window.matchMedia('(pointer: coarse)').matches) {
        // 移动端不添加鼠标事件，否则mouseleave会被触发，产生干扰
        titleElement.addEventListener('mouseenter', this.showTitleTooltip, { passive: true });
        titleElement.addEventListener('mouseleave', this.hideTitleTooltip, { passive: true });
      }
      titleElement.addEventListener('focus', this.showTitleTooltip, { passive: true });
      titleElement.addEventListener('blur', this.hideTitleTooltip, { passive: true });
    }

    // 描述浮窗：当描述被截断时显示完整描述
    const descriptionElement = element.querySelector(".description");
    const descriptionContainer = element.querySelector(".description-container");
    const descriptionText = descriptionContainer?.querySelector(".description-text");
    const isDescriptionInline = descriptionElement?.classList.contains("inline");
    this.descriptionElement = descriptionElement; 
    this.descriptionContainer = descriptionContainer;
    this.descriptionText = descriptionText;
    this.isDescriptionInline = isDescriptionInline;

    if (this.descriptionContainer) {
      if (this.descriptionText) {
        requestAnimationFrame(() => {
          // 行内描述
          if (isDescriptionInline && this.descriptionText.offsetWidth > this.descriptionContainer.offsetWidth) {
            this.descriptionContainer.dataset.tooltip = this.descriptionText.textContent;
          // 分行描述（单行或多行）  
          } else if (!isDescriptionInline && this.descriptionContainer.scrollHeight > this.descriptionContainer.clientHeight) {
            this.descriptionContainer.dataset.tooltip = this.descriptionText.textContent;
          }
        });
      }
      // 绑定事件
      this.showDescriptionTooltip = () => this.showFloatTooltip(this.descriptionContainer);
      this.hideDescriptionTooltip = () => this.hideFloatTooltip(this.descriptionContainer);
      this.descriptionContainer.addEventListener('focus', this.showDescriptionTooltip, { passive: true });
      this.descriptionContainer.addEventListener('blur', this.hideDescriptionTooltip, { passive: true });
      if (window.matchMedia('(pointer: coarse)').matches) {
        // 电脑端不添加click事件，否则影响在浮窗内选取文字
        this.descriptionContainer.addEventListener('click', this.showDescriptionTooltip, { passive: true });
      }
      if (!window.matchMedia('(pointer: coarse)').matches) {
        // 移动端不添加鼠标事件，否则mouseleave会被触发，产生干扰
        this.descriptionContainer.addEventListener('mouseenter', this.showDescriptionTooltip, { passive: true });
        this.descriptionContainer.addEventListener('mouseleave', this.hideDescriptionTooltip, { passive: true });
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

    // 清理浮窗事件
    if (this.titleElement) {
      this.titleElement.removeEventListener('mouseenter', this.showTitleTooltip);
      this.titleElement.removeEventListener('mouseleave', this.hideTitleTooltip);
      this.titleElement.removeEventListener('touchstart', this.showTitleTooltip);
      this.titleElement.removeEventListener('touchend', this.hideTitleTooltip);
      this.titleElement.removeEventListener('focus', this.showTitleTooltip);
      this.titleElement.removeEventListener('blur', this.hideTitleTooltip);
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
    localStorage.setItem('bookmarkListScrollPosition', window.scrollY);
    localStorage.setItem('bookmarkListReturnUrl', window.location.pathname);
  }

  showFloatTooltip(targetEl) {
    if (!targetEl || !targetEl.dataset.tooltip) return;

    // 如果浮窗已存在，则显示
    let tooltip = targetEl.querySelector('.float-tooltip');
    if (tooltip) {
      tooltip.style.display = tooltip.style.display === 'none' ? 'block' : 'none';
      return;
    }

    // 否则创建浮窗
    tooltip = document.createElement('div');
    tooltip.className = 'float-tooltip';
    tooltip.textContent = targetEl.dataset.tooltip;
    targetEl.appendChild(tooltip);
  }

  hideFloatTooltip(targetEl) {
    const tooltip = targetEl.querySelector('.float-tooltip');
    if (tooltip) {
      tooltip.style.display = 'none';
    }
  }

  showDescriptionFloatTooltip() {
    this.showFloatTooltip(this.descriptionContainer);
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
