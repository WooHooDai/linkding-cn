import { Behavior, registerBehavior, applyBehaviors } from "./index";

class BookmarkPagination extends Behavior {
  constructor(element) {
    super(element);

    const isStickyOn = element.dataset.stickyOn === 'true';
    if (!isStickyOn) return;

    this.scroller = document.querySelector('.body-container');
    this.container = document.querySelector('#bookmark-list-container');
    this.isOnResize = false;
    this.onScroll = this.onScroll.bind(this);
    this.onResize = this.onResize.bind(this);
    this.scroller.addEventListener('scroll', this.onScroll, { passive: true });
    window.addEventListener('resize', this.onResize, { passive: true });

    this.updateStickyState();
  }

  destroy() {
    if (this.scroller) {
      this.scroller.removeEventListener('scroll', this.onScroll);
    }
    window.removeEventListener('resize', this.onResize);
  }

  onScroll() {
    this.updateStickyState();
  }

  onResize() {
    this.isOnResize = true;
    this.updateStickyState();
  }

  openSticky() {
    this.element.classList.add('sticky');
  }

  closeSticky() {
    this.element.classList.remove('sticky');
    this.element.style.width = '';
  }

  setSticky() {
    const width = this.getContainerWidth();
    if (width === 0) return;
    this.element.style.width = `${width}px`;
  }

  getContainerWidth() {
    // 检查是否在Bundle预览环境中
    const bundlePreview = this.element.closest('turbo-frame[id="preview"]');
    if (bundlePreview) {
      // 在Bundle预览中，使用预览容器的宽度
      const previewContainer = bundlePreview.closest('aside');
      if (previewContainer) {
        return previewContainer.getBoundingClientRect().width;
      }
    }
    
    // 默认使用书签列表容器的宽度
    if (!this.container) return 0;
    return this.container.getBoundingClientRect().width;
  }

  updateStickyState() {
    const isStickyOpen = this.element.classList.contains('sticky');
    const isNearBottom = (this.scroller.scrollTop + this.scroller.clientHeight >= this.scroller.scrollHeight - 100);

    // 接近底部，关闭Sticky
    if(isNearBottom) {
      if(isStickyOpen) {
        this.closeSticky();
      }
      return;
    }

    // 若在调整窗口大小，则重新计算Sticky宽度
    if(this.isOnResize) {
      this.isOnResize = false;
      this.setSticky();
      return;
    }

    // 开启Sticky
    if(!isStickyOpen) {
      this.setSticky();
      this.openSticky();
    }
  }
}

registerBehavior("ld-pagination", BookmarkPagination);

class HeaderControls extends Behavior {
  constructor(element) {
    super(element);

    // 搜索栏粘性吸顶
    const isStickyOn = element.dataset.stickyOn === 'true';
    if (!isStickyOn) return;

    this.scroller = document.querySelector('.body-container') || window;
    this.container = document.querySelector('.main');
    this.isOnResize = false;
    this.onScroll = this.onScroll.bind(this);
    this.onResize = this.onResize.bind(this);
    window.addEventListener('scroll', this.onScroll, { passive: true });
    window.addEventListener('resize', this.onResize, { passive: true });

    this.updateStickyState();
  }

  destroy() {
    window.removeEventListener('scroll', this.onScroll);
    window.removeEventListener('resize', this.onResize);
  }

  onScroll() {
    this.updateStickyState();
  }

  onResize() {
    this.isOnResize = true;
    this.updateStickyState();
  }

  openSticky() {
    this.element.classList.add('sticky');
  }

  closeSticky() {
    this.element.classList.remove('sticky');
    this.element.style.width = '';
  }

  setSticky() {
    if (!this.container) return;
    const rect = this.container.getBoundingClientRect();
    this.element.style.width = `${rect.width}px`;
  }

  updateStickyState() {
    const isStickyOpen = this.element.classList.contains('sticky');
    const isNearTop = this.scroller.scrollTop < 100;

    // 接近顶部，关闭Sticky
    if(isNearTop) {
      if(isStickyOpen) {
        this.closeSticky();
      }
      return;
    }

    // 若在调整窗口大小，则重新计算Sticky宽度
    if(this.isOnResize) {
      this.isOnResize = false;
      this.setSticky();
      return;
    }

    // 开启Sticky
    if(!isStickyOpen) {
      this.setSticky();
      this.openSticky();
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
          let availableWidth = titleElement.offsetWidth;
          availableWidth -= 24;  // 减去favicon宽度16px + 8px间距
          if (titleSpan.offsetWidth > availableWidth) {
            titleElement.dataset.tooltip = titleSpan.textContent;
          }
        });
      }

      this.showTitleTooltip = () => {this.showFloatTooltip(this.titleElement)};
      this.hideTitleTooltip = () => this.hideFloatTooltip(this.titleElement);

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

      // 标题：禁用Safari浏览器下原生tooltip
      // 禁用标题链接鼠标响应，由标题父容器实现点击点转
      const isSafari = navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome');
      if(isSafari) {
        const titleLinkElement = element.querySelector("a.title-link")
        titleLinkElement.style.pointerEvents = 'none';

        titleElement.style.cursor = "pointer";
        this.onTitleClick = this.onTitleClick.bind(this);
        titleElement.addEventListener("click", this.onTitleClick);
      }
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
          if (isDescriptionInline) {
            // 获取标签元素
            const tagsElement = this.descriptionContainer.querySelector('.tags');
            let availableWidth = this.descriptionContainer.offsetWidth;
            availableWidth -= 7;  // 减去分隔符宽度(" | ")
            if (tagsElement) {  // 减去标签占用的宽度
              availableWidth -= tagsElement.offsetWidth;
            }
            if (window.matchMedia('(pointer: coarse)').matches) {
              if (availableWidth <= 0) { // 移动端交互形式为点击，若标签已占据全部空间，则不显示浮窗
                return;
              }
            }
            if (this.descriptionText && this.descriptionText.offsetWidth > availableWidth) {
              this.descriptionContainer.dataset.tooltip = this.descriptionText.textContent;
            }
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
    localStorage.setItem('bookmarkListScrollPosition', window.scrollY);
    localStorage.setItem('bookmarkListReturnUrl', window.location.pathname);
  }

  onTitleClick(event) {
    // 不要处理favicon的点击
    if (event.target.closest('a.favicon-link')) return;

    const link = this.titleElement.querySelector('a.title-link');
    if (!link || !link.href) return;

    const target = link.getAttribute('target');
    if (target==='_blank') {
      window.open(link.href, target, 'noopener noreferrer');
    } else {
      window.open(link.href, target);
    }

    return;
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
    
    const isStickyOn = element.dataset.stickyOn === 'true';
    if (!isStickyOn) return;
    
    this.scroller = document.querySelector('.body-container');
    this.isOnResize = false;
    this.onScroll = this.onScroll.bind(this);
    this.onResize = this.onResize.bind(this);

    this.scroller.addEventListener('scroll', this.onScroll, { passive: true });
    window.addEventListener('resize', this.onResize, { passive: true });

    this.updateStickyState();
  }

  destroy() {
    if (this.scroller) {
      this.scroller.removeEventListener('scroll', this.onScroll);
    }
    window.removeEventListener('resize', this.onResize);
  }

  onScroll() {
    this.updateStickyState();
  }

  onResize() {
    this.isOnResize = true;
    this.updateStickyState();
  }

  openSticky() {
    this.element.classList.add('sticky');
  }

  closeSticky() {
    this.element.classList.remove('sticky');
    this.element.style.left = '';
    this.element.style.width = '';
  }

  setSticky() {
    const rect = this.element.getBoundingClientRect();
    const parentRect = this.element.parentElement.getBoundingClientRect();
    this.element.style.left = `${parentRect.left + parentRect.width - rect.width}px`;
    this.element.style.width = `${rect.width}px`;
  }

  updateStickyState() {
    const isStickyOpen = this.element.classList.contains('sticky');
    const scrollY = this.scroller.scrollTop;

    // 屏幕宽度不足不启用
    if(window.innerWidth <= 840) {
      if(isStickyOpen) {
        this.closeSticky();
      }
      return;
    }

    // 滚动位置不足，关闭Sticky
    if(scrollY <= 100) {
      if(isStickyOpen) {
        this.closeSticky();
      }
      return;
    }

    // 若在调整窗口大小，则重新计算Sticky位置
    if(this.isOnResize) {
      this.isOnResize = false;
      this.closeSticky();
      this.setSticky();
      this.openSticky();
      return;
    }

    // 滚动位置超过100px时，开启Sticky
    if(!isStickyOpen) {
      this.setSticky();
      this.openSticky();
    }
    
  }
}

registerBehavior("ld-side-panel", SidePanel);

