import { getCSRFToken } from "../utils/csrf.js";

/** 行内编辑的共享原语：PATCH、文本域自适应、移动端遮罩、编辑器单例互斥。
 *  行行为本体与各职责模块（fields / notes / tags）都从这里取，避免各自复制一份。 */

export function gettext(s) {
  return window.gettext ? window.gettext(s) : s;
}

export async function patchBookmark(bookmarkId, data) {
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

export function autoResizeTextarea(textarea) {
  textarea.style.height = "auto";
  textarea.style.overflowY = "hidden";

  const maxHeightPx =
    Number.parseFloat(getComputedStyle(textarea).maxHeight) || Infinity;
  const nextHeight = Math.min(textarea.scrollHeight, maxHeightPx);
  textarea.style.height = `${nextHeight}px`;
  textarea.style.overflowY =
    textarea.scrollHeight > maxHeightPx ? "auto" : "hidden";
}

export function focusEnd(element) {
  element.focus({ preventScroll: true });
  element.setSelectionRange(element.value.length, element.value.length);
}

export function scrollToBookmarkTop(element) {
  if (!window.matchMedia("(hover: none) and (pointer: coarse)").matches) return;
  const li = element.closest("li[ld-bookmark-item]");
  if (!li) return;
  const stickyHeader = document.querySelector(
    '.section-header[data-sticky-on="true"]',
  );
  const stickyHeight = stickyHeader ? stickyHeader.offsetHeight : 0;
  // 格子视图可能另有一层吸顶表头，贴在搜索栏下方，编辑态滚顶时也要一并让开
  let offset = stickyHeight;
  const rowHeader = document.querySelector(".table-header-sticky");
  if (rowHeader) offset += rowHeader.offsetHeight;
  const rect = li.getBoundingClientRect();
  const currentScroll =
    window.pageYOffset || document.scrollingElement.scrollTop;
  const targetTop = rect.top + currentScroll - offset;
  window.scrollTo({ top: targetTop, behavior: "instant" });
}

export function parseTags(value) {
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

export function tagsEqual(left, right) {
  const leftSorted = [...left].sort();
  const rightSorted = [...right].sort();
  return JSON.stringify(leftSorted) === JSON.stringify(rightSorted);
}

/**
 * 就地编辑器的统一接线：Escape 撤销、回车保存（多行编辑器用 Cmd/Ctrl+Enter）、
 * 失焦保存；值未变则不保存。DOM 增删与显示回写由调用方在 onClose/onSave 里做，
 * 列表行与表格格子的差异只有这些。
 */
export function wireInlineEditor(editor, options) {
  const {
    originalValue,
    multiline = false,
    onClose,
    onSave,
    onError,
    onInput,
  } = options;
  let closed = false;

  const close = (save) => {
    if (closed) return;
    closed = true;
    const value = editor.value.trim();
    const changed = save && value !== originalValue;
    onClose({ save, value, changed });
    if (changed && onSave) Promise.resolve(onSave(value)).catch(onError);
  };

  if (onInput) editor.addEventListener("input", () => onInput(editor));
  editor.addEventListener("blur", () => close(true));
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      editor.value = originalValue;
      close(false);
    } else if (
      event.key === "Enter" &&
      (!multiline || event.metaKey || event.ctrlKey)
    ) {
      event.preventDefault();
      close(true);
    }
  });

  return close;
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
  editorOverlay.addEventListener(
    "touchend",
    (e) => {
      e.preventDefault();
      closeActiveEditor();
    },
    { passive: false },
  );
  document.body.appendChild(editorOverlay);
}

function removeEditorOverlay() {
  if (editorOverlay) {
    editorOverlay.remove();
    editorOverlay = null;
  }
}

let activeEditor = null;

export function activateEditor(bookmarkId, fieldType, closeFn, options = {}) {
  const withOverlay = isMobileDevice() && options.overlay !== false;
  if (withOverlay) {
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
        if (withOverlay) {
          removeEditorOverlay();
        }
      }
      closeFn({ save: options.save !== false });
    },
  };
  activeEditor = editor;
  return editor;
}

export function isActiveEditor(bookmarkId, fieldType) {
  return (
    activeEditor?.bookmarkId === bookmarkId &&
    activeEditor?.fieldType === fieldType
  );
}

export function closeEditor(editor, options = {}) {
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

export function closeActiveEditor(options = {}) {
  closeEditor(activeEditor, options);
}
