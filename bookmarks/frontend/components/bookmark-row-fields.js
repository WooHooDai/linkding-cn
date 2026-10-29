import { initAuthorScroll } from "../utils/author-scroll.js";
import {
  autoResizeTextarea,
  closeEditor,
  focusEnd,
  patchBookmark,
  scrollToBookmarkTop,
} from "./bookmark-row-editor.js";

/** 行内可编辑的文本字段：标题、描述、作者，以及标题浮窗与描述展开/收起。 */
export const bookmarkRowFields = {
  _startEditTitle() {
    const titleLink = this.element.querySelector(".title-link");
    if (!titleLink) return;

    titleLink.parentElement.querySelector(".quick-edit-title-input")?.remove();

    const titleText = titleLink.querySelector("span");
    const currentTitle =
      titleText?.textContent.trim() || titleLink.textContent.trim();
    const titleContainer = titleLink.parentElement;

    const input = document.createElement("input");
    input.type = "text";
    input.className = "quick-edit-title-input";
    input.value = currentTitle;

    titleLink.style.display = "none";
    titleContainer.insertBefore(input, titleLink);

    const editor = this._activateQuickEditor("title", ({ save }) => {
      const newTitle = input.value.trim();
      input.remove();
      titleLink.style.display = "";
      window.scrollTo(0, savedScrollY);

      if (!save || newTitle === currentTitle) return;

      patchBookmark(this.bookmarkId, { title: newTitle })
        .then(() => {
          const displayTitle = newTitle || titleLink.href || currentTitle;
          if (titleText) titleText.textContent = displayTitle;

          const titleElement = this.element.querySelector(".title");
          const tooltipEl = titleElement?.querySelector(".float-tooltip");
          if (tooltipEl) tooltipEl.textContent = displayTitle;
        })
        .catch((error) => {
          console.error("Failed to save title:", error);
        });
    });

    input.addEventListener("blur", () => closeEditor(editor));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        closeEditor(editor);
      } else if (event.key === "Escape") {
        event.preventDefault();
        input.value = currentTitle;
        closeEditor(editor, { save: false });
      }
    });

    const savedScrollY = window.scrollY;
    scrollToBookmarkTop(input);
    focusEnd(input);
  },

  _startEditDescription() {
    const descContainer = this.element.querySelector(".description-container");
    if (!descContainer) return;

    descContainer.querySelector(".quick-edit-description-textarea")?.remove();

    let descText = descContainer.querySelector(".description-text");
    const currentDesc = descText?.textContent || "";
    const description = descContainer.closest(".description");
    const wasTruncated = descContainer.classList.contains("truncate");

    if (!descText) {
      descText = document.createElement("span");
      descText.className = "description-text";
      descContainer.appendChild(descText);
    }

    descContainer.classList.remove("truncate");
    description?.classList.remove("expanded");
    description?.classList.add("is-editing-description");
    descText.style.display = "none";

    const textarea = document.createElement("textarea");
    textarea.className = "quick-edit-description-textarea";
    textarea.rows = 1;
    textarea.value = currentDesc;

    descText.insertAdjacentElement("afterend", textarea);

    const saveDescription = (newDesc) => {
      patchBookmark(this.bookmarkId, { description: newDesc })
        .then(() => {
          if (newDesc) {
            descText.textContent = newDesc;
          } else {
            descText.remove();
          }

          if (this.descriptionContainer) {
            if (newDesc) {
              this.descriptionContainer.dataset.tooltip = newDesc;
            } else {
              delete this.descriptionContainer.dataset.tooltip;
            }

            const tooltipEl =
              this.descriptionContainer.querySelector(".float-tooltip");
            if (tooltipEl) {
              if (newDesc) {
                tooltipEl.textContent = newDesc;
              } else {
                tooltipEl.remove();
              }
            }
          }

          this._syncInlineDescriptionSeparator();
          this._reinitDescriptionToggle();
        })
        .catch((error) => {
          console.error("Failed to save description:", error);
        });
    };

    const editor = this._activateQuickEditor("description", ({ save }) => {
      const newDesc = textarea.value.trim();
      textarea.remove();
      descText.style.display = "";
      description?.classList.remove("is-editing-description");
      window.scrollTo(0, savedScrollY);

      if (wasTruncated) {
        descContainer.classList.add("truncate");
      }

      if (!save || newDesc === currentDesc) {
        if (!currentDesc) {
          descText.remove();
        }
        return;
      }
      saveDescription(newDesc);
    });

    textarea.addEventListener("input", () => autoResizeTextarea(textarea));
    textarea.addEventListener("blur", () => closeEditor(editor));
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        textarea.value = currentDesc;
        closeEditor(editor, { save: false });
      }
    });

    autoResizeTextarea(textarea);
    const savedScrollY = window.scrollY;
    scrollToBookmarkTop(textarea);
    focusEnd(textarea);
  },

  _initAuthorScroll() {
    this.element.querySelectorAll(".title .author").forEach((authorEl) => {
      initAuthorScroll(authorEl);
    });
  },

  _startEditAuthor() {
    // 列表视图的作者在 .title 行内；表格视图是独立的 .cell-author 格
    const titleContainer =
      this.element.querySelector(".title") ||
      this.element.querySelector(".cell-author");
    if (!titleContainer) return;

    titleContainer.querySelector(".quick-edit-author-input")?.remove();

    // 无 author 元素时按模板顺序补建（标题链接之后）
    let authorSpan = titleContainer.querySelector(".author");
    const currentAuthor = authorSpan?.textContent?.trim() || "";
    const titleLink = titleContainer.querySelector(".title-link");

    if (!authorSpan) {
      authorSpan = document.createElement("span");
      authorSpan.className = "author";
      if (titleLink) {
        titleLink.insertAdjacentElement("afterend", authorSpan);
      } else {
        titleContainer.appendChild(authorSpan);
      }
    }

    authorSpan.style.display = "none";

    const input = document.createElement("input");
    input.type = "text";
    input.className = "quick-edit-author-input";
    input.value = currentAuthor;
    authorSpan.insertAdjacentElement("afterend", input);

    const editor = this._activateQuickEditor("author", ({ save }) => {
      const newAuthor = input.value.trim();
      input.remove();
      authorSpan.style.display = "";
      window.scrollTo(0, savedScrollY);

      if (!save || newAuthor === currentAuthor) {
        if (!currentAuthor) {
          authorSpan.remove();
        }
        return;
      }

      patchBookmark(this.bookmarkId, { author: newAuthor })
        .then(() => {
          if (newAuthor) {
            authorSpan.textContent = newAuthor;
            initAuthorScroll(authorSpan);
          } else {
            authorSpan.remove();
          }
        })
        .catch((error) => {
          console.error("Failed to save author:", error);
        });
    });

    input.addEventListener("blur", () => closeEditor(editor));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        closeEditor(editor);
      } else if (event.key === "Escape") {
        event.preventDefault();
        input.value = currentAuthor;
        closeEditor(editor, { save: false });
      }
    });

    const savedScrollY = window.scrollY;
    scrollToBookmarkTop(input);
    focusEnd(input);
  },

  onTitleClick(event) {
    if (
      event.target.closest("a.favicon-link") ||
      event.target.closest("label.bulk-edit-checkbox")
    )
      return;

    const link = this.titleElement.querySelector("a.title-link");
    if (!link || !link.href) return;

    const target = link.getAttribute("target");
    if (target === "_blank") {
      window.open(link.href, target, "noopener noreferrer");
    } else {
      window.open(link.href, target);
    }
  },

  showFloatTooltip(targetEl, text) {
    if (!targetEl) return;
    const content = text != null ? text : targetEl.dataset.tooltip;
    if (!content) return;

    let tooltip = targetEl.querySelector(".float-tooltip");
    if (tooltip) {
      tooltip.style.display =
        tooltip.style.display === "none" ? "block" : "none";
      return;
    }

    tooltip = document.createElement("div");
    tooltip.className = "float-tooltip";
    tooltip.textContent = content;
    targetEl.appendChild(tooltip);
  },

  hideFloatTooltip(targetEl) {
    const tooltip = targetEl.querySelector(".float-tooltip");
    if (tooltip) tooltip.style.display = "none";
  },

  /**
   * 当前标题是否被截断（文本自然宽度超出链接可见宽度）。
   * 在 hover/focus 触发时实时测量，避免窗口缩放、布局变化后旧的截断状态残留。
   */
  _isTitleTruncated() {
    const link = this.titleElement?.querySelector("a.title-link");
    const span = link?.querySelector("span");
    if (!link || !span) return false;
    if (!span.textContent.trim()) return false;

    const range = document.createRange();
    range.selectNodeContents(span);
    const naturalWidth = range.getBoundingClientRect().width;
    return naturalWidth > link.clientWidth + 0.5;
  },

  initTitleTooltip() {
    this.titleElement = this.element.querySelector(".title");
    if (!this.titleElement) return;

    this.showTitleTooltip = () => {
      // 仅在当前真实截断时才弹出浮层；标题已完整显示则不重复提示
      if (!this._isTitleTruncated()) return;
      const text =
        this.titleElement.querySelector("a.title-link span")?.textContent || "";
      this.showFloatTooltip(this.titleElement, text);
    };
    this.hideTitleTooltip = () => this.hideFloatTooltip(this.titleElement);

    const isTouch = window.matchMedia("(pointer: coarse)").matches;
    if (!isTouch) {
      this.titleElement.addEventListener("mouseenter", this.showTitleTooltip, {
        passive: true,
      });
      this.titleElement.addEventListener("mouseleave", this.hideTitleTooltip, {
        passive: true,
      });
    }
    this.titleElement.addEventListener("focus", this.showTitleTooltip, {
      passive: true,
    });
    this.titleElement.addEventListener("blur", this.hideTitleTooltip, {
      passive: true,
    });

    // Safari 特殊处理
    const isSafari =
      navigator.userAgent.includes("Safari") &&
      !navigator.userAgent.includes("Chrome");
    if (isSafari) {
      const titleLinkElement = this.element.querySelector("a.title-link");
      if (titleLinkElement) titleLinkElement.style.pointerEvents = "none";
      this.titleElement.style.cursor = "pointer";
      this.titleElement.addEventListener("click", this.onTitleClick);
    }
  },

  /**
   * 当前描述是否被截断（按当前布局实时测量，供点击 / hover 时判断）。
   * 同行模式比较文本自然宽度与 tags 占用后的剩余宽度；分行模式比较内容高度与裁剪高度。
   */
  _isDescriptionTruncated() {
    if (!this._descriptionText) return false;

    if (this._descriptionIsInline) {
      const tagsElement = this.descriptionContainer.querySelector(".tags");
      let availableWidth = this.descriptionContainer.clientWidth - 7;
      if (tagsElement) availableWidth -= tagsElement.offsetWidth;
      const range = document.createRange();
      range.selectNodeContents(this._descriptionText);
      const naturalWidth = range.getBoundingClientRect().width;
      return naturalWidth > availableWidth + 0.5;
    }

    return (
      this.descriptionContainer.scrollHeight >
      this.descriptionContainer.clientHeight + 0.5
    );
  },

  /** 当前描述是否处于展开态（折叠操作不做截断拦截）。 */
  _isDescriptionExpanded() {
    return this._descriptionIsInline
      ? this._descriptionText?.classList.contains("expanded")
      : this.descriptionElement?.classList.contains("expanded");
  },

  /** 按当前是否可展开刷新 cursor 提示类（布局变化后随 hover 重新校正，避免残留）。 */
  _refreshDescriptionHint() {
    if (!this.descriptionContainer) return;
    const expandable =
      this._isDescriptionExpanded() || this._isDescriptionTruncated();
    if (this._descriptionIsInline) {
      this._descriptionText?.classList.toggle("is-expandable", expandable);
    } else {
      this.descriptionContainer.classList.toggle("is-expandable", expandable);
    }
  },

  initDescriptionToggle() {
    // 格子行的展开/折叠是整行行为、由视图自身接管：这里再挂钩会把点击吞掉
    // （stopPropagation + 按列表语义切 .expanded），行高就不会跟着变
    if (this.element.querySelector(".cell")) return;
    this.descriptionContainer = this.element.querySelector(
      ".description-container",
    );
    if (!this.descriptionContainer) return;

    this.descriptionElement = this.element.querySelector(".description");
    if (!this.descriptionElement) return;

    this._descriptionText =
      this.descriptionContainer.querySelector(".description-text");
    this._descriptionIsInline =
      this.descriptionElement.classList.contains("inline");

    const target = this._descriptionIsInline
      ? this._descriptionText
      : this.descriptionContainer;
    if (!target) return;

    this._descriptionToggleTarget = target;
    this.onToggleDescription = (event) => {
      event.stopPropagation();
      // 拖选描述文本松手会触发 click，此时不折叠
      if (this._hasTextSelection()) return;
      // 未展开且当前并未截断：不响应点击
      if (!this._isDescriptionExpanded() && !this._isDescriptionTruncated())
        return;

      if (this._descriptionIsInline) {
        const expanded = !this._descriptionText.classList.contains("expanded");
        this._descriptionText.classList.toggle("expanded", expanded);
        this.descriptionContainer.classList.toggle("expanded", expanded);
      } else {
        const expanding =
          !this.descriptionElement.classList.contains("expanded");
        if (!expanding) {
          this._descriptionScrollTop = this.descriptionContainer.scrollTop || 0;
        }
        this.descriptionElement.classList.toggle("expanded", expanding);
        if (expanding) {
          this.descriptionContainer.scrollTop = this._descriptionScrollTop || 0;
        }
      }
      this._refreshDescriptionHint();
    };
    target.addEventListener("click", this.onToggleDescription);

    // 鼠标进入整行即按当前布局刷新可点击光标提示：紧凑视图下描述随行 hover 才显示，
    // 悬停标题区时也要能校正；用实例闭包持有 this，避免回调丢失 Behavior 实例上下文
    this.onRefreshDescriptionHint = () => this._refreshDescriptionHint();
    this.element.addEventListener("mouseenter", this.onRefreshDescriptionHint, {
      passive: true,
    });

    this._refreshDescriptionHint();
  },

  /**
   * 判断用户是否正在选择描述区域内的文本。
   * 展开态下鼠标拖选/触摸长按选择文本，松手时浏览器会合成 click，
   * 此检查用于避免把“选择文本”误判为“点击折叠”。
   */
  _hasTextSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return false;
    const isInside = (node) =>
      !!node && this.descriptionContainer.contains(node);
    return isInside(selection.anchorNode) || isInside(selection.focusNode);
  },

  /**
   * 清除旧的描述 toggle 监听，重新检测截断并绑定点击展开。
   * 供内联编辑保存后调用。
   */
  _reinitDescriptionToggle() {
    // 移除旧 handler
    if (this.onToggleDescription) {
      const target = this._descriptionToggleTarget || this.descriptionContainer;
      target.removeEventListener("click", this.onToggleDescription);
      this.element?.removeEventListener(
        "mouseenter",
        this.onRefreshDescriptionHint,
      );
      this.onToggleDescription = null;
      this._descriptionToggleTarget = null;
    }
    // 重置展开态和可点击提示类
    if (this.descriptionContainer) {
      this.descriptionContainer.classList.remove("expanded");
      this.descriptionContainer.classList.remove("is-expandable");
    }
    if (this.descriptionElement) {
      this.descriptionElement.classList.remove("expanded");
    }
    const dt = this.descriptionContainer?.querySelector(".description-text");
    if (dt) dt.classList.remove("is-expandable");

    this.initDescriptionToggle();
  },
};
