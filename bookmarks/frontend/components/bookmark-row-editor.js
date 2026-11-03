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

// 移动端编辑态遮罩（单例）
let editorOverlay = null;

function isMobileDevice() {
  return window.matchMedia("(hover: none) and (pointer: coarse)").matches;
}

/** 遮罩上挖洞：--hole-* 记四边到编辑框的距离，四条遮条据此让开 */
function trackHole(overlay, getHole) {
  let pending = 0;
  let observer = null;

  const update = () => {
    pending = 0;
    const element = getHole();
    const rect = element?.getBoundingClientRect?.();
    const box = overlay.getBoundingClientRect();
    if (!rect || !box.width) return;

    // 元素可能晚一拍才渲染出来，拿到再观察它的尺寸变化（多行输入框会长高）
    if (!observer && typeof ResizeObserver === "function") {
      observer = new ResizeObserver(schedule);
      observer.observe(element);
    }
    const style = overlay.style;
    style.setProperty("--hole-top", `${Math.max(0, rect.top - box.top)}px`);
    style.setProperty("--hole-left", `${Math.max(0, rect.left - box.left)}px`);
    style.setProperty("--hole-right", `${Math.max(0, rect.right - box.left)}px`);
    style.setProperty("--hole-bottom", `${Math.max(0, rect.bottom - box.top)}px`);
    style.setProperty("--hole-height", `${rect.height}px`);
  };

  function schedule() {
    if (!pending) pending = requestAnimationFrame(update);
  }

  schedule();
  window.addEventListener("scroll", schedule, { capture: true, passive: true });
  window.addEventListener("resize", schedule, { passive: true });
  window.visualViewport?.addEventListener("resize", schedule);
  window.visualViewport?.addEventListener("scroll", schedule);

  return {
    refresh: schedule,
    stop() {
      if (pending) cancelAnimationFrame(pending);
      observer?.disconnect();
      window.removeEventListener("scroll", schedule, { capture: true });
      window.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("scroll", schedule);
    },
  };
}

/**
 * 打开编辑态遮罩：盖住页面，点遮罩任意位置退出编辑，点不到下面的任何东西。
 * hole 返回的节点会被挖出来保持可见可点——表格格内编辑器被 .table-body-scroll 的
 * 层叠上下文困在遮罩之下，抬不上去，只能反过来在遮罩上开洞。
 */
export function openEditorOverlay({ hole = null, onDismiss } = {}) {
  if (!isMobileDevice()) return;
  closeEditorOverlay();

  const overlay = document.createElement("div");
  overlay.className = "editor-overlay";

  // 拖动是滚页面（遮罩会跟着移位），只有原地按一下才算「点遮罩」
  let startX = 0;
  let startY = 0;
  let dragged = false;
  overlay.addEventListener(
    "touchstart",
    (event) => {
      const touch = event.touches[0];
      startX = touch.clientX;
      startY = touch.clientY;
      dragged = false;
    },
    { passive: true },
  );
  overlay.addEventListener(
    "touchmove",
    (event) => {
      const touch = event.touches[0];
      if (
        Math.abs(touch.clientX - startX) > 8 ||
        Math.abs(touch.clientY - startY) > 8
      ) {
        dragged = true;
      }
    },
    { passive: true },
  );
  overlay.addEventListener(
    "touchend",
    (event) => {
      if (dragged) return;
      // 吞掉这次触摸，免得收起编辑器后合成的 click 落到下面
      event.preventDefault();
      onDismiss?.();
    },
    { passive: false },
  );

  if (hole) {
    overlay.classList.add("is-spotlight");
    for (const side of ["top", "bottom", "left", "right"]) {
      const strip = document.createElement("div");
      strip.className = "editor-overlay-strip";
      strip.dataset.side = side;
      overlay.appendChild(strip);
    }
  }

  document.body.appendChild(overlay);
  editorOverlay = {
    element: overlay,
    hole: hole ? trackHole(overlay, hole) : null,
  };
}

export function refreshEditorOverlay() {
  editorOverlay?.hole?.refresh();
}

export function closeEditorOverlay() {
  if (!editorOverlay) return;
  editorOverlay.hole?.stop();
  editorOverlay.element.remove();
  editorOverlay = null;
}

let activeEditor = null;

export function activateEditor(bookmarkId, fieldType, closeFn, options = {}) {
  const withOverlay = options.overlay !== false;
  if (withOverlay) {
    openEditorOverlay({
      hole: options.hole,
      onDismiss: () => closeActiveEditor(),
    });
  } else {
    closeEditorOverlay();
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
          closeEditorOverlay();
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

// 只要活跃编辑器属于这条书签就返回 true，不区分字段
export function isEditorActiveFor(bookmarkId) {
  return activeEditor?.bookmarkId === bookmarkId;
}

export function closeEditor(editor, options = {}) {
  if (!editor) return;
  if (activeEditor === editor) {
    activeEditor = null;
    closeEditorOverlay();
  }
  editor.close(options);
}

export function closeActiveEditor(options = {}) {
  closeEditor(activeEditor, options);
}
