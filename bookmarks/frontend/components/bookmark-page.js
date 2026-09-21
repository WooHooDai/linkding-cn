import { Behavior, registerBehavior } from "./runtime.js";
import { sanitizeSvgBody } from "../utils/svg.js";
import { handleBookmarkAction } from "../utils/bookmark-action.js";
import { getCSRFToken } from "../utils/csrf.js";

// ==========================================
// 书签列表
// ==========================================

function gettext(s) {
  return window.gettext ? window.gettext(s) : s;
}

async function patchBookmark(bookmarkId, data) {
  const response = await fetch(`/api/bookmarks/${bookmarkId}/`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken(),
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    throw new Error(`Failed to update bookmark ${bookmarkId}`);
  }

  return response;
}


function autoResizeTextarea(textarea) {
  textarea.style.height = "auto";
  textarea.style.overflowY = "hidden";

  const maxHeightPx = Number.parseFloat(
    getComputedStyle(textarea).maxHeight,
  ) || Infinity;
  const nextHeight = Math.min(textarea.scrollHeight, maxHeightPx);
  textarea.style.height = `${nextHeight}px`;
  textarea.style.overflowY =
    textarea.scrollHeight > maxHeightPx ? "auto" : "hidden";
}

function focusEnd(element) {
  element.focus({ preventScroll: true });
  element.setSelectionRange(element.value.length, element.value.length);
}

function scrollToBookmarkTop(element) {
  if (!window.matchMedia("(hover: none) and (pointer: coarse)").matches)
    return;
  const li = element.closest("li[ld-bookmark-item]");
  if (!li) return;
  const stickyHeader = document.querySelector(
    '.section-header[data-sticky-on="true"]',
  );
  const stickyHeight = stickyHeader ? stickyHeader.offsetHeight : 0;
  const rect = li.getBoundingClientRect();
  const currentScroll =
    window.pageYOffset || document.scrollingElement.scrollTop;
  const targetTop = rect.top + currentScroll - stickyHeight;
  window.scrollTo({ top: targetTop, behavior: "instant" });
}

function parseTags(value) {
  const seen = new Set();
  return (value || "")
    .split(/\s+/)
    .map((tag) => tag.trim())
    .filter((tag) => {
      if (!tag || seen.has(tag)) return false;
      seen.add(tag);
      return true;
    });
}

function tagsEqual(left, right) {
  const leftSorted = [...left].sort();
  const rightSorted = [...right].sort();
  return JSON.stringify(leftSorted) === JSON.stringify(rightSorted);
}

// 移动端编辑态遮罩
let editorOverlay = null;

function isMobileDevice() {
  return window.matchMedia("(hover: none) and (pointer: coarse)").matches;
}

function createEditorOverlay() {
  if (editorOverlay) return;
  editorOverlay = document.createElement("div");
  editorOverlay.className = "editor-overlay";
  editorOverlay.addEventListener("touchend", (e) => {
    e.preventDefault();
    closeActiveEditor();
  }, { passive: false });
  document.body.appendChild(editorOverlay);
}

function removeEditorOverlay() {
  if (editorOverlay) {
    editorOverlay.remove();
    editorOverlay = null;
  }
}


let activeEditor = null;

function activateEditor(bookmarkId, fieldType, closeFn) {
  // 移动端创建遮罩
  if (isMobileDevice()) {
    createEditorOverlay();
  }
  
  const editor = {
    bookmarkId,
    fieldType,
    closed: false,
    close(options = {}) {
      if (editor.closed) return;
      editor.closed = true;
      if (activeEditor === editor) {
        activeEditor = null;
        // 移动端移除遮罩
        if (isMobileDevice()) {
          removeEditorOverlay();
        }
      }
      closeFn({ save: options.save !== false });
    },
  };
  activeEditor = editor;
  return editor;
}

function isActiveEditor(bookmarkId, fieldType) {
  return (
    activeEditor?.bookmarkId === bookmarkId &&
    activeEditor?.fieldType === fieldType
  );
}

function closeEditor(editor, options = {}) {
  if (!editor) return;
  if (activeEditor === editor) {
    activeEditor = null;
    // 移动端移除遮罩
    if (isMobileDevice()) {
      removeEditorOverlay();
    }
  }
  editor.close(options);
}

function closeActiveEditor(options = {}) {
  closeEditor(activeEditor, options);
}

class BookmarkItem extends Behavior {
  constructor(element) {
    super(element);

    // 绑定基础事件
    this.onToggleNotes = this.onToggleNotes.bind(this);
    this.onEditClick = this.onEditClick.bind(this);
    this.onTitleClick = this.onTitleClick.bind(this);
    this.onQuickEdit = this.onQuickEdit.bind(this);
    this.onQuickEditMouseDown = this.onQuickEditMouseDown.bind(this);
    this.onNotesBlur = this.onNotesBlur.bind(this);
    this.onNotesKeydown = this.onNotesKeydown.bind(this);

    this.scroller = document.scrollingElement;
    this.bookmarkId = element.dataset.bookmarkId;

    // 初始化 Notes
    this.notesToggle = element.querySelector(".toggle-notes");
    if (this.notesToggle) {
      this.notesToggle.addEventListener("click", this.onToggleNotes);
    }

    // 初始化内联编辑
    this.notesMarkdown = this._getNotesContainer()?.querySelector(".markdown");
    this.notesEditor = this._getNotesContainer()?.querySelector(
      ".inline-edit-textarea",
    );
    this.originalNotes = this.notesEditor?.value || "";

    if (this.notesEditor) {
      this.notesEditor.addEventListener("blur", this.onNotesBlur);
      this.notesEditor.addEventListener("keydown", this.onNotesKeydown);
    }

    // 初始化快捷编辑按钮
    this.quickEditBtns = element.querySelectorAll(".quick-edit-btn");
    this.quickEditBtns.forEach((btn) => {
      btn.setAttribute("aria-pressed", "false");
      btn.addEventListener("mousedown", this.onQuickEditMouseDown);
      btn.addEventListener("click", this.onQuickEdit);
    });

    // 初始化快捷标签按钮
    this.onQuickTagClick = this.onQuickTagClick.bind(this);
    this.onQuickTagMenuTrigger = this.onQuickTagMenuTrigger.bind(this);
    this.quickTagBtns = element.querySelectorAll(".quick-tag-btn");
    this.quickTagBtns.forEach((btn) => {
      btn.addEventListener("click", this.onQuickTagClick);
    });
    this.quickTagMenuTrigger = element.querySelector(".quick-tag-menu-trigger");
    if (this.quickTagMenuTrigger) {
      this.quickTagMenuTrigger.addEventListener("click", this.onQuickTagMenuTrigger);
    }

    // 初始化 Edit Action
    this.editAction = element.querySelector(".edit-action");
    if (this.editAction) {
      this.editAction.addEventListener("click", this.onEditClick);
    }


    // 初始化标题浮窗
    this.initTitleTooltip();

    // 初始化描述浮窗
    this.initDescriptionToggle();

    // 初始化 action 按钮（分享、已读、归档、删除等）
    this._initActionButtons();
  }

  destroy() {
    if (activeEditor?.bookmarkId === this.bookmarkId) {
      closeActiveEditor({ save: false });
    }

    if (this.notesToggle)
      this.notesToggle.removeEventListener("click", this.onToggleNotes);
    if (this.editAction)
      this.editAction.removeEventListener("click", this.onEditClick);

    this.quickEditBtns.forEach((btn) => {
      btn.removeEventListener("mousedown", this.onQuickEditMouseDown);
      btn.removeEventListener("click", this.onQuickEdit);
    });

    this.quickTagBtns.forEach((btn) => {
      btn.removeEventListener("click", this.onQuickTagClick);
    });
    if (this.quickTagMenuTrigger) {
      this.quickTagMenuTrigger.removeEventListener("click", this.onQuickTagMenuTrigger);
    }
    this._closeQuickTagMenu();

    if (this.notesEditor) {
      this.notesEditor.removeEventListener("blur", this.onNotesBlur);
      this.notesEditor.removeEventListener("keydown", this.onNotesKeydown);
    }

    if (this.titleElement) {
      this.titleElement.removeEventListener(
        "mouseenter",
        this.showTitleTooltip,
      );
      this.titleElement.removeEventListener(
        "mouseleave",
        this.hideTitleTooltip,
      );
      this.titleElement.removeEventListener("focus", this.showTitleTooltip);
      this.titleElement.removeEventListener("blur", this.hideTitleTooltip);
      this.titleElement.removeEventListener("click", this.onTitleClick);
    }

    if (this.onToggleDescription) {
      const target =
        this._descriptionToggleTarget || this.descriptionContainer;
      target.removeEventListener("click", this.onToggleDescription);
    }
  }

  onToggleNotes(event) {
    event.preventDefault();
    event.stopPropagation();
    const next = !this.element.classList.contains("show-notes");
    this.element.dataset.notesEnabled = String(next);
    this.element.classList.toggle("show-notes", next);
  }

  onQuickEditMouseDown(event) {
    if (event.button !== 0) return;
    event.preventDefault();
  }

  onQuickEdit(event) {
    event.preventDefault();
    event.stopPropagation();
    const type = event.currentTarget.dataset.quickEdit;
    this.startQuickEdit(type);
  }

  startQuickEdit(fieldType) {
    if (isActiveEditor(this.bookmarkId, fieldType)) {
      closeActiveEditor();
      return;
    }

    closeActiveEditor();

    switch (fieldType) {
      case "title":
        this._startEditTitle();
        break;
      case "description":
        this._startEditDescription();
        break;
      case "notes":
        this._startEditNotes();
        break;
      case "tags":
        this._startEditTags();
        break;
    }
  }

  _getNotesContainer() {
    return this.element.querySelector(".inline-edit-notes");
  }

  _activateQuickEditor(fieldType, closeFn) {
    this._setQuickEditButtonActive(fieldType, true);

    return activateEditor(this.bookmarkId, fieldType, (options) => {
      try {
        closeFn(options);
      } finally {
        this._setQuickEditButtonActive(fieldType, false);
      }
    });
  }

  _setQuickEditButtonActive(fieldType, active) {
    const button = this.element.querySelector(
      `.quick-edit-btn[data-quick-edit="${fieldType}"]`,
    );
    if (!button) return;

    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }

  _getOrCreateTagsContainer() {
    const existing = this.element.querySelector(".tags");
    if (existing) return existing;

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
  }

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
    const hasDescription = Boolean(descriptionText?.textContent);
    let separator = descriptionContainer.querySelector(
      ".description-separator",
    );

    if (hasTags && hasDescription && !separator) {
      separator = document.createElement("span");
      separator.className = "description-separator";
      separator.textContent = " | ";
      tagsContainer.insertAdjacentElement("afterend", separator);
    } else if ((!hasTags || !hasDescription) && separator) {
      separator.remove();
    }
  }

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
          if (titleElement?.dataset.tooltip) {
            titleElement.dataset.tooltip = displayTitle;
            const tooltipEl = titleElement.querySelector(".float-tooltip");
            if (tooltipEl) tooltipEl.textContent = displayTitle;
          }
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
  }

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
  }

  _startEditNotes() {
    if (!this.notesMarkdown || !this.notesEditor) {
      this._createNotesEditor();
    }
    if (!this.notesMarkdown || !this.notesEditor) return;

    const wasShowingNotes = this.element.classList.contains("show-notes");
    this.element.classList.add("show-notes");
    this.element.dataset.notesEnabled = "true";

    // 保存显示态滚动位置
    this._savedNotesScrollTop = this.notesMarkdown.scrollTop || 0;

    this.notesMarkdown.style.display = "none";
    this.notesEditor.style.display = "block";
    autoResizeTextarea(this.notesEditor);

    const savedScrollY = window.scrollY;
    scrollToBookmarkTop(this.notesEditor);
    focusEnd(this.notesEditor);
    requestAnimationFrame(() => {
      this.notesEditor.scrollTop = this._savedNotesScrollTop;
    });

    this._activateQuickEditor("notes", ({ save }) => {
      this.notesEditor.removeEventListener("input", this._onNotesInput);
      this._saveNotesAndSwitch({ save });
      if (!wasShowingNotes) {
        this.element.classList.remove("show-notes");
        this.element.dataset.notesEnabled = "false";
      }
      window.scrollTo(0, savedScrollY);
    });

    this._onNotesInput = () => autoResizeTextarea(this.notesEditor);
    this.notesEditor.addEventListener("input", this._onNotesInput);
  }

  _createNotesEditor() {
    const contentDiv = this.element.querySelector(".content");
    if (!contentDiv) return;

    const notesContainer = document.createElement("div");
    notesContainer.className = "inline-edit inline-edit-notes";
    notesContainer.style.display = "block";

    this.notesMarkdown = document.createElement("div");
    this.notesMarkdown.className = "markdown";

    this.notesEditor = document.createElement("textarea");
    this.notesEditor.className = "inline-edit-textarea";
    this.notesEditor.rows = 1;
    this.notesEditor.value = "";
    this.notesEditor.placeholder = gettext("Enter notes here");
    this.notesEditor.style.display = "block";

    notesContainer.appendChild(this.notesMarkdown);
    notesContainer.appendChild(this.notesEditor);
    contentDiv.parentNode.insertBefore(notesContainer, contentDiv.nextSibling);

    this.notesEditor.addEventListener("blur", this.onNotesBlur);
    this.notesEditor.addEventListener("keydown", this.onNotesKeydown);
    this.originalNotes = "";
  }

  onNotesBlur() {
    if (isActiveEditor(this.bookmarkId, "notes")) {
      closeActiveEditor();
    }
  }

  async _saveNotesAndSwitch({ save = true } = {}) {
    if (!this.notesEditor) return;

    const newNotes = this.notesEditor.value;
    if (!save) {
      this.notesEditor.value = this.originalNotes;
      this._switchNotesToDisplay();
      return;
    }

    if (newNotes === this.originalNotes) {
      this._switchNotesToDisplay();
      return;
    }

    const previousNotes = this.originalNotes;

    if (this.notesMarkdown) {
      this.notesMarkdown.style.display = "";
    }
    this.notesEditor.style.display = "none";
    this.notesEditor.style.height = "";

    try {
      await patchBookmark(this.bookmarkId, { notes: newNotes });

      const htmlResponse = await fetch(
        `/api/bookmarks/${this.bookmarkId}/notes_html/`,
      );
      if (htmlResponse.ok && this.notesMarkdown) {
        const data = await htmlResponse.json();
        this.notesMarkdown.innerHTML = data.html || "";
      } else if (this.notesMarkdown) {
        this.notesMarkdown.textContent = newNotes;
      }

      this.originalNotes = newNotes;
      this._switchNotesToDisplay();
    } catch (error) {
      console.error("Failed to save notes:", error);
      this.originalNotes = previousNotes;
      if (this.notesEditor) {
        this.notesEditor.value = previousNotes;
      }
      this._switchNotesToDisplay();
    }
  }

  _switchNotesToDisplay() {
    const notesContainer = this._getNotesContainer();

    if (!this.notesEditor) return;

    // 保存编辑态滚动位置
    const editorScrollTop = this.notesEditor.scrollTop || 0;

    this.notesEditor.style.fontStyle = "";
    this.notesEditor.placeholder = "";

    if (this.notesMarkdown) {
      this.notesMarkdown.style.display = "";
    }
    this.notesEditor.style.display = "none";
    this.notesEditor.style.height = "";

    if (!this.originalNotes && notesContainer) {
      this.notesEditor.removeEventListener("blur", this.onNotesBlur);
      this.notesEditor.removeEventListener("keydown", this.onNotesKeydown);
      notesContainer.remove();
      this.notesMarkdown = null;
      this.notesEditor = null;
      return;
    }

    // 恢复到显示态（优先用编辑态滚动位置，其次用进入编辑态前的位置）
    if (this.notesMarkdown) {
      this.notesMarkdown.scrollTop = editorScrollTop || this._savedNotesScrollTop || 0;
    }
  }

  onNotesKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeActiveEditor({ save: false });
    }
  }

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
      input = autocomplete.querySelector("input");
      if (!input || editor.closed) return;

      savedScrollY = window.scrollY;
      scrollToBookmarkTop(input);
      focusEnd(input);
      input.addEventListener("blur", () => {
        setTimeout(() => {
          if (autocomplete.contains(document.activeElement)) return;
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
  }

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
  }

  _readCurrentTagNames() {
    const tagsContainer = this.element.querySelector(".tags");
    if (!tagsContainer) return [];
    return Array.from(tagsContainer.querySelectorAll("a"))
      .map((a) => a.textContent.trim())
      .filter(Boolean);
  }

  onQuickTagMenuTrigger(event) {
    event.preventDefault();
    event.stopPropagation();

    const wrapper = this.quickTagMenuTrigger.closest(".quick-tag-menu-wrapper");
    if (!wrapper) return;

    const existing = document.querySelector(".quick-tag-menu-panel");
    if (existing) {
      const isOwnMenu = this.element.contains(existing) || existing._owner === this;
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
      btn.title = qt.tagNames.map(t => "#" + t).join(" ");

      // icon（优先内联 SVG，否则走 iconify-icon 组件）
      const labelEl = document.createElement("span");
      labelEl.textContent = qt.label;
      if (qt.iconData && qt.iconData.body) {
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", "action-icon");
        svg.setAttribute("width", "16");
        svg.setAttribute("height", "16");
        svg.setAttribute("viewBox", `0 0 ${qt.iconData.width || 24} ${qt.iconData.height || 24}`);
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
      if (!panel.contains(e.target) && !this.quickTagMenuTrigger.contains(e.target)) {
        this._closeQuickTagMenu();
      }
    };
    setTimeout(() => document.addEventListener("click", this._onMenuDocClick), 0);
  }

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
  }

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
  }

  _closeQuickTagMenu() {
    if (this._onMenuDocClick) {
      document.removeEventListener("click", this._onMenuDocClick);
      this._onMenuDocClick = null;
    }
    document.querySelectorAll(".quick-tag-menu-panel").forEach((el) => el.remove());
  }

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

    if (tagNames.length === 0) {
      tagsContainer.remove();
    }

    this._syncInlineDescriptionSeparator();
  }

  onEditClick() {
    if (this.scroller) {
      localStorage.setItem(
        "ld:bookmark-list:scroll",
        JSON.stringify({
          position: this.scroller.scrollTop,
          returnUrl: window.location.pathname,
        }),
      );
    }
  }


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
  }

  showFloatTooltip(targetEl) {
    if (!targetEl || !targetEl.dataset.tooltip) return;

    let tooltip = targetEl.querySelector(".float-tooltip");
    if (tooltip) {
      tooltip.style.display =
        tooltip.style.display === "none" ? "block" : "none";
      return;
    }

    tooltip = document.createElement("div");
    tooltip.className = "float-tooltip";
    tooltip.textContent = targetEl.dataset.tooltip;
    targetEl.appendChild(tooltip);
  }

  hideFloatTooltip(targetEl) {
    const tooltip = targetEl.querySelector(".float-tooltip");
    if (tooltip) tooltip.style.display = "none";
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
  }

  initDescriptionToggle() {
    this.descriptionContainer = this.element.querySelector(
      ".description-container",
    );
    if (!this.descriptionContainer) return;

    this.descriptionElement = this.element.querySelector(".description");
    if (!this.descriptionElement) return;

    // 检测描述是否被截断，绑定点击展开
    requestAnimationFrame(() => {
      const descriptionText =
        this.descriptionContainer.querySelector(".description-text");
      if (!descriptionText) return;

      const isInline =
        this.descriptionElement.classList.contains("inline");

      if (isInline) {
        // 同行模式：检测文字是否溢出
        const tagsElement = this.descriptionContainer.querySelector(".tags");
        let availableWidth = this.descriptionContainer.offsetWidth - 7;
        if (tagsElement) availableWidth -= tagsElement.offsetWidth;
        if (descriptionText.offsetWidth <= availableWidth) return;

        descriptionText.classList.add("is-expandable");
        this._descriptionToggleTarget = descriptionText;
        this.onToggleDescription = (event) => {
          event.stopPropagation();
          // 拖选描述文本松手会触发 click，此时不折叠
          if (this._hasTextSelection()) return;
          const expanded = !descriptionText.classList.contains("expanded");
          descriptionText.classList.toggle("expanded", expanded);
          this.descriptionContainer.classList.toggle("expanded", expanded);
        };
        descriptionText.addEventListener("click", this.onToggleDescription);
      } else {
        // 分行模式：检测内容是否超出显示区域
        if (
          this.descriptionContainer.scrollHeight <=
          this.descriptionContainer.clientHeight
        )
          return;

        this.descriptionContainer.classList.add("is-expandable");
        this.onToggleDescription = (event) => {
          event.stopPropagation();
          // 拖选描述文本松手会触发 click，此时不折叠
          if (this._hasTextSelection()) return;
          const expanding =
            !this.descriptionElement.classList.contains("expanded");
          if (!expanding) {
            this._descriptionScrollTop =
              this.descriptionContainer.scrollTop || 0;
          }
          this.descriptionElement.classList.toggle("expanded", expanding);
          if (expanding) {
            this.descriptionContainer.scrollTop =
              this._descriptionScrollTop || 0;
          }
        };
        this.descriptionContainer.addEventListener(
          "click",
          this.onToggleDescription,
        );
      }
    });
  }

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
    return (
      isInside(selection.anchorNode) || isInside(selection.focusNode)
    );
  }

  /**
   * 清除旧的描述 toggle 监听，重新检测截断并绑定点击展开。
   * 供内联编辑保存后调用。
   */
  _reinitDescriptionToggle() {
    // 移除旧 handler
    if (this.onToggleDescription) {
      const target = this._descriptionToggleTarget || this.descriptionContainer;
      target.removeEventListener("click", this.onToggleDescription);
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
  }

  // ==========================================
  // Action 按钮（分享、已读、归档、删除）
  // 采用乐观更新：先改 DOM，再发 API，失败回滚
  // ==========================================

  _initActionButtons() {
    const actionButtons = this.element.querySelectorAll("button[data-action]");
    actionButtons.forEach((button) => {
      if (!button.dataset.action) return;

      if (button.hasAttribute("ld-confirm-button")) {
        button._onConfirm = () => this._executeAction(button, button.dataset.action);
      } else {
        button.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this._executeAction(button, button.dataset.action);
        });
      }
    });
  }

  async _executeAction(button, action) {
    const isShare = action === "share" || action === "unshare";
    const isRead = action === "mark_as_read" || action === "mark_as_unread";

    handleBookmarkAction({
      bookmarkId: this.bookmarkId,
      action,
      onOptimistic: () => {
        if (isShare) this._toggleShareButton(button, action === "share");
        if (isRead) this._toggleReadButton(button, action === "mark_as_read");
      },
      onRollback: () => {
        if (isShare) this._toggleShareButton(button, action !== "share");
        if (isRead) this._toggleReadButton(button, action !== "mark_as_read");
      },
    });
  }

  // ---- 图标替换 ----

  _setIcon(button, href) {
    const oldUse = button.querySelector("svg.action-icon use");
    if (oldUse) {
      const newUse = document.createElementNS("http://www.w3.org/2000/svg", "use");
      newUse.setAttribute("href", href);
      oldUse.replaceWith(newUse);
    }
  }

  _toggleShareButton(button, isShared) {
    this._setIcon(button, isShared ? "#ld-icon-share" : "#ld-icon-share-x");
    button.name = isShared ? "unshare" : "share";
    button.dataset.action = isShared ? "unshare" : "share";
    button.title = isShared ? gettext("Shared") : gettext("Share");
  }

  _toggleReadButton(button, isRead) {
    this._setIcon(button, isRead ? "#ld-icon-read-check" : "#ld-icon-unread-x");
    button.dataset.action = isRead ? "mark_as_unread" : "mark_as_read";
    button.title = isRead ? gettext("Mark as unread") : gettext("Mark as read");
    this.element.classList.toggle("unread", !isRead);
  }
}
registerBehavior("ld-bookmark-item", BookmarkItem);

// Turbo 导航时清理全局缓存
document.addEventListener("turbo:before-render", () => {
  delete window.__ldQuickTagsSubmenu;
});

// ==========================================
// 展开折叠按钮
// ==========================================

// 模板 data-toggle-storage-key → ld:sidebar:collapse JSON 字段名
const COLLAPSE_KEY_MAP = {
  userSummarySectionState: "summary",
  userSummaryActivityState: "activity",
  tagSectionState: "tags",
  domainSectionState: "domains",
  bundleSectionState: "bundles",
};

const COLLAPSE_STORAGE_KEY = "ld:sidebar:collapse";

function _readCollapseMap() {
  try { return JSON.parse(localStorage.getItem(COLLAPSE_STORAGE_KEY) || "{}"); }
  catch { return {}; }
}

const BUNDLES_KEY = "ld:sidebar:bundles";
const DOMAINS_KEY = "ld:sidebar:domains";

class CollapseButtonBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.collapseField = COLLAPSE_KEY_MAP[element.dataset.toggleStorageKey] || null;
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
    try { state = JSON.parse(localStorage.getItem(BUNDLES_KEY) || "{}"); }
    catch {}
    state[bundleId] = expanded;
    localStorage.setItem(BUNDLES_KEY, JSON.stringify(state));
  }

  restoreBundleState() {
    let state = {};
    try { state = JSON.parse(localStorage.getItem(BUNDLES_KEY) || "{}"); }
    catch {}

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
    try { html = sessionStorage.getItem(cacheKey); } catch {}

    if (html === null) {
      // 2. Cache miss — fetch from server
      try {
        const resp = await fetch(`/domain-tree/children?${sp.toString()}`);
        if (!resp.ok) {
          this._loadChildrenFailed(item, button);
          return;
        }
        html = await resp.text();
        try { sessionStorage.setItem(cacheKey, html); } catch {}
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
      ul.className = isIconMode ? "domain-children domain-children-icon" : "domain-children";
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
    try { state = JSON.parse(localStorage.getItem(DOMAINS_KEY) || "{}"); }
    catch {}
    state[nodeId] = expanded;
    localStorage.setItem(DOMAINS_KEY, JSON.stringify(state));
  }

  restoreTreeState() {
    let state = {};
    try { state = JSON.parse(localStorage.getItem(DOMAINS_KEY) || "{}"); }
    catch {}

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
      if (node) { this.toggleNode(node); return; }
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

    const button = node.querySelector(":scope > .tag-tree-row .tag-tree-toggle");
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
    const button = node.querySelector(":scope > .tag-tree-row .tag-tree-toggle");

    // 1. Check sessionStorage cache first
    let html = null;
    try { html = sessionStorage.getItem(cacheKey); } catch {}

    if (html === null) {
      // 2. Cache miss — fetch from server
      try {
        const resp = await fetch(`/tag-tree/children?${sp.toString()}`);
        if (!resp.ok) return;
        html = await resp.text();
        try { sessionStorage.setItem(cacheKey, html); } catch {}
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
    try { state = JSON.parse(localStorage.getItem(TAG_TREE_KEY) || "{}"); }
    catch {}
    state[path] = expanded;
    localStorage.setItem(TAG_TREE_KEY, JSON.stringify(state));
  }

  restoreTreeState() {
    let state = {};
    try { state = JSON.parse(localStorage.getItem(TAG_TREE_KEY) || "{}"); }
    catch {}

    this.element.querySelectorAll(".tag-tree-node[data-has-children='true']").forEach((node) => {
      const path = tagTreePath(node);
      if (state[path]) {
        node.dataset.expanded = "true";
        const button = node.querySelector(":scope > .tag-tree-row .tag-tree-toggle");
        if (button) button.setAttribute("aria-expanded", "true");
        this.loadChildren(node, path);
      }
    });
  }
}
registerBehavior("ld-tag-tree", TagTreeBehavior);

// ==========================================
// 滚动位置记忆
// ==========================================

function restoreBookmarkListScrollPosition() {
  const scroller = document.scrollingElement;
  if (scroller && document.querySelector(".bookmark-list")) {
    let data = null;
    try { data = JSON.parse(localStorage.getItem("ld:bookmark-list:scroll")); }
    catch {}

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
      setTimeout(() => { sidebarRestoring = false; }, 50);
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
  if (page && page.classList.contains("highlights-page")) return "ld:sidebar-state:highlights";
  return "ld:sidebar-state:bookmarks";
}

let sidebarJustToggled = false;

function saveSidebarState(isOpen) {
  try { localStorage.setItem(getSidebarStateKey(), isOpen ? "1" : "0"); } catch {}
  // 同步到 Cookie，让服务端判断是否需要懒加载
  try {
    const page = getSidebarPage();
    const isHighlights = page && page.classList.contains("highlights-page");
    const cookieName = isHighlights ? "ld_sidebar_highlights" : "ld_sidebar_bookmarks";
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

  const modules = placeholder.dataset.sidebarLazyModules || "domains,tags,bundles,summary";
  const fingerprint = placeholder.dataset.domainConfigFingerprint || "";
  const cacheKey = `sidebar-content:${ctx}:${modules}` + (fingerprint ? `:${fingerprint}` : "");

  let html = null;
  try { html = sessionStorage.getItem(cacheKey); } catch {}

  if (html === null) {
    const sp = new URLSearchParams();
    sp.set("ctx", ctx);
    sp.set("modules", modules);
    try {
      const resp = await fetch(`/sidebar-content?${sp.toString()}`);
      if (!resp.ok) return;
      html = await resp.text();
      try { sessionStorage.setItem(cacheKey, html); } catch {}
    } catch (e) {
      console.error("Failed to load sidebar content:", e);
      return;
    }
  }

  if (html && html.trim()) {
    placeholder.outerHTML = html;
    document.querySelectorAll("[ld-domain-tree]").forEach(el => {
      el.behaviors?.forEach(b => b instanceof DomainTreeBehavior && b.restoreTreeState());
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
    if (e.type === "touchstart") { e.preventDefault(); }
    const wasOpen = page.classList.contains("sidebar-open");
    wasOpen ? closeSidebar(page) : openSidebar(page);
    saveSidebarState(!wasOpen);
    return;
  }

  // 关闭按钮（移动端）
  if (e.target.closest("[data-sidebar-close]")) {
    if (e.type === "touchstart") { e.preventDefault(); }
    closeSidebar(page);
    saveSidebarState(false);
    return;
  }

  // 遮罩点击（移动端 sidebar 外部区域）
  if (e.type === "click" && !sidebarJustToggled &&
      page.classList.contains("sidebar-visible") &&
      !e.target.closest(".sidebar") && isMobile()) {
    closeSidebar(page);
    saveSidebarState(false);
  }
}
document.addEventListener("click", handleSidebarInteraction);
document.addEventListener("touchstart", handleSidebarInteraction, { passive: false });

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
