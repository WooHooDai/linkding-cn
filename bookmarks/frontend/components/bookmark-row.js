import { Behavior, registerBehavior } from "./runtime.js";
import {
  activateEditor,
  closeActiveEditor,
  isActiveEditor,
  isEditorActiveFor,
} from "./bookmark-row-editor.js";
import { bookmarkRowFields } from "./bookmark-row-fields.js";
import { bookmarkRowNotes } from "./bookmark-row-notes.js";
import { bookmarkRowTags } from "./bookmark-row-tags.js";
import { bookmarkRowActions } from "./bookmark-row-actions.js";
import { bookmarkRowDblClick } from "./bookmark-row-dblclick.js";
import { toggleTaskCheckbox } from "./bookmark-notes-task.js";

/** 书签行的行为本体：只负责事件接线、编辑器单例互斥与销毁。
 *  各字段的编辑逻辑按职责放在 bookmark-row-{fields,notes,tags,actions}.js，
 *  以混入方式装到原型上，行为注册名保持不变。 */
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
    this.onNotesTaskClick = this.onNotesTaskClick.bind(this);

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

    // 笔记待办勾选框（默认/紧凑视图共用同一套笔记片段）
    element.addEventListener("click", this.onNotesTaskClick);

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
      this.quickTagMenuTrigger.addEventListener(
        "click",
        this.onQuickTagMenuTrigger,
      );
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

    // 初始化作者名自动滚动（标题行内固定宽度，超长时左右往复滚动）
    this._initAuthorScroll();

    // 初始化双击进入编辑（默认/紧凑视图；表格视图由表格行为接管）
    this._initDblClickEdit();

    // 初始化 action 按钮（分享、已读、归档、删除等）
    this._initActionButtons();
  }

  destroy() {
    this._teardownDblClickEdit();

    if (isEditorActiveFor(this.bookmarkId)) {
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
      this.quickTagMenuTrigger.removeEventListener(
        "click",
        this.onQuickTagMenuTrigger,
      );
    }
    this._closeQuickTagMenu();

    if (this.notesEditor) {
      this.notesEditor.removeEventListener("blur", this.onNotesBlur);
      this.notesEditor.removeEventListener("keydown", this.onNotesKeydown);
    }

    this.element.removeEventListener("click", this.onNotesTaskClick);

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
      const target = this._descriptionToggleTarget || this.descriptionContainer;
      target.removeEventListener("click", this.onToggleDescription);
      if (this.element) {
        this.element.removeEventListener(
          "mouseenter",
          this.onRefreshDescriptionHint,
        );
      }
    }
  }

  onToggleNotes(event) {
    event.preventDefault();
    event.stopPropagation();
    const next = !this.element.classList.contains("show-notes");
    this.element.dataset.notesEnabled = String(next);
    this.element.classList.toggle("show-notes", next);
  }

  /** 笔记里的待办勾选框：只切完成态，不连带触发行的其它行为（工具栏/展开） */
  onNotesTaskClick(event) {
    const box = event.target.closest(".inline-edit-notes .task-checkbox");
    if (!box) return;
    event.preventDefault();
    event.stopPropagation();
    toggleTaskCheckbox(box, {
      bookmarkId: this.bookmarkId,
      source: box
        .closest(".inline-edit-notes")
        ?.querySelector(".inline-edit-textarea"),
      // 下次编辑笔记的初值要与勾选后的源一致，否则保存会把勾选改回去
      onChange: (markdown) => {
        this.originalNotes = markdown;
      },
    });
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
      case "author":
        this._startEditAuthor();
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

  _activateQuickEditor(fieldType, closeFn, hole) {
    this._setQuickEditButtonActive(fieldType, true);

    // 格子行的编辑器抬不到遮罩之上，只认「开洞」这一种遮罩；给不出洞就不盖，
    // 免得编辑框被压在遮罩下
    const isCellRow = Boolean(this.element.querySelector(".cell"));

    return activateEditor(
      this.bookmarkId,
      fieldType,
      (options) => {
        try {
          closeFn(options);
        } finally {
          this._setQuickEditButtonActive(fieldType, false);
        }
      },
      { overlay: !isCellRow || Boolean(hole), hole: isCellRow ? hole : undefined },
    );
  }

  _setQuickEditButtonActive(fieldType, active) {
    const button = this.element.querySelector(
      `.quick-edit-btn[data-quick-edit="${fieldType}"]`,
    );
    if (!button) return;

    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
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
}

Object.assign(
  BookmarkItem.prototype,
  bookmarkRowFields,
  bookmarkRowNotes,
  bookmarkRowTags,
  bookmarkRowActions,
  bookmarkRowDblClick,
);

registerBehavior("ld-bookmark-item", BookmarkItem);

document.addEventListener("turbo:before-render", () => {
  delete window.__ldQuickTagsSubmenu;
});
