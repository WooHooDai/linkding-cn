import { Highlighter, HIGHLIGHT_COLORS } from "./anchoring/highlighter";
import { TextQuoteAnchor } from "./anchoring/index";
import { READER_ICONS } from "./reader-icons";
import { gettext, ngettext, interpolate } from "../utils/i18n.js";
import "../components/confirm-inline.js";
import "../components/tag-autocomplete.js";
import "./reader-toolbar.js";
import "./reader-sidebar.js";
import { loadReaderSettings } from "./reader-settings.js";
import {
  DEFAULT_ITEM_FORMAT,
  DEFAULT_SEPARATOR,
  renderByAction,
} from "../utils/highlight-copy-format.js";
import {
  parseJsonScriptValue,
  normalizeBaseUrl,
  joinPath,
  patchAnnotation,
  deleteAnnotation,
  restoreAnnotationToAsset,
  fetchBookmarkData,
  fetchAssetList,
} from "./reader-api.js";
import {
  ReadingProgressController,
  getScrollMetrics,
} from "./reading-progress.js";

// --- Highlight copy format config cache ---
let _cachedItemFormat = null;
let _cachedSeparator = null;
let _cachedCopyAction = "both";
let _copyConfigFetched = false;

async function getCopyConfig(apiBase) {
  if (_copyConfigFetched) return {
    itemFormat: _cachedItemFormat || DEFAULT_ITEM_FORMAT,
    separator: _cachedSeparator || DEFAULT_SEPARATOR,
    action: _cachedCopyAction,
  };
  _copyConfigFetched = true;
  try {
    const resp = await fetch(`${apiBase}/user/profile/`);
    if (resp.ok) {
      const data = await resp.json();
      const fmt = data.highlight_copy_format || {};
      if (fmt.item_format) _cachedItemFormat = fmt.item_format;
      if (fmt.separator) _cachedSeparator = fmt.separator;
      if (data.highlight_copy_default_action) _cachedCopyAction = data.highlight_copy_default_action;
    }
  } catch { /* silent */ }
  return {
    itemFormat: _cachedItemFormat || DEFAULT_ITEM_FORMAT,
    separator: _cachedSeparator || DEFAULT_SEPARATOR,
    action: _cachedCopyAction,
  };
}

const HIGHLIGHT_COLOR_LABELS = {
  yellow: gettext("Yellow"),
  green: gettext("Green"),
  blue: gettext("Blue"),
  pink: gettext("Pink"),
  primary: gettext("Theme"),
};
/**
 * Reader mode renderer — fetches defuddle-cleaned content from server,
 * with toolbar, sidebar, and annotation support.
 */
function renderReader(options = {}) {
  const {
    bookmarkId,
    assetId,
    from,
  } = options;

  const apiBase = normalizeBaseUrl(
    parseJsonScriptValue("reader-api-base-url", "/api/"),
  );
  const assetsBase = parseJsonScriptValue("reader-assets-base-url", "/assets");
  const bookmarksIndexUrl = parseJsonScriptValue(
    "reader-bookmarks-index-url",
    "/bookmarks",
  );

  // Parse bookmark data injected by Django's json_script
  const bookmarkScript = document.getElementById("bookmark-data");
  let bookmarkData = {};
  if (bookmarkScript) {
    try {
      bookmarkData = JSON.parse(bookmarkScript.textContent);
    } catch (err) {
      console.warn("Failed to parse bookmark data:", err);
    }
  }

  // Title shown in toolbar should always come from bookmark metadata.
  let toolbarTitle = bookmarkData.title || "";
  if (!toolbarTitle) {
    try {
      toolbarTitle = new URL(bookmarkData.url || window.location.href)
        .hostname;
    } catch {
      toolbarTitle = gettext("Reader");
    }
  }
  document.title = toolbarTitle;

  // Fetch article content asynchronously, then render.
  const contentUrl = assetId > 0
    ? joinPath(assetsBase, `${assetId}`)
    : null;

  if (contentUrl) {
    fetch(contentUrl)
      .then((r) => {
        if (!r.ok) return Promise.reject(r.status);
        return r.text();
      })
      .then((html) => {
        // 从完整 HTML 文档中提取 body 内容和 head 元数据
        const bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
        const bodyHtml = bodyMatch ? bodyMatch[1] : html;
        const titleMatch = html.match(/<meta\s+name="title"\s+content="([^"]*)"/i);
        const wcMatch = html.match(/<meta\s+name="word-count"\s+content="(\d+)"/i);
        if (titleMatch) document.title = titleMatch[1];
        renderArticle(bodyHtml, {
          title: titleMatch ? titleMatch[1] : null,
          wordCount: wcMatch ? parseInt(wcMatch[1], 10) : 0,
        }, toolbarTitle, bookmarkData, apiBase, assetsBase, bookmarksIndexUrl, bookmarkId, assetId, from);
      })
      .catch((err) => {
        console.error("Failed to load article content:", err);
        const el = document.getElementById("loading-container");
        if (el) el.textContent = gettext("Failed to load article.");
      });
  }
}

function renderArticle(bodyHtml, meta, resolvedTitle, bookmarkData, apiBase, assetsBase, bookmarksIndexUrl, bookmarkId, assetId, fromParam) {
  // Remove loading spinner
  const loadingEl = document.getElementById("loading-container");
  if (loadingEl) loadingEl.remove();

  const container = document.createElement("div");
  container.classList.add("container");

  // 标题
  if (meta.title) {
    const articleTitle = document.createElement("h1");
    articleTitle.className = "article-title";
    articleTitle.textContent = meta.title;
    container.append(articleTitle);
  }

  // 字数和预计阅读时长
  if (meta.wordCount > 0) {
    const stats = document.createElement("p");
    stats.className = "article-stats";
    const hr = document.createElement("hr");
    container.append(stats);
    container.append(hr);

    function updateReadingStats() {
      const speed = Number(loadReaderSettings().readingSpeed) || 400;
      const fast = Math.ceil(meta.wordCount / (speed * 1.1));
      const slow = Math.ceil(meta.wordCount / (speed * 0.9));
      let minText;
      if (fast === slow) {
        minText = interpolate(
          ngettext("%(fast)s minute", "%(fast)s minutes", fast),
          { fast: fast.toLocaleString() },
        );
      } else {
        minText = interpolate(
          ngettext("%(fast)s~%(slow)s minute", "%(fast)s~%(slow)s minutes", slow),
          { fast: fast.toLocaleString(), slow: slow.toLocaleString() },
        );
      }
      stats.textContent =
        interpolate(gettext("%(wordCount)s words · %(min)s"), {
          wordCount: meta.wordCount.toLocaleString(),
          min: minText,
        });
    }
    updateReadingStats();
    document.addEventListener("reader-settings-changed", updateReadingStats);
  }

  const articleContent = document.createElement("div");
  articleContent.id = "article-content";
  articleContent.innerHTML = bodyHtml;
  postProcess(articleContent);
  container.append(articleContent);

  // --- Build the new layout ---
  const layout = document.createElement("div");
  layout.id = "reader-layout";

  const contentArea = document.createElement("div");
  contentArea.id = "reader-content";
  contentArea.classList.add("scrollbar");
  contentArea.appendChild(container);
  layout.appendChild(contentArea);

  document.body.appendChild(layout);

  // --- Snapshot URL (open latest snapshot page directly) ---
  const snapshotUrl = bookmarkData.snapshot_url || (
    bookmarkData.snapshot_id
      ? joinPath(assetsBase, `${bookmarkData.snapshot_id}`)
      : ""
  );

  // --- Toolbar ---
  const toolbar = document.createElement("reader-toolbar");
  toolbar.title = resolvedTitle;
  toolbar.bookmarkUrl = bookmarkData.url || "";
  toolbar.snapshotUrl = snapshotUrl;
  document.body.prepend(toolbar);

  // --- Sidebar ---
  const sidebar = document.createElement("reader-sidebar");
  sidebar.bookmarkData = bookmarkData;
  sidebar.apiBase = apiBase;
  sidebar.assetsBase = assetsBase;
  sidebar.bookmarksIndexUrl = bookmarksIndexUrl;
  // Prevent flex item min-width:auto from keeping the closed sidebar visible
  sidebar.style.minWidth = "0";
  layout.appendChild(sidebar);

  // Restore sidebar state from localStorage (default: closed)
  const savedSidebarRaw = localStorage.getItem("ld:reader:sidebar-open");
  const savedSidebarOpen = savedSidebarRaw === "true";
  sidebar.open = savedSidebarOpen;
  toolbar.sidebarOpen = savedSidebarOpen;

  // Toggle sidebar
  toolbar.addEventListener("toggle-sidebar", () => {
    const newState = !sidebar.open;
    sidebar.open = newState;
    toolbar.sidebarOpen = newState;
    localStorage.setItem("ld:reader:sidebar-open", String(newState));
  });

  // On mobile, tap outside sidebar closes it.
  const isMobileViewport = window.matchMedia("(max-width: 768px)");
  document.addEventListener("pointerdown", (e) => {
    if (!isMobileViewport.matches) return;
    if (!sidebar.open) return;
    const path = typeof e.composedPath === "function" ? e.composedPath() : [];
    const insideSidebar = path.includes(sidebar);
    const insideToolbar = path.includes(toolbar);
    // 确认弹窗等浮层也不应关闭侧边栏
    const insidePopup = e.target.closest(".reader-confirm-popup, .ld-confirm-popup");
    if (!insideSidebar && !insideToolbar && !insidePopup) {
      sidebar.open = false;
      toolbar.sidebarOpen = false;
      localStorage.setItem("ld:reader:sidebar-open", "false");
    }
  });

  // --- Editable mode ---
  const isEditable = bookmarkData.is_editable !== false;
  const isPreview = bookmarkData.is_preview === true;
  sidebar.isEditable = isEditable;
  toolbar.isEditable = isEditable;

  // --- Toolbar "add-bookmark" action ---
  toolbar.addEventListener("add-bookmark", () => {
    sidebar._addToMyBookmarks();
  });

  // --- Non-owner: toast on text selection ---
  if (!isEditable) {
    let selectionToast = null;

    function removeSelectionToast() {
      if (selectionToast) { selectionToast.remove(); selectionToast = null; }
    }

    document.addEventListener("selectionchange", () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !selection.toString().trim()) {
        return;
      }
      const range = selection.getRangeAt(0);
      if (!articleContent.contains(range.commonAncestorContainer)) return;
      if (selectionToast) return;

      selectionToast = document.createElement("div");
      selectionToast.className = "reader-resume-toast reader-resume-toast--resume";
      selectionToast.setAttribute("role", "status");
      selectionToast.innerHTML = `
        <span class="reader-resume-toast-text">${gettext("Add bookmark to highlight")}</span>
        <span class="reader-resume-toast-buttons">
          <button type="button" class="btn btn-sm btn-link selection-toast-cancel">${gettext("Cancel")}</button>
          <button type="button" class="btn btn-sm btn-primary selection-toast-add">${gettext("Add")}</button>
        </span>
      `;
      selectionToast.querySelector(".selection-toast-cancel").addEventListener("click", () => {
        removeSelectionToast();
        selection.removeAllRanges();
      });
      selectionToast.querySelector(".selection-toast-add").addEventListener("click", () => {
        removeSelectionToast();
        sidebar._addToMyBookmarks();
      });
      document.body.appendChild(selectionToast);

      // Dismiss on scroll or page click (outside toast)
      const dismissOnInteraction = (e) => {
        if (selectionToast && !selectionToast.contains(e.target)) {
          removeSelectionToast();
          document.removeEventListener("scroll", dismissOnInteraction, true);
          document.removeEventListener("pointerdown", dismissOnInteraction, true);
        }
      };
      document.addEventListener("scroll", dismissOnInteraction, true);
      document.addEventListener("pointerdown", dismissOnInteraction, true);
    });
  }

  // --- Fetch full bookmark data and assets ---
  if (bookmarkData.id) {
    fetchBookmarkData(bookmarkData.id, sidebar, apiBase);
    if (isEditable) {
      fetchAssetList(bookmarkData.id, sidebar, apiBase);
    }
  }

  // --- Highlighter (owner only) ---
  let highlighter = null;
  if (isEditable && !isPreview && bookmarkId && Number(assetId) > 0) {
    highlighter = initHighlighter(articleContent, bookmarkId, assetId, sidebar, apiBase);
  }

  // --- Scroll progress (owner only) ---
  setupScrollProgress(contentArea, toolbar);
  if (isEditable && !isPreview) {
    // Check for pending scroll from "Add to my bookmarks" flow
    try {
      const pending = JSON.parse(localStorage.getItem("ld:reader:pending-scroll") || "null");
      if (pending && pending.bookmarkId === bookmarkId && pending.scrollTop > 0) {
        localStorage.removeItem("ld:reader:pending-scroll");
        requestAnimationFrame(() => {
          contentArea.scrollTop = pending.scrollTop;
        });
      }
    } catch {}
    if (fromParam === "highlights") {
      // From highlights page: skip progress saving, scroll to annotation
      _initHighlightsJumpMode(contentArea, articleContent, bookmarkId, assetId, apiBase, highlighter);
    } else {
      new ReadingProgressController(
        contentArea,
        articleContent,
        bookmarkId,
        assetId,
        apiBase,
      );
    }
  }
}

/**
 * From highlights page: pause progress sync, scroll to annotation, show banner.
 */
function _initHighlightsJumpMode(contentArea, articleContent, bookmarkId, assetId, apiBase, highlighter) {
  // Show pause banner
  const banner = document.createElement("div");
  banner.className = "reader-progress-paused-banner";
  banner.innerHTML = `
    <span class="reader-progress-paused-text"></span>
    <button type="button" class="btn btn-sm reader-progress-resume-btn"></button>
  `;
  banner.querySelector(".reader-progress-paused-text").textContent =
    gettext("Reading progress sync is paused");
  banner.querySelector(".reader-progress-resume-btn").textContent =
    gettext("Resume sync");

  banner.querySelector(".reader-progress-resume-btn").addEventListener("click", () => {
    banner.remove();
    new ReadingProgressController(contentArea, articleContent, bookmarkId, assetId, apiBase);
  });

  document.body.appendChild(banner);

  // Scroll to annotation hash after annotations load
  const hash = location.hash;
  if (!hash || !hash.startsWith("#annotation-")) return;
  const targetAnnId = hash.replace("#annotation-", "");

  function jumpToAnnotation() {
    if (!highlighter) return;
    const ann = highlighter.annotations.get(targetAnnId);
    if (!ann) return;
    try {
      const range = highlighter.resolveAnnotationRange(ann);
      scrollRangeIntoReaderView(contentArea, range);
      // Wait for scroll to finish by watching scrollTop, then flash
      const container = getReaderScrollContainer(contentArea) || contentArea;
      let last = container.scrollTop;
      const poll = setInterval(() => {
        const cur = container.scrollTop;
        if (Math.abs(cur - last) < 1) {
            clearInterval(poll);
            flashAnnotationRange(range);
        }
        last = cur;
      }, 100);
    } catch {}
  }

  if (highlighter) {
    // Poll until the target annotation is loaded, then jump
    let elapsed = 0;
    const poll = setInterval(() => {
      elapsed += 200;
      if (highlighter.annotations.has(targetAnnId)) {
        clearInterval(poll);
        requestAnimationFrame(() => jumpToAnnotation());
      } else if (elapsed >= 5000) {
        clearInterval(poll);
        // Last resort: try resolving anyway
        jumpToAnnotation();
      }
    }, 200);
  }
}

function postProcess(articleContent) {
  articleContent.querySelectorAll("table").forEach((table) => {
    table.classList.add("table");
    // Wrap wide tables in a horizontally scrollable container
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll-wrapper";
    table.parentNode.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  });
  articleContent.querySelectorAll('figure[aria-label="ld-carousel"]').forEach((figure) => {
    figure.classList.add("ld-carousel");
    // The snapshot pins a fixed inline height on the figure so the saved page
    // matches the source layout. In the reader the media is sized by content
    // (auto height, capped at 80vh), so the pinned height must go — otherwise
    // taller images are clipped to a horizontal band (overflow-y hidden +
    // align-items center = vertical center crop).
    figure.style.height = "auto";
    figure.style.maxHeight = "";
    figure.style.minHeight = "";
    figure.querySelectorAll("img, video, iframe").forEach((media) => {
      media.classList.add("ld-carousel-item");
      const width = parseFloat(media.getAttribute("width"));
      const height = parseFloat(media.getAttribute("height"));
      media.style.flex = "0 0 auto";
      media.style.maxWidth = "none";
      if (media.tagName === "IFRAME") {
        if (width) media.style.width = `${width}px`;
        if (height) media.style.height = `${height}px`;
      } else {
        media.style.width = "auto";
        media.style.height = "auto";
        media.style.maxHeight = "80vh";
        media.style.objectFit = "contain";
      }
    });
  });
}

function setupScrollProgress(contentEl, toolbar) {
  let ticking = false;
  const update = () => {
    toolbar.progress = Math.round(getScrollMetrics(contentEl).progress * 100);
  };

  update();
  contentEl.addEventListener("scroll", () => {
    if (!ticking) {
      window.requestAnimationFrame(() => {
        update();
        ticking = false;
      });
      ticking = true;
    }
  });
}

const ANNOTATION_TOOLBAR_MODE_HYBRID = "hybrid";
const ANNOTATION_TOOLBAR_MODE_TAKEOVER = "takeover";
const DEFAULT_HIGHLIGHT_COLOR = "yellow";

function getAnnotationToolbarMode() {
  const mode = loadReaderSettings().annotationToolbarMode;
  return mode === ANNOTATION_TOOLBAR_MODE_TAKEOVER ? mode : ANNOTATION_TOOLBAR_MODE_HYBRID;
}

function setAnnotationToolbarModeDataset(mode) {
  document.documentElement.dataset.annotationToolbarMode = mode;
}

function toSolidColor(bg) {
  if (typeof bg !== "string") return bg;
  if (bg.includes("color-mix(")) return bg;
  return bg.replace("0.35", "0.8").replace("0.3", "0.8");
}

function getReaderScrollContainer(contentEl) {
  return (
    contentEl.closest("#reader-content") ||
    document.getElementById("reader-content") ||
    null
  );
}

function getRangeStartRect(range) {
  try {
    const startRange = range.cloneRange();
    startRange.collapse(true);
    const caretRects = startRange.getClientRects();
    if (caretRects.length > 0) return caretRects[0];
  } catch {
    // Ignore and fall back.
  }
  try {
    const rects = range.getClientRects();
    if (rects.length > 0) return rects[0];
    const box = range.getBoundingClientRect();
    if (box && (box.width > 0 || box.height > 0)) return box;
  } catch {
    // Ignore and let caller fallback.
  }
  return null;
}

function scrollRangeIntoReaderView(contentEl, range) {
  const container = getReaderScrollContainer(contentEl);
  const targetRect = getRangeStartRect(range);

  if (container && targetRect) {
    const containerRect = container.getBoundingClientRect();
    const offsetInContainer =
      container.scrollTop + (targetRect.top - containerRect.top);
    // Keep target around upper-middle area for better reading continuity.
    const desiredTop = offsetInContainer - container.clientHeight * 0.32;
    const maxTop = Math.max(
      0,
      container.scrollHeight - container.clientHeight,
    );
    const nextTop = Math.max(0, Math.min(maxTop, desiredTop));
    container.scrollTo({ top: nextTop, behavior: "smooth" });
    return;
  }

  // Fallback for unexpected DOM states.
  const node = range.startContainer;
  const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
  if (el) {
    el.scrollIntoView({
      behavior: "smooth",
      block: "center",
      inline: "nearest",
    });
  }
}

/**
 * Wait until scroll position stabilizes, then call callback.
 */
function _waitForScrollStable(el, callback) {
  let last = -1, stable = 0;
  const check = setInterval(() => {
    const cur = el.scrollTop;
    if (Math.abs(cur - last) < 1) {
      if (++stable >= 3) { clearInterval(check); callback(); }
    } else {
      stable = 0;
    }
    last = cur;
  }, 80);
  // Safety: never wait more than 2s
  setTimeout(() => { clearInterval(check); callback(); }, 2000);
}

/**
 * Flash underline bars under each line of the annotation range.
 * Uses getClientRects() for per-line rects.
 */
function flashAnnotationRange(range) {
  // Try per-line rects first
  const rects = Array.from(range.getClientRects()).filter(r => r.width > 0 && r.height > 0);
  if (rects.length > 0) {
    const seen = new Set();
    for (const r of rects) {
      const key = Math.round(r.bottom);
      if (seen.has(key)) continue;
      seen.add(key);
      _createFlashBar(r.left, r.bottom, r.width);
    }
    return;
  }
  // Fallback: single bounding box
  const box = range.getBoundingClientRect();
  if (box.width > 0 && box.height > 0) {
    _createFlashBar(box.left, box.bottom, box.width);
  }
}

function _createFlashBar(left, bottom, width) {
  const color = "rgba(255,235,0,0.85)";
  const bar = document.createElement("div");
  bar.setAttribute("style", [
    "position:fixed",
    "z-index:99999",
    "pointer-events:none",
    "border-radius:1px",
    "left:" + left + "px",
    "top:" + (bottom + 1) + "px",
    "width:" + width + "px",
    "height:2px",
    "background:" + color,
    "animation:reader-annotation-flash 0.4s ease 2",
  ].join(";"));
  document.body.appendChild(bar);
  bar.addEventListener("animationend", () => bar.remove());
  setTimeout(() => { if (bar.parentNode) bar.remove(); }, 1200);
}

/**
 * Initialize the annotation highlighter with a unified popup.
 */
function initHighlighter(contentEl, bookmarkId, assetId, sidebar, apiBase) {
  const highlighter = new Highlighter(contentEl, {
    apiBase,
    bookmarkId,
    assetId,
  });

  // Sync annotations to sidebar
  highlighter.onChange((annotations) => {
    const unresolved = (sidebar.annotations || []).filter(
      (ann) => ann._unresolved,
    );
    sidebar.annotations = [...Array.from(annotations.values()), ...unresolved];
  });

  // Reload asset list when assets change
  sidebar.addEventListener("reload-assets", (e) => {
    const { bookmarkId: bmId } = e.detail;
    if (bmId) fetchAssetList(bmId, sidebar, apiBase);
  });

  // Handle sidebar annotation actions
  sidebar.addEventListener("annotation-action", async (e) => {
    const { id, action, note } = e.detail;

    if (action === "jump") {
      const ann = highlighter.annotations.get(String(id));
      if (!ann) return;
      try {
        const range = highlighter.resolveAnnotationRange(ann);
        // Scroll to exact range position instead of parent element center.
        scrollRangeIntoReaderView(contentEl, range);
      } catch {
        console.warn(`Could not locate annotation ${id}`);
      }
    } else if (action === "copy") {
      const ann =
        highlighter.annotations.get(String(id)) ||
        (sidebar.annotations || []).find(
          (item) => String(item.id) === String(id),
        );
      if (ann) {
        const config = await getCopyConfig(apiBase);
        const text = renderByAction(config.itemFormat, ann.selected_text, ann.note_content || "", config.action);
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
        }
      }
    } else if (action === "edit-note") {
      const unresolvedAnn = (sidebar.annotations || []).find(
        (item) => String(item.id) === String(id) && item._unresolved,
      );
      if (unresolvedAnn) {
        try {
          const updated = await patchAnnotation(apiBase, id, {
            note_content: note,
          });
          sidebar.annotations = (sidebar.annotations || []).map((item) =>
            String(item.id) === String(id)
              ? { ...updated, _unresolved: true }
              : item,
          );
        } catch (err) {
          console.error("Failed to update annotation:", err);
        }
      } else {
        await highlighter.updateAnnotation(String(id), { note_content: note });
      }
    } else if (action === "delete") {
      const unresolvedAnn = (sidebar.annotations || []).find(
        (item) => String(item.id) === String(id) && item._unresolved,
      );
      if (unresolvedAnn) {
        try {
          await deleteAnnotation(apiBase, id);
          sidebar.annotations = (sidebar.annotations || []).filter(
            (item) => String(item.id) !== String(id),
          );
          highlighter.annotations.delete(String(id));
        } catch (err) {
          console.error("Failed to delete annotation:", err);
        }
      } else {
        await highlighter.deleteAnnotation(String(id));
      }
    } else if (action === "change-color") {
      const unresolvedAnn = (sidebar.annotations || []).find(
        (item) => String(item.id) === String(id) && item._unresolved,
      );
      if (unresolvedAnn) {
        try {
          const updated = await patchAnnotation(apiBase, id, {
            color: e.detail.color,
          });
          sidebar.annotations = (sidebar.annotations || []).map((item) =>
            String(item.id) === String(id)
              ? { ...updated, _unresolved: true }
              : item,
          );
        } catch (err) {
          console.error("Failed to update annotation:", err);
        }
      } else {
        await highlighter.updateAnnotation(String(id), {
          color: e.detail.color,
        });
      }
    }
  });

  const toolbarMode = getAnnotationToolbarMode();
  setAnnotationToolbarModeDataset(toolbarMode);
  document.documentElement.classList.toggle(
    "reader-annotation-takeover",
    toolbarMode === ANNOTATION_TOOLBAR_MODE_TAKEOVER,
  );

  const hasCoarsePrimaryPointer =
    window.matchMedia("(pointer: coarse)").matches;
  const hasAnyCoarsePointer = window.matchMedia(
    "(any-pointer: coarse)",
  ).matches;
  const isTouchCapable = (navigator.maxTouchPoints || 0) > 0;
  const preferMobileToolbar =
    hasCoarsePrimaryPointer || hasAnyCoarsePointer || isTouchCapable;

  const popup = createHighlightPopup({
    compact: !preferMobileToolbar,
  });
  document.body.appendChild(popup);

  if (
    toolbarMode === ANNOTATION_TOOLBAR_MODE_TAKEOVER &&
    window.matchMedia("(pointer: coarse)").matches
  ) {
    contentEl.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  const state = {
    pendingRange: null,
    pendingAnnotation: null,
    anchorRect: null,
    anchorClientX: null,
    mouseSelecting: false,
    mouseSelectStartX: 0,
    mouseSelectStartY: 0,
    mouseSelectMoved: false,
    lastSelectionPointerX: null,
    lastSelectionPointerTs: -1,
    isOpeningFromHighlightClick: false,
    suppressSelectionChangeUntil: 0,
    lastHighlightPointerUpTs: -1,
    contextToken: 0,
    isOpen: false,
    openedAt: 0,
    isPersisting: false,
    isAnchorInViewport: true,
    lastScrollAt: 0,
    isRestoringSelection: false,
    popupLayout: "anchored",
    dockedEditingSession: false,
    lastToolbarControlPointerDownTs: -1,
    lastToolbarInteractionTs: -1,
    pendingColor: null,
    saveStatus: "idle",
    saveStatusClearTimer: null,
    mode: "idle",
  };

  function setMode(mode) {
    state.mode = mode;
    popup.dataset.state = mode;
  }

  function setPopupLayout(layout) {
    const next =
      preferMobileToolbar && layout === "docked-top"
        ? "docked-top"
        : "anchored";
    state.popupLayout = next;
    popup.dataset.layout = next;
  }

  function isDockedTopLayout() {
    return preferMobileToolbar && state.popupLayout === "docked-top";
  }

  function setPopupMode(isEdit) {
    popup.dataset.mode = isEdit ? "edit" : "new";
    popup._deleteBtn.hidden = !isEdit;
    popup._actionGroup.hidden = !isEdit;
    popup._actionCard.hidden = !isEdit;
    popup._deleteConfirm.hidden = true;
  }

  function setNoteOpen(isOpen) {
    popup.dataset.noteOpen = isOpen ? "true" : "false";
  }

  function updateDraftHighlight() {
    if (!state.pendingRange || state.pendingAnnotation) {
      highlighter.clearDraftHighlight?.();
      return;
    }
    const draftColor = state.pendingColor || DEFAULT_HIGHLIGHT_COLOR;
    highlighter.setDraftHighlight?.(state.pendingRange, draftColor);
  }

  function setSaveStatus(status, text = "") {
    state.saveStatus = status;
    popup.dataset.saveState = status;
    if (popup._saveStatus) {
      popup._saveStatus.textContent = text;
    }
    if (state.saveStatusClearTimer) {
      clearTimeout(state.saveStatusClearTimer);
      state.saveStatusClearTimer = null;
    }
    if (status === "saved") {
      state.saveStatusClearTimer = setTimeout(() => {
        setSaveStatus("idle", "");
      }, 1300);
    }
  }

  function updateMobileToolbarOffset() {
    // Mobile toolbar now follows selection like desktop positioning.
    popup.style.removeProperty("--ld-annotation-mobile-top-offset");
  }

  function suppressSelectionChangeFor(ms = 180) {
    state.suppressSelectionChangeUntil = performance.now() + ms;
  }

  function markToolbarInteraction() {
    state.lastToolbarInteractionTs = performance.now();
  }

  function getNoteText() {
    return popup._noteInput.value.trim();
  }

  async function resizeNoteInput() {
    const input = popup._noteInput;
    if (!input) return;

    const styles = window.getComputedStyle(input);
    const lineHeight = await measureLineHeightPx(input);
    const paddingTop = parseFloat(styles.paddingTop) || 0;
    const paddingBottom = parseFloat(styles.paddingBottom) || 0;
    const borderTop = parseFloat(styles.borderTopWidth) || 0;
    const borderBottom = parseFloat(styles.borderBottomWidth) || 0;
    const verticalFrame = paddingTop + paddingBottom + borderTop + borderBottom;
    const minHeight = lineHeight + verticalFrame;
    const viewportHeight = Math.max(
      0,
      window.visualViewport?.height || window.innerHeight,
    );
    const popupRect = popup.getBoundingClientRect();
    const toolbarRow = popup.querySelector(".ld-annotation-toolbar-row");
    const toolbarRowHeight = toolbarRow
      ? toolbarRow.getBoundingClientRect().height
      : 0;
    const noteAreaStyles = popup._noteArea
      ? window.getComputedStyle(popup._noteArea)
      : null;
    const noteAreaMarginTop = noteAreaStyles
      ? parseFloat(noteAreaStyles.marginTop) || 0
      : 0;
    const safeGap = 12;
    const availablePopupHeight = Math.max(
      minHeight,
      viewportHeight - popupRect.top - safeGap,
    );
    const availableForNote = Math.max(
      minHeight,
      availablePopupHeight - toolbarRowHeight - noteAreaMarginTop,
    );
    const maxLinesByViewport = Math.max(
      1,
      Math.floor((viewportHeight * 0.45 - verticalFrame) / lineHeight),
    );
    const maxLinesBySpace = Math.max(
      1,
      Math.floor((availableForNote - verticalFrame) / lineHeight),
    );
    const maxLines = Math.min(10, maxLinesByViewport, maxLinesBySpace);
    const maxHeight = lineHeight * maxLines + verticalFrame;

    input.style.height = "auto";
    const contentHeight = Math.max(minHeight, input.scrollHeight);
    const nextHeight = Math.min(maxHeight, contentHeight);
    input.style.height = `${nextHeight}px`;
    input.style.overflowY = contentHeight > maxHeight + 0.5 ? "auto" : "hidden";
  }

  function clearPending({ keepSelection = false } = {}) {
    state.pendingRange = null;
    state.pendingAnnotation = null;
    state.anchorRect = null;
    state.anchorClientX = null;
    state.pendingColor = null;
    highlighter.clearDraftHighlight?.();
    if (!keepSelection) {
      window.getSelection()?.removeAllRanges();
    }
  }

  function openPopup() {
    if (state.isOpen) return;
    state.isOpen = true;
    state.openedAt = performance.now();
    state.isAnchorInViewport = true;
    setSaveStatus("idle", "");
    updateMobileToolbarOffset();
    popup.hidden = false;
    popup.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => {
      popup.dataset.open = "true";
    });
  }

  function closePopup() {
    if (!state.isOpen) return;
    state.isOpen = false;
    popup.dataset.open = "false";
    popup.setAttribute("aria-hidden", "true");
    popup.hidden = true;
    setMode("idle");
    setPopupLayout("anchored");
    state.dockedEditingSession = false;
    setSaveStatus("idle", "");
    popup.dataset.anchorVisible = "true";
    popup._deleteConfirm.hidden = true;
  }

  function isPopupTarget(target) {
    return popup.contains(target);
  }

  function getCurrentSelectionRange() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount)
      return null;
    const range = selection.getRangeAt(0);
    if (!contentEl.contains(range.commonAncestorContainer)) return null;
    if (!selection.toString().trim().length) return null;
    return range;
  }

  function isSameRange(a, b) {
    if (!a || !b) return false;
    return (
      a.startContainer === b.startContainer &&
      a.startOffset === b.startOffset &&
      a.endContainer === b.endContainer &&
      a.endOffset === b.endOffset
    );
  }

  function restorePendingSelectionIfNeeded() {
    if (!preferMobileToolbar) return false;
    if (!state.pendingRange || state.pendingAnnotation) return false;
    if (!state.isOpen || popup.hidden) return false;
    if (performance.now() - state.lastScrollAt > 260) return false;

    try {
      if (!contentEl.contains(state.pendingRange.commonAncestorContainer)) {
        return false;
      }
      const selection = window.getSelection();
      if (!selection) return false;
      if (
        selection.rangeCount &&
        isSameRange(selection.getRangeAt(0), state.pendingRange)
      ) {
        return true;
      }
      const restored = state.pendingRange.cloneRange();
      state.isRestoringSelection = true;
      suppressSelectionChangeFor(80);
      selection.removeAllRanges();
      selection.addRange(restored);
      requestAnimationFrame(() => {
        state.isRestoringSelection = false;
      });
      return true;
    } catch {
      state.isRestoringSelection = false;
      return false;
    }
  }

  function getLastSelectionRect(range) {
    const rects = range.getClientRects();
    return rects.length > 0
      ? rects[rects.length - 1]
      : range.getBoundingClientRect();
  }

  function getSelectionEndRect(range) {
    try {
      const endRange = range.cloneRange();
      endRange.collapse(false);
      const rects = endRange.getClientRects();
      if (rects.length > 0) return rects[0];
      return endRange.getBoundingClientRect();
    } catch {
      return getLastSelectionRect(range);
    }
  }

  function updateAnchorRectFromPendingRange() {
    if (!state.pendingRange) return;
    try {
      state.anchorRect = getSelectionEndRect(state.pendingRange);
    } catch {
      state.anchorRect = null;
    }
  }

  function updateAnchorRectFromAnnotation(ann) {
    let rect = null;
    const annEl = document.querySelector(`.ld-highlight[data-annotation-id="${ann.id}"]`);
    if (annEl) {
      rect = annEl.getBoundingClientRect();
    } else {
      rect = getAnnotationRect(ann, contentEl);
    }
    state.anchorRect = rect;
  }

  function isRectInViewport(rect) {
    if (!rect) return false;
    return (
      rect.bottom >= 0 &&
      rect.top <= window.innerHeight &&
      rect.right >= 0 &&
      rect.left <= window.innerWidth
    );
  }

  function schedulePopupReflow() {
    const token = state.contextToken;
    requestAnimationFrame(() => {
      if (!state.isOpen || token !== state.contextToken) return;
      reflowPopupPosition();
    });
  }

  function showToolbarForSelection(range, anchorClientX = null) {
    state.contextToken += 1;
    state.pendingRange = range.cloneRange();
    state.pendingAnnotation = null;
    state.anchorRect = getSelectionEndRect(range);
    state.anchorClientX = Number.isFinite(anchorClientX) ? anchorClientX : null;
    state.pendingColor = null;
    state.dockedEditingSession = false;
    setPopupLayout("anchored");
    setPopupMode(false);
    popup._noteInput.value = "";
    setNoteOpen(true);
    void resizeNoteInput();
    updateColorButtons(popup, null);
    openPopup();
    positionPopup(popup, state.anchorRect, state.anchorClientX);
    popup.dataset.anchorVisible = "true";
    popup._deleteConfirm.hidden = true;
    updateDraftHighlight();
    setMode("toolbar_open_new");
    schedulePopupReflow();
  }

  function showToolbarForAnnotation(ann, anchorClientX = null) {
    state.contextToken += 1;
    state.pendingRange = null;
    state.pendingAnnotation = ann;
    state.anchorClientX = Number.isFinite(anchorClientX) ? anchorClientX : null;
    state.pendingColor = ann.color || null;
    highlighter.clearDraftHighlight?.();
    updateAnchorRectFromAnnotation(ann);
    state.dockedEditingSession = false;
    setPopupLayout("anchored");
    setPopupMode(true);
    popup._noteInput.value = ann.note_content || "";
    setNoteOpen(true);
    void resizeNoteInput();
    updateColorButtons(popup, ann.color);
    if (state.anchorRect) {
      openPopup();
      positionPopup(popup, state.anchorRect, state.anchorClientX);
      popup.dataset.anchorVisible = "true";
      popup._deleteConfirm.hidden = true;
      setMode("toolbar_open_edit");
      schedulePopupReflow();
    }
  }

  async function savePendingAnnotation() {
    if (state.isPersisting) return false;

    const noteText = getNoteText();
    if (state.pendingAnnotation) {
      const existingNote = state.pendingAnnotation.note_content || "";
      if (noteText !== existingNote) {
        state.isPersisting = true;
        setMode("persisting");
        setSaveStatus("saving", gettext("Saving..."));
        try {
          const updated = await highlighter.updateAnnotation(
            String(state.pendingAnnotation.id),
            {
              note_content: noteText,
            },
          );
          if (updated) {
            setSaveStatus("saved", gettext("Saved"));
          } else {
            setSaveStatus("error", gettext("Save failed"));
          }
        } catch {
          setSaveStatus("error", gettext("Save failed"));
        } finally {
          state.isPersisting = false;
        }
      }
      return true;
    }

    if (state.pendingRange && noteText) {
      state.isPersisting = true;
      setMode("persisting");
      setSaveStatus("saving", gettext("Saving..."));
      try {
        window.getSelection()?.removeAllRanges();
        const colorToSave = state.pendingColor || DEFAULT_HIGHLIGHT_COLOR;
        const created = await highlighter.createAnnotation(
          state.pendingRange,
          colorToSave,
          noteText,
        );
        if (created) {
          state.pendingRange = null;
          state.pendingAnnotation = created;
          state.pendingColor = created.color || colorToSave;
          highlighter.clearDraftHighlight?.();
          setPopupMode(true);
          updateColorButtons(popup, state.pendingColor);
          updateAnchorRectFromAnnotation(created);
          setSaveStatus("saved", gettext("Saved"));
          if (!isDockedTopLayout()) {
            schedulePopupReflow();
          }
        } else {
          setSaveStatus("error", gettext("Save failed"));
        }
      } catch {
        setSaveStatus("error", gettext("Save failed"));
      } finally {
        state.isPersisting = false;
      }
      return true;
    }

    return false;
  }

  async function closeAndPersist({
    clearSelection = true,
    contextToken = state.contextToken,
  } = {}) {
    await savePendingAnnotation();
    if (contextToken !== state.contextToken) return;
    closePopup();
    clearPending({ keepSelection: !clearSelection });
  }

  async function applyColor(color) {
    const noteText = getNoteText();
    const selectedColor =
      color || state.pendingColor || DEFAULT_HIGHLIGHT_COLOR;
    let createdAnnotation = null;
    if (state.pendingAnnotation) {
      const updates = { color: selectedColor };
      if (noteText !== (state.pendingAnnotation.note_content || "")) {
        updates.note_content = noteText;
      }
      state.isPersisting = true;
      setMode("persisting");
      try {
        await highlighter.updateAnnotation(
          String(state.pendingAnnotation.id),
          updates,
        );
      } finally {
        state.isPersisting = false;
      }
    } else if (state.pendingRange) {
      state.isPersisting = true;
      setMode("persisting");
      try {
        window.getSelection()?.removeAllRanges();
        createdAnnotation = await highlighter.createAnnotation(
          state.pendingRange,
          selectedColor,
          noteText,
        );
      } finally {
        state.isPersisting = false;
      }
    }
    if (createdAnnotation) {
      state.pendingRange = null;
      state.pendingAnnotation = createdAnnotation;
      state.pendingColor = createdAnnotation.color || selectedColor;
      highlighter.clearDraftHighlight?.();
      setPopupMode(true);
    }

    if (state.pendingAnnotation) {
      const latest = highlighter.annotations.get(
        String(state.pendingAnnotation.id),
      );
      if (latest) {
        state.pendingAnnotation = latest;
      }
      updateColorButtons(popup, selectedColor);
      if (state.pendingAnnotation) {
        updateAnchorRectFromAnnotation(state.pendingAnnotation);
        if (isDockedTopLayout()) {
          popup.dataset.anchorVisible = "true";
        } else if (state.anchorRect && isRectInViewport(state.anchorRect)) {
          popup.dataset.anchorVisible = "true";
          positionPopup(popup, state.anchorRect, state.anchorClientX);
        } else {
          popup.dataset.anchorVisible = "false";
        }
      }
      setMode("toolbar_open_edit");
      schedulePopupReflow();
      return;
    }
    state.pendingColor = selectedColor;
    updateColorButtons(popup, selectedColor);
    updateDraftHighlight();
    closePopup();
    clearPending();
  }

  async function handleDelete() {
    if (!state.pendingAnnotation) return;
    state.isPersisting = true;
    setMode("persisting");
    try {
      await highlighter.deleteAnnotation(String(state.pendingAnnotation.id));
    } finally {
      state.isPersisting = false;
    }
    closePopup();
    clearPending();
  }

  function reflowPopupPosition() {
    if (!state.isOpen || popup.hidden) return;
    updateMobileToolbarOffset();
    void resizeNoteInput();
    if (state.pendingRange) {
      updateAnchorRectFromPendingRange();
    } else if (state.pendingAnnotation) {
      updateAnchorRectFromAnnotation(state.pendingAnnotation);
    }
    updateDraftHighlight();
    if (isDockedTopLayout()) {
      popup.dataset.anchorVisible = "true";
      return;
    }

    if (!state.anchorRect) return;
    const visible = isRectInViewport(state.anchorRect);
    state.isAnchorInViewport = visible;
    popup.dataset.anchorVisible = visible ? "true" : "false";
    if (!visible) return;
    positionPopup(popup, state.anchorRect, state.anchorClientX);
  }

  loadAnnotations(highlighter, apiBase, bookmarkId, assetId, sidebar);

  // --- Text selection ---
  document.addEventListener("selectionchange", () => {
    if (performance.now() < state.suppressSelectionChangeUntil) return;
    if (state.isRestoringSelection) return;
    if (state.isOpeningFromHighlightClick) return;
    const range = getCurrentSelectionRange();
    if (!range) {
      if (restorePendingSelectionIfNeeded()) {
        schedulePopupReflow();
        return;
      }
      if (
        preferMobileToolbar &&
        state.pendingRange &&
        state.isOpen &&
        !state.pendingAnnotation &&
        !state.isPersisting
      ) {
        schedulePopupReflow();
        return;
      }
      // Safari can clear selection while interacting with toolbar controls.
      if (performance.now() - state.lastToolbarInteractionTs < 520) {
        schedulePopupReflow();
        return;
      }
      if (!isPopupTarget(document.activeElement) && !state.isPersisting) {
        closePopup();
        clearPending({ keepSelection: true });
      }
      return;
    }

    if (state.isPersisting) return;
    if (state.mouseSelecting) return;
    setMode("selection_ready");
    const canReusePointerX =
      Number.isFinite(state.lastSelectionPointerX) &&
      performance.now() - state.lastSelectionPointerTs < 420;
    const anchorX = canReusePointerX ? state.lastSelectionPointerX : null;
    showToolbarForSelection(range, anchorX);
  });

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (!contentEl.contains(e.target)) return;
    if (isPopupTarget(e.target)) return;
    state.mouseSelecting = true;
    state.mouseSelectStartX = e.clientX;
    state.mouseSelectStartY = e.clientY;
    state.mouseSelectMoved = false;
  });

  document.addEventListener("mousemove", (e) => {
    if (!state.mouseSelecting || state.mouseSelectMoved) return;
    const dx = e.clientX - state.mouseSelectStartX;
    const dy = e.clientY - state.mouseSelectStartY;
    if (dx * dx + dy * dy > 9) {
      state.mouseSelectMoved = true;
    }
  });

  document.addEventListener("mouseup", (e) => {
    if (!state.mouseSelecting) return;
    if (Number.isFinite(e.clientX)) {
      state.lastSelectionPointerX = e.clientX;
      state.lastSelectionPointerTs = performance.now();
    } else {
      state.lastSelectionPointerX = null;
      state.lastSelectionPointerTs = -1;
    }
    state.mouseSelecting = false;
    state.mouseSelectMoved = false;
    if (state.isPersisting || state.isOpeningFromHighlightClick) return;
    const range = getCurrentSelectionRange();
    if (!range) return;
    setMode("selection_ready");
    showToolbarForSelection(range, e.clientX);
  });

  // --- Click on existing highlight ---
  function openFromHighlightPointerEvent(e) {
    // Ignore the synthetic click that follows the same pointerup.
    if (
      e.type === "click" &&
      state.lastHighlightPointerUpTs >= 0 &&
      Math.abs(e.timeStamp - state.lastHighlightPointerUpTs) < 280
    ) {
      return;
    }
    if (e.type === "pointerup") {
      state.lastHighlightPointerUpTs = e.timeStamp;
    }

    // If the user is drag-selecting text, don't treat this as a highlight click.
    if (
      state.mouseSelecting &&
      (state.mouseSelectMoved || getCurrentSelectionRange())
    ) {
      return;
    }

    state.isOpeningFromHighlightClick = true;
    const annId = highlighter.getAnnotationIdFromTarget(
      e.target,
      e.clientX,
      e.clientY,
    );
    if (annId) {
      const ann = highlighter.annotations.get(annId);
      if (ann) {
        suppressSelectionChangeFor();
        showToolbarForAnnotation(ann, e.clientX);
        e.preventDefault();
        window.getSelection()?.removeAllRanges();
        setTimeout(() => {
          state.isOpeningFromHighlightClick = false;
        }, 0);
        return;
      }
    }
    setTimeout(() => {
      state.isOpeningFromHighlightClick = false;
    }, 0);
  }
  contentEl.addEventListener("pointerup", openFromHighlightPointerEvent);
  contentEl.addEventListener("click", openFromHighlightPointerEvent);

  // Keep popup interactions from collapsing selection.
  popup.addEventListener("mousedown", (e) => {
    markToolbarInteraction();
    const interactive = e.target.closest(
      "textarea, input, button, select, option, [contenteditable='true']",
    );
    if (
      popup.classList.contains("ld-annotation-toolbar-desktop") &&
      !interactive
    ) {
      e.preventDefault();
    }
  });
  popup.addEventListener("pointerdown", (e) => {
    markToolbarInteraction();
    e.stopPropagation();
    if (!preferMobileToolbar || !isDockedTopLayout()) return;
    const toolbarControl = e.target.closest(
      "[data-color], [data-action='delete'], [data-action='delete-confirm'], [data-action='delete-cancel']",
    );
    if (!toolbarControl) return;
    state.lastToolbarControlPointerDownTs = performance.now();
  });
  popup.addEventListener(
    "touchstart",
    () => {
      markToolbarInteraction();
    },
    { passive: true },
  );

  // --- Color button click ---
  popup.addEventListener("click", async (e) => {
    markToolbarInteraction();
    if (
      state.isOpen &&
      performance.now() - state.openedAt < 140 &&
      e.target.closest(
        "[data-action='delete'], [data-action='delete-confirm'], [data-action='delete-cancel']",
      )
    ) {
      return;
    }
    const deleteBtn = e.target.closest("[data-action='delete']");
    if (deleteBtn) {
      if (!state.pendingAnnotation) return;
      popup._deleteConfirm.hidden = !popup._deleteConfirm.hidden;
      return;
    }
    const deleteCancel = e.target.closest("[data-action='delete-cancel']");
    if (deleteCancel) {
      popup._deleteConfirm.hidden = true;
      return;
    }
    const deleteConfirm = e.target.closest("[data-action='delete-confirm']");
    if (deleteConfirm) {
      popup._deleteConfirm.hidden = true;
      await handleDelete();
      return;
    }

    const colorBtn = e.target.closest("[data-color]");
    if (!colorBtn) return;
    popup._deleteConfirm.hidden = true;
    await applyColor(colorBtn.dataset.color);
  });

  popup._noteInput.addEventListener("keydown", async (e) => {
    markToolbarInteraction();
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      await closeAndPersist();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closePopup();
      clearPending({ keepSelection: true });
    }
  });

  popup._noteInput.addEventListener("input", () => {
    markToolbarInteraction();
    if (
      state.pendingRange &&
      !state.pendingAnnotation &&
      !state.pendingColor &&
      popup._noteInput.value.trim().length > 0
    ) {
      state.pendingColor = DEFAULT_HIGHLIGHT_COLOR;
      updateColorButtons(popup, state.pendingColor);
      updateDraftHighlight();
    }
    if (state.saveStatus === "error") {
      setSaveStatus("idle", "");
    }
    void resizeNoteInput();
    reflowPopupPosition();
  });

  popup._noteInput.addEventListener("focus", () => {
    markToolbarInteraction();
    if (preferMobileToolbar) {
      state.dockedEditingSession = true;
      setPopupLayout("docked-top");
    }
    setMode("note_editing");
    void resizeNoteInput();
    reflowPopupPosition();
  });

  popup._noteInput.addEventListener("blur", async (e) => {
    markToolbarInteraction();
    if (!state.isOpen) return;
    const blurToToolbarControl =
      performance.now() - state.lastToolbarControlPointerDownTs < 260;
    if (blurToToolbarControl) {
      if (preferMobileToolbar && state.dockedEditingSession) {
        setPopupLayout("docked-top");
        schedulePopupReflow();
      }
      return;
    }
    await savePendingAnnotation();
    if (state.pendingAnnotation) {
      const latest = highlighter.annotations.get(
        String(state.pendingAnnotation.id),
      );
      if (latest) {
        state.pendingAnnotation = latest;
      }
    }
    if (preferMobileToolbar) {
      const nextActive = e.relatedTarget || document.activeElement;
      const focusStillInside = nextActive && isPopupTarget(nextActive);
      if (state.dockedEditingSession || focusStillInside) {
        setPopupLayout("docked-top");
      } else {
        setPopupLayout("anchored");
      }
      schedulePopupReflow();
    }
  });

  document.addEventListener("keydown", async (e) => {
    if (!state.isOpen) return;
    if (e.key === "Escape") {
      e.preventDefault();
      await closeAndPersist();
    }
  });

  // Close on outside pointer interaction.
  document.addEventListener("pointerdown", async (e) => {
    if (!state.isOpen) return;
    if (performance.now() - state.lastToolbarInteractionTs < 320) return;
    if (!isPopupTarget(e.target)) {
      if (preferMobileToolbar && isDockedTopLayout()) {
        state.dockedEditingSession = false;
      }
      popup._deleteConfirm.hidden = true;
      const contextToken = state.contextToken;
      await closeAndPersist({ contextToken });
    }
  });

  window.addEventListener("resize", reflowPopupPosition, { passive: true });
  document.addEventListener(
    "scroll",
    () => {
      state.lastScrollAt = performance.now();
      if (preferMobileToolbar && state.pendingRange && state.isOpen) {
        restorePendingSelectionIfNeeded();
      }
    },
    {
      passive: true,
      capture: true,
    },
  );
  document.addEventListener("scroll", reflowPopupPosition, {
    passive: true,
    capture: true,
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", reflowPopupPosition, {
      passive: true,
    });
    window.visualViewport.addEventListener("scroll", reflowPopupPosition, {
      passive: true,
    });
  }
  updateMobileToolbarOffset();
  return highlighter;
}

/**
 * Load annotations from the API.
 */
async function loadAnnotations(
  highlighter,
  apiBase,
  bookmarkId,
  assetId,
  sidebar,
) {
  if (!bookmarkId || Number(assetId) <= 0) {
    highlighter.load([]);
    if (sidebar) sidebar.annotations = [];
    return;
  }

  try {
    const response = await fetch(
      joinPath(apiBase, `bookmarks/${bookmarkId}/annotations/`),
    );
    if (!response.ok) return;

    const data = await response.json();
    const annotations = Array.isArray(data) ? data : data.results || [];
    const currentAssetId = Number(assetId);
    const currentAnnotations = [];
    const unresolvedAnnotations = [];

    for (const ann of annotations) {
      if (Number(ann.article_asset) === currentAssetId) {
        currentAnnotations.push(ann);
        continue;
      }

      try {
        const restored = await restoreAnnotationToAsset(
          highlighter,
          apiBase,
          currentAssetId,
          ann,
        );
        currentAnnotations.push(restored);
      } catch {
        unresolvedAnnotations.push({ ...ann, _unresolved: true });
      }
    }

    highlighter.load(currentAnnotations);
    if (sidebar) {
      sidebar.annotations = [
        ...Array.from(highlighter.annotations.values()),
        ...unresolvedAnnotations,
      ];
    }
  } catch (err) {
    console.error("Failed to load annotations:", err);
  }
}

// ---- Unified Highlight Popup ----

/**
 * Create the unified highlight popup element.
 */
function createHighlightPopup({ compact }) {
  const popup = document.createElement("div");
  popup.id = "ld-highlight-popup";
  popup.className = compact
    ? "ld-annotation-toolbar ld-annotation-toolbar-desktop"
    : "ld-annotation-toolbar ld-annotation-toolbar-mobile";
  popup.dataset.open = "false";
  popup.dataset.mode = "new";
  popup.dataset.noteOpen = "true";
  popup.dataset.state = "idle";
  popup.dataset.layout = "anchored";
  popup.dataset.saveState = "idle";
  popup.dataset.anchorVisible = "true";
  popup.hidden = true;
  popup.setAttribute("role", "dialog");
  popup.setAttribute("aria-label", gettext("Highlight actions"));
  popup.setAttribute("aria-hidden", "true");

  const toolbar = document.createElement("div");
  toolbar.className = "ld-annotation-toolbar-row";

  const colorCard = document.createElement("div");
  colorCard.className = "ld-annotation-card ld-annotation-card-colors";
  const colorGroup = document.createElement("div");
  colorGroup.className = "ld-annotation-color-group";

  // Color buttons
  for (const [name, cfg] of Object.entries(HIGHLIGHT_COLORS)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ld-annotation-color-btn";
    btn.dataset.color = name;
    const colorLabel = HIGHLIGHT_COLOR_LABELS[name] || String(cfg.label || "");
    btn.title = interpolate(gettext("Highlight: %(color)s"), {
      color: colorLabel,
    });
    btn.setAttribute(
      "aria-label",
      interpolate(gettext("Highlight with %(color)s"), { color: colorLabel }),
    );
    btn.style.setProperty("--ld-annotation-color", toSolidColor(cfg.bg));
    colorGroup.appendChild(btn);
  }
  colorCard.appendChild(colorGroup);
  toolbar.appendChild(colorCard);

  const actionCard = document.createElement("div");
  actionCard.className = "ld-annotation-card ld-annotation-card-actions";
  const actions = document.createElement("div");
  actions.className = "ld-annotation-action-group";
  actions.hidden = true;

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "ld-annotation-action-btn ld-annotation-action-delete";
  deleteBtn.title = gettext("Remove highlight");
  deleteBtn.setAttribute("aria-label", gettext("Remove highlight"));
  deleteBtn.dataset.action = "delete";
  deleteBtn.innerHTML = READER_ICONS.delete;
  deleteBtn.hidden = true;
  actions.appendChild(deleteBtn);

  const deleteWrap = document.createElement("span");
  deleteWrap.className = "annotation-delete-wrap";
  deleteWrap.appendChild(deleteBtn);

  const deleteConfirm = document.createElement("span");
  deleteConfirm.className = "ld-confirm-popup-inline";
  deleteConfirm.hidden = true;
  deleteConfirm.innerHTML = `
    <span class="confirm-popup-question">${gettext("Are you sure?")}</span>
    <span class="confirm-popup-actions">
      <button type="button" class="btn btn-sm" data-action="delete-cancel">${gettext("Cancel")}</button>
      <button type="button" class="btn btn-sm btn-error" data-action="delete-confirm">${gettext("Delete")}</button>
    </span>
  `;
  deleteWrap.appendChild(deleteConfirm);
  actions.appendChild(deleteWrap);

  actionCard.appendChild(actions);
  toolbar.appendChild(actionCard);

  popup.appendChild(toolbar);

  const noteArea = document.createElement("div");
  noteArea.className = "ld-annotation-card ld-annotation-note-area";

  const noteMeta = document.createElement("div");
  noteMeta.className = "ld-annotation-note-meta";

  const saveStatus = document.createElement("span");
  saveStatus.className = "ld-annotation-save-status";
  saveStatus.setAttribute("aria-live", "polite");
  noteMeta.appendChild(saveStatus);
  noteArea.appendChild(noteMeta);

  const noteInput = document.createElement("textarea");
  noteInput.className = "ld-annotation-note-input";
  noteInput.placeholder = gettext("Click to add note");
  noteInput.rows = 1;
  noteInput.setAttribute("aria-label", gettext("Annotation note"));
  noteArea.appendChild(noteInput);
  popup.appendChild(noteArea);

  // Store references
  popup._deleteBtn = deleteBtn;
  popup._deleteConfirm = deleteConfirm;
  popup._actionCard = actionCard;
  popup._actionGroup = actions;
  popup._noteArea = noteArea;
  popup._noteInput = noteInput;
  popup._saveStatus = saveStatus;

  return popup;
}

/**
 * Get bounding rect of an annotation by reconstructing its range.
 */
function getAnnotationRect(ann, contentEl) {
  try {
    const selector = ann.selector;
    const anchor = TextQuoteAnchor.fromSelector(contentEl, {
      exact: selector.exact || ann.selected_text,
      prefix: selector.prefix,
      suffix: selector.suffix,
    });
    const hint =
      typeof selector.start === "number" ? selector.start : undefined;
    const range = anchor.toRange({ hint });
    return range.getBoundingClientRect();
  } catch {
    return null;
  }
}

/**
 * Position popup below a rect, keeping it within the viewport.
 */
function positionPopup(popup, rect, anchorClientX = null) {
  popup.style.visibility = "hidden";
  popup.hidden = false;
  popup.style.left = "0px";
  popup.style.top = "0px";

  const gap = 6;
  const popupWidth = popup.offsetWidth;
  const popupHeight = popup.offsetHeight;
  const margin = popup.classList.contains("ld-annotation-toolbar-mobile")
    ? 8
    : 10;

  const preferredX = Number.isFinite(anchorClientX)
    ? anchorClientX
    : rect.right - popupWidth * 0.25;
  let left = preferredX - popupWidth * 0.5;
  left = Math.max(
    margin,
    Math.min(left, window.innerWidth - popupWidth - margin),
  );

  let top = rect.bottom + gap;

  if (top + popupHeight > window.innerHeight - margin) {
    top = rect.top - popupHeight - gap;
  }
  if (top < margin) top = margin;

  popup.style.left = `${left}px`;
  popup.style.top = `${top}px`;
  popup.style.visibility = "";
}

async function measureLineHeightPx(input) {
  const styles = window.getComputedStyle(input);
  const parsed = parseFloat(styles.lineHeight);
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }

  const probe = document.createElement("span");
  probe.textContent = "M";
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  probe.style.pointerEvents = "none";
  probe.style.whiteSpace = "pre";
  probe.style.font = styles.font;
  probe.style.lineHeight = styles.lineHeight;
  probe.style.letterSpacing = styles.letterSpacing;
  document.body.appendChild(probe);
  const h = probe.getBoundingClientRect().height;
  document.body.removeChild(probe);
  return h > 0 ? h : 18;
}

/**
 * Update color button borders to reflect current color.
 */
function updateColorButtons(popup, currentColor) {
  popup.querySelectorAll("[data-color]").forEach((btn) => {
    btn.dataset.active = btn.dataset.color === currentColor ? "true" : "false";
  });
}

/**
 * Render a reader article without bookmark/asset state.
 *
 * Used by the site-adapters test panel so a URL test can preview the same
 * article layout as a real bookmark reader page without requiring an existing
 * bookmark or article asset.
 */
function renderReaderPreview(options = {}) {
  const title = options.title || "";
  const wordCount = Number(options.wordCount) || 0;
  const content = options.content || "";
  const apiBase = normalizeBaseUrl(
    parseJsonScriptValue("reader-api-base-url", "/api/"),
  );
  const assetsBase = parseJsonScriptValue("reader-assets-base-url", "/assets");
  const bookmarksIndexUrl = parseJsonScriptValue(
    "reader-bookmarks-index-url",
    "/bookmarks",
  );
  if (title) document.title = title;
  const bodyMatch = content.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  const bodyHtml = bodyMatch ? bodyMatch[1] : content;

  renderArticle(
    bodyHtml,
    { title, wordCount },
    title || gettext("Reader"),
    {
      is_editable: true,
      is_preview: true,
      title,
      url: options.originalUrl || "",
      snapshot_url: options.snapshotUrl || "",
    },
    apiBase,
    assetsBase,
    bookmarksIndexUrl,
    0,
    0,
    "",
  );
}

// Expose to global scope for inline template scripts
window.renderReader = renderReader;
window.renderReaderPreview = renderReaderPreview;
