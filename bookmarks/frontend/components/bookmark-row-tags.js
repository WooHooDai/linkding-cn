import { sanitizeSvgBody } from "../utils/svg.js";
import {
  closeEditor,
  focusEnd,
  gettext,
  parseTags,
  patchBookmark,
  scrollToBookmarkTop,
  tagsEqual,
} from "./bookmark-row-editor.js";

// 触屏上按下补全候选会先失焦，浏览器的兼容鼠标事件要再晚一两百毫秒才到。
// 这段时间窗内的失焦归因于「按在补全框里」，不能当成点到外面收编辑态。
const PRESS_BLUR_GRACE = 400;

/** 标签：就地编辑、快捷标签按钮、快捷标签子菜单。 */
export const bookmarkRowTags = {
  _getOrCreateTagsContainer() {
    const existing = this.element.querySelector(".tags");
    if (existing) return existing;

    // 格子行（表格视图）：标签有自己的格子，不能落进描述格——描述格的 .description
    // 硬编码了 inline class，会被下面的 inline 分支误判。没有标签格即该视图不提供标签编辑。
    if (this.element.querySelector(".cell")) return null;

    const description = this.element.querySelector(".description");
    const descriptionContainer = this.element.querySelector(
      ".description-container",
    );

    if (description?.classList.contains("inline") && descriptionContainer) {
      const tagsContainer = document.createElement("span");
      tagsContainer.className = "tags";
      descriptionContainer.insertBefore(
        tagsContainer,
        descriptionContainer.firstChild,
      );
      return tagsContainer;
    }

    const content = this.element.querySelector(".content");
    if (!content) return null;

    const tagsContainer = document.createElement("div");
    tagsContainer.className = "tags";

    if (description) {
      description.insertAdjacentElement("afterend", tagsContainer);
    } else {
      content.appendChild(tagsContainer);
    }

    return tagsContainer;
  },

  _syncInlineDescriptionSeparator() {
    const description = this.element.querySelector(".description.inline");
    const descriptionContainer = description?.querySelector(
      ".description-container",
    );
    if (!descriptionContainer) return;

    const tagsContainer = descriptionContainer.querySelector(".tags");
    const descriptionText =
      descriptionContainer.querySelector(".description-text");
    const hasTags = Boolean(tagsContainer?.querySelector("a"));
    const hasDescription = Boolean(descriptionText?.textContent?.trim());

    // 移除旧分隔符后，在相邻非空段之间重新插入
    descriptionContainer
      .querySelectorAll(".description-separator")
      .forEach((separator) => separator.remove());

    const segments = [
      hasTags ? tagsContainer : null,
      hasDescription ? descriptionText : null,
    ];
    let prev = null;
    for (const segment of segments) {
      if (!segment) continue;
      if (prev) {
        const separator = document.createElement("span");
        separator.className = "description-separator";
        separator.textContent = " | ";
        prev.insertAdjacentElement("afterend", separator);
      }
      prev = segment;
    }
  },

  _startEditTags() {
    const tagsContainer = this._getOrCreateTagsContainer();
    if (!tagsContainer || tagsContainer._editing) return;

    tagsContainer._editing = true;
    const currentTags = Array.from(tagsContainer.querySelectorAll("a")).map(
      (tag) => tag.textContent,
    );

    tagsContainer.style.display = "none";

    const autocomplete = document.createElement("ld-tag-autocomplete");
    autocomplete.classList.add("quick-edit-tags-container");
    autocomplete.setAttribute("input-value", currentTags.join(" "));
    autocomplete.setAttribute("input-placeholder", gettext("Enter tags"));
    // 格子行：标签格窄、编辑框宽度受视口限制，多标签易超宽，改用软换行看全
    if (this.element.querySelector(".cell")) {
      autocomplete.setAttribute("multiline", "");
    }
    tagsContainer.parentNode.insertBefore(
      autocomplete,
      tagsContainer.nextSibling,
    );

    let input = null;
    let savedScrollY = null;
    const editor = this._activateQuickEditor("tags", ({ save }) => {
      const newTags = save && input ? parseTags(input.value) : currentTags;

      autocomplete.remove();
      tagsContainer._editing = false;
      this._updateTagsDisplay(tagsContainer, newTags);
      if (savedScrollY != null) window.scrollTo(0, savedScrollY);

      if (save && input && !tagsEqual(newTags, currentTags)) {
        patchBookmark(this.bookmarkId, { tag_names: newTags }).catch(
          (error) => {
            console.error("Failed to save tags:", error);
            const restoreContainer = tagsContainer.isConnected
              ? tagsContainer
              : this._getOrCreateTagsContainer();
            this._updateTagsDisplay(restoreContainer, currentTags);
          },
        );
      }
    });

    const onReady = () => {
      input = autocomplete.querySelector("input, textarea");
      if (!input || editor.closed) return;

      savedScrollY = window.scrollY;
      scrollToBookmarkTop(input);
      focusEnd(input);
      // 记录最近一次按下补全框的时间，供 blur 判断这次失焦是不是「按在候选上」
      let pressedInsideAt = 0;
      autocomplete.addEventListener("pointerdown", () => {
        pressedInsideAt = performance.now();
      });
      input.addEventListener("blur", () => {
        setTimeout(() => {
          if (autocomplete.contains(document.activeElement)) return;
          if (performance.now() - pressedInsideAt < PRESS_BLUR_GRACE) {
            focusEnd(input);
            return;
          }
          closeEditor(editor);
        }, 150);
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          closeEditor(editor, { save: false });
        }
      });
      // 监听 commit 事件（回车键保存）
      autocomplete.addEventListener("commit", () => {
        closeEditor(editor);
      });
    };

    if (autocomplete.updateComplete) {
      autocomplete.updateComplete.then(onReady);
    } else {
      requestAnimationFrame(onReady);
    }
  },

  // ==========================================
  // 快捷标签 Toggle
  // ==========================================

  onQuickTagClick(event) {
    event.preventDefault();
    event.stopPropagation();
    const btn = event.currentTarget;
    const tagName = btn.dataset.quickTagName;
    if (!tagName) return;

    const currentTags = this._readCurrentTagNames();
    const quickTagNames = tagName.split(/\s+/).filter(Boolean);
    const isActive = btn.classList.contains("active");

    let newTags;
    if (isActive) {
      newTags = currentTags.filter((t) => !quickTagNames.includes(t));
      btn.classList.remove("active");
    } else {
      const missing = quickTagNames.filter((t) => !currentTags.includes(t));
      newTags = [...currentTags, ...missing];
      btn.classList.add("active");
    }

    // 先更新 DOM（即时反馈）
    const tagsContainer = this._getOrCreateTagsContainer();
    this._updateTagsDisplay(tagsContainer, newTags);

    // 再调 API，失败时回滚
    patchBookmark(this.bookmarkId, { tag_names: newTags }).catch((error) => {
      console.error("Failed to toggle quick tag:", error);
      btn.classList.toggle("active");
      const restoreContainer = this._getOrCreateTagsContainer();
      this._updateTagsDisplay(restoreContainer, currentTags);
    });
  },

  _readCurrentTagNames() {
    const tagsContainer = this.element.querySelector(".tags");
    if (!tagsContainer) return [];
    return Array.from(tagsContainer.querySelectorAll("a"))
      .map((a) => a.textContent.trim())
      .filter(Boolean);
  },

  onQuickTagMenuTrigger(event) {
    event.preventDefault();
    event.stopPropagation();

    const wrapper = this.quickTagMenuTrigger.closest(".quick-tag-menu-wrapper");
    if (!wrapper) return;

    const existing = document.querySelector(".quick-tag-menu-panel");
    if (existing) {
      const isOwnMenu =
        this.element.contains(existing) || existing._owner === this;
      this._closeQuickTagMenu();
      if (isOwnMenu) return;
    }

    // 从模板数据构建子菜单
    const panel = document.createElement("div");
    panel.classList.add("quick-tag-menu-panel");

    // 获取所有 submenu quick tags 的数据（从 trigger 的 data 属性或 DOM 中）
    const list = this.element.closest(".bookmark-list");
    if (!list) return;

    // 从页面中获取 submenu quick tags 数据
    const quickTagsData = this._getSubmenuQuickTagsData();
    if (quickTagsData.length === 0) return;

    const currentTags = this._readCurrentTagNames();

    quickTagsData.forEach((qt) => {
      const allPresent = qt.tagNames.every((t) => currentTags.includes(t));
      const btn = document.createElement("button");
      btn.type = "button";
      btn.classList.add("btn", "btn-link", "btn-sm", "quick-tag-btn");
      if (allPresent) btn.classList.add("active");
      btn.dataset.quickTagName = qt.tagName;
      btn.title = qt.tagNames.map((t) => "#" + t).join(" ");

      // icon（优先内联 SVG，否则走 iconify-icon 组件）
      const labelEl = document.createElement("span");
      labelEl.textContent = qt.label;
      if (qt.iconData && qt.iconData.body) {
        const svg = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "svg",
        );
        svg.setAttribute("class", "action-icon");
        svg.setAttribute("width", "16");
        svg.setAttribute("height", "16");
        svg.setAttribute(
          "viewBox",
          `0 0 ${qt.iconData.width || 24} ${qt.iconData.height || 24}`,
        );
        svg.innerHTML = sanitizeSvgBody(qt.iconData.body);
        btn.append(svg, labelEl);
      } else {
        const iconEl = document.createElement("iconify-icon");
        iconEl.setAttribute("icon", qt.iconName);
        iconEl.setAttribute("width", "16");
        iconEl.setAttribute("height", "16");
        btn.append(iconEl, labelEl);
      }

      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this._toggleQuickTagFromMenu(btn, qt);
      });
      panel.appendChild(btn);
    });

    panel._owner = this;
    wrapper.appendChild(panel);

    // 右侧不足时左移
    const panelWidth = panel.offsetWidth;
    const viewportWidth = document.documentElement.clientWidth;
    const wrapperRight = wrapper.getBoundingClientRect().right;
    if (wrapperRight + panelWidth > viewportWidth - 8) {
      panel.style.left = `${viewportWidth - wrapperRight - panelWidth - 8}px`;
    }

    // 点击外部关闭
    this._onMenuDocClick = (e) => {
      if (
        !panel.contains(e.target) &&
        !this.quickTagMenuTrigger.contains(e.target)
      ) {
        this._closeQuickTagMenu();
      }
    };
    setTimeout(
      () => document.addEventListener("click", this._onMenuDocClick),
      0,
    );
  },

  _getSubmenuQuickTagsData() {
    // 从页面的 script 标签或 data 属性中获取 submenu quick tags 数据
    // 使用一个全局注入点
    if (!window.__ldQuickTagsSubmenu) {
      // 尝试从 DOM 中解析
      const trigger = this.element.querySelector(".quick-tag-menu-trigger");
      if (!trigger) return [];
      const dataAttr = trigger.dataset.quickTagsSubmenu;
      if (dataAttr) {
        try {
          window.__ldQuickTagsSubmenu = JSON.parse(dataAttr);
        } catch {
          return [];
        }
      }
    }
    return window.__ldQuickTagsSubmenu || [];
  },

  _toggleQuickTagFromMenu(btn, qt) {
    const currentTags = this._readCurrentTagNames();
    const isActive = btn.classList.contains("active");

    let newTags;
    if (isActive) {
      newTags = currentTags.filter((t) => !qt.tagNames.includes(t));
      btn.classList.remove("active");
    } else {
      const missing = qt.tagNames.filter((t) => !currentTags.includes(t));
      newTags = [...currentTags, ...missing];
      btn.classList.add("active");
    }

    // 同步 direct 按钮状态（基于实际标签状态）
    const nowAllPresent = qt.tagNames.every((t) => newTags.includes(t));
    this.quickTagBtns.forEach((directBtn) => {
      if (directBtn.dataset.quickTagName === qt.tagName) {
        directBtn.classList.toggle("active", nowAllPresent);
      }
    });

    // 先更新 DOM（即时反馈）
    const tagsContainer = this._getOrCreateTagsContainer();
    this._updateTagsDisplay(tagsContainer, newTags);

    // 再调 API，失败时回滚
    patchBookmark(this.bookmarkId, { tag_names: newTags }).catch((error) => {
      console.error("Failed to toggle quick tag from menu:", error);
      btn.classList.toggle("active");
      this.quickTagBtns.forEach((directBtn) => {
        if (directBtn.dataset.quickTagName === qt.tagName) {
          directBtn.classList.toggle("active");
        }
      });
      const restoreContainer = this._getOrCreateTagsContainer();
      this._updateTagsDisplay(restoreContainer, currentTags);
    });
  },

  _closeQuickTagMenu() {
    if (this._onMenuDocClick) {
      document.removeEventListener("click", this._onMenuDocClick);
      this._onMenuDocClick = null;
    }
    document
      .querySelectorAll(".quick-tag-menu-panel")
      .forEach((el) => el.remove());
  },

  _updateTagsDisplay(tagsContainer, tagNames) {
    if (!tagsContainer) return;

    tagsContainer.replaceChildren();
    tagNames.forEach((tag, index) => {
      if (index > 0) {
        tagsContainer.appendChild(document.createTextNode(" "));
      }

      const link = document.createElement("a");
      link.href = `?q=%23${encodeURIComponent(tag)}`;
      link.textContent = tag;
      tagsContainer.appendChild(link);
    });
    tagsContainer.style.display = "";

    this._syncInlineDescriptionSeparator();
  },
};
