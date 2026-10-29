import {
  autoResizeTextarea,
  closeActiveEditor,
  focusEnd,
  gettext,
  isActiveEditor,
  patchBookmark,
  scrollToBookmarkTop,
} from "./bookmark-row-editor.js";

/** 笔记：展开、编辑、保存，保存后重新取渲染好的 markdown。 */
export const bookmarkRowNotes = {
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
  },

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
  },

  onNotesBlur() {
    if (isActiveEditor(this.bookmarkId, "notes")) {
      closeActiveEditor();
    }
  },

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
  },

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
      this.notesMarkdown.scrollTop =
        editorScrollTop || this._savedNotesScrollTop || 0;
    }
  },

  onNotesKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeActiveEditor({ save: false });
    }
  },
};
