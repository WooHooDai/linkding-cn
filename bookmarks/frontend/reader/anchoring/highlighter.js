/**
 * Highlight rendering module.
 *
 * Uses CSS Custom Highlight API for rendering highlights without modifying DOM.
 * Falls back to <span> wrapping for browsers that don't support the API.
 *
 * CSS Custom Highlight API references:
 * - https://developer.mozilla.org/en-US/docs/Web/API/CSS_Custom_Highlight_API
 * - https://drafts.csswg.org/css-highlight-api-1/
 *
 * Inspired by Obsidian Web Clipper's highlight rendering approach.
 */

import {
  TextQuoteAnchor,
  TextPositionAnchor,
  describeRange,
  lastVisibleClientRect,
} from "./index";
import { gettext } from "../../utils/i18n.js";
import { getCSRFToken } from "../../utils/csrf.js";

/**
 * Get CSRF token from cookie or meta tag.
 * @returns {string}
 */
function normalizeBaseUrl(baseUrl) {
  const value = String(baseUrl || "").trim();
  if (!value) return "";
  return `${value.replace(/\/+$/, "")}/`;
}

function joinPath(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${String(path || "").replace(/^\/+/, "")}`;
}

/**
 * Highlight color definitions.
 * 每个色号给出两种不透明度：bg 用于正文标记，dot 用于小面积色点。
 * 这是「内容色」而非界面配色，取值与 styles/highlight-colors.css 的
 * --hl-* 保持一致；::highlight() 里必须写字面量，故此处不使用 var()。
 */
export const HIGHLIGHT_COLORS = {
  yellow: {
    bg: "rgba(255, 235, 0, 0.35)",
    dot: "rgba(255, 235, 0, 0.8)",
    label: gettext("Yellow"),
  },
  green: {
    bg: "rgba(0, 200, 83, 0.3)",
    dot: "rgba(0, 200, 83, 0.8)",
    label: gettext("Green"),
  },
  blue: {
    bg: "rgba(66, 165, 245, 0.3)",
    dot: "rgba(66, 165, 245, 0.8)",
    label: gettext("Blue"),
  },
  pink: {
    bg: "rgba(236, 64, 122, 0.3)",
    dot: "rgba(236, 64, 122, 0.8)",
    label: gettext("Pink"),
  },
  primary: {
    bg: "color-mix(in srgb, var(--primary-color) 35%, transparent)",
    dot: "color-mix(in srgb, var(--primary-color) 80%, transparent)",
    label: gettext("Theme"),
  },
};
const DRAFT_HIGHLIGHT_ID = "ld-hl-draft";
const NOTE_DOT_MARKER_CLASS = "ld-highlight-note-marker";

/**
 * Check if CSS Custom Highlight API is supported.
 * @returns {boolean}
 */
function supportsCSSHighlight() {
  return typeof CSS !== "undefined" && CSS.highlights !== undefined;
}

/**
 * Highlight manager that handles rendering, creating, and removing highlights.
 */
export class Highlighter {
  /**
   * @param {Element} root - The root element containing the article content
   * @param {object} [options]
   * @param {string} [options.apiBase] - Base URL for annotation API
   * @param {number} [options.bookmarkId] - Current bookmark ID
   * @param {number} [options.assetId] - Current article asset ID
   */
  constructor(root, options = {}) {
    this.root = root;
    this.apiBase = normalizeBaseUrl(options.apiBase);
    this.bookmarkId = options.bookmarkId;
    this.assetId = options.assetId;
    /** @type {Map<string, object>} id -> annotation data */
    this.annotations = new Map();
    this._useCSSHighlight = supportsCSSHighlight();
    this._changeCallbacks = [];
    this._draftRange = null;
    this._noteMarkerFrame = null;
    this._baseHighlightCSS = `::highlight(${DRAFT_HIGHLIGHT_ID}) { background-color: var(--ld-draft-highlight-bg, ${HIGHLIGHT_COLORS.yellow.bg}); }`;

    if (this._useCSSHighlight) this._ensureDynamicStyleEl();

    // 圆点是按文字矩形定位的绝对定位元素，排版变了就得重新量一次
    this._onLayoutChange = () => this.scheduleNoteMarkers();
    window.addEventListener("resize", this._onLayoutChange, { passive: true });
    document.addEventListener("reader-settings-changed", this._onLayoutChange);
  }

  _ensureDynamicStyleEl() {
    let el = document.getElementById("ld-highlight-styles");
    if (!el) {
      el = document.createElement("style");
      el.id = "ld-highlight-styles";
      document.head.appendChild(el);
    }
    if (!el.textContent.includes(`::highlight(${DRAFT_HIGHLIGHT_ID})`)) {
      el.textContent += this._baseHighlightCSS;
    }
    this._styleEl = el;
  }

  _resetHighlightStyles() {
    if (!this._styleEl) return;
    this._styleEl.textContent = this._useCSSHighlight ? this._baseHighlightCSS : "";
  }

  _hasNoteContent(ann) {
    return (
      typeof ann?.note_content === "string" && ann.note_content.trim().length > 0
    );
  }

  _clearNoteMarkers() {
    this.root
      .querySelectorAll(`.${NOTE_DOT_MARKER_CLASS}`)
      .forEach((node) => node.remove());
  }

  scheduleNoteMarkers() {
    if (this._noteMarkerFrame !== null) return;
    this._noteMarkerFrame = requestAnimationFrame(() => {
      this._noteMarkerFrame = null;
      if (this.root.isConnected) this.repositionNoteMarkers();
    });
  }

  /**
   * 给带批注的高亮画右上角圆点。
   *
   * 圆点挂在正文容器开头、绝对定位，不插进文字流：跨段落、段尾选区、
   * 代码块换行都不会让它自成一行，也不会切碎文本节点影响后续锚定。
   */
  repositionNoteMarkers() {
    this._clearNoteMarkers();
    if (!this.root.isConnected) return;

    const rootRect = this.root.getBoundingClientRect();
    const originX = rootRect.left + this.root.clientLeft;
    const originY = rootRect.top + this.root.clientTop;
    const fragment = document.createDocumentFragment();

    for (const [, ann] of this.annotations) {
      if (!this._hasNoteContent(ann)) continue;
      let range;
      try {
        range = this.resolveAnnotationRange(ann);
      } catch {
        continue;
      }
      const rect = lastVisibleClientRect(range);
      if (!rect) continue;

      const dot = document.createElement("span");
      dot.className = NOTE_DOT_MARKER_CLASS;
      dot.dataset.annotationId = String(ann.id);
      dot.setAttribute("aria-hidden", "true");
      dot.style.left = `${rect.right - originX}px`;
      dot.style.top = `${rect.top - originY}px`;
      fragment.appendChild(dot);
    }

    // 插在开头，免得抢占 `#article-content > :last-child` 的样式
    this.root.prepend(fragment);
  }

  /**
   * Register a callback invoked whenever annotations change.
   * Returns an unsubscribe function.
   * @param {(annotations: Map<string, object>) => void} callback
   * @returns {() => void}
   */
  onChange(callback) {
    this._changeCallbacks.push(callback);
    return () => {
      this._changeCallbacks = this._changeCallbacks.filter(cb => cb !== callback);
    };
  }

  _notifyChange() {
    for (const cb of this._changeCallbacks) {
      cb(this.annotations);
    }
  }

  /**
   * Add a CSS ::highlight rule for a specific annotation.
   * @param {string} id
   * @param {string} color
   */
  _addHighlightStyle(id, color) {
    const bgColor = HIGHLIGHT_COLORS[color]?.bg || HIGHLIGHT_COLORS.yellow.bg;
    const rule = `::highlight(ld-hl-${id}) { background-color: ${bgColor}; } `;
    this._styleEl.textContent += rule;
  }

  /**
   * Render a transient draft highlight for pending selection.
   *
   * @param {Range|null} range
   * @param {string} [color="yellow"]
   */
  setDraftHighlight(range, color = "yellow") {
    if (!this._useCSSHighlight) return false;
    if (!range) {
      this.clearDraftHighlight();
      return false;
    }
    try {
      if (!this.root.contains(range.commonAncestorContainer)) {
        this.clearDraftHighlight();
        return false;
      }
      const cloned = range.cloneRange();
      this._draftRange = cloned;
      document.documentElement.style.setProperty(
        "--ld-draft-highlight-bg",
        HIGHLIGHT_COLORS[color]?.bg || HIGHLIGHT_COLORS.yellow.bg
      );
      const highlight = new Highlight(cloned);
      if ("priority" in highlight) {
        highlight.priority = 8;
      }
      CSS.highlights.set(DRAFT_HIGHLIGHT_ID, highlight);
      return true;
    } catch {
      this.clearDraftHighlight();
      return false;
    }
  }

  /**
   * Clear transient draft highlight.
   */
  clearDraftHighlight() {
    if (!this._useCSSHighlight) return;
    CSS.highlights.delete(DRAFT_HIGHLIGHT_ID);
    this._draftRange = null;
    document.documentElement.style.removeProperty("--ld-draft-highlight-bg");
  }

  /**
   * Load all annotations for the current asset and render them.
   *
   * @param {Array<object>} annotations - Array of annotation data from API
   */
  load(annotations) {
    this.annotations.clear();
    for (const ann of annotations) {
      this.annotations.set(String(ann.id), ann);
    }
    this.renderAll();
    this._notifyChange();
  }

  /**
   * Render all stored highlights.
   */
  renderAll() {
    this._clearNoteMarkers();

    if (this._useCSSHighlight) {
      // Clear existing highlights
      for (const [id] of this.annotations) {
        CSS.highlights.delete(`ld-hl-${id}`);
      }
      // Clear dynamic styles
      this._resetHighlightStyles();
    } else {
      // Clear existing span highlights
      this.root
        .querySelectorAll(".ld-highlight")
        .forEach((el) => {
          const parent = el.parentNode;
          while (el.firstChild) {
            parent.insertBefore(el.firstChild, el);
          }
          parent.removeChild(el);
        });
      // Normalize merged text nodes
      this.root.normalize();
    }

    // Render each annotation
    for (const [, ann] of this.annotations) {
      this._renderAnnotation(ann);
    }
    this.repositionNoteMarkers();
  }

  /**
   * Render a single annotation highlight.
   *
   * @param {object} ann
   * @returns {boolean} Whether rendering succeeded
   */
  _renderAnnotation(ann) {
    try {
      const range = this.resolveAnnotationRange(ann);

      if (this._useCSSHighlight) {
        this._addHighlightStyle(String(ann.id), ann.color);
        const highlight = new Highlight(range);
        CSS.highlights.set(`ld-hl-${ann.id}`, highlight);
      } else {
        this._wrapRangeWithSpan(range, ann);
      }

      return true;
    } catch (err) {
      console.warn(`Failed to render annotation ${ann.id}:`, err);
      return false;
    }
  }

  /**
   * Resolve an annotation to a DOM Range in the current article content.
   *
   * @param {object} ann
   * @returns {Range}
   */
  resolveAnnotationRange(ann) {
    const selector = ann.selector || {};
    try {
      // Use start offset as hint for faster matching (stored alongside TextQuoteSelector)
      const hint = typeof selector.start === "number" ? selector.start : undefined;

      // Use TextQuoteAnchor for robust matching
      const quoteSelector =
        selector.type === "TextQuoteSelector"
          ? selector
          : { type: "TextQuoteSelector", exact: ann.selected_text };

      const anchor = TextQuoteAnchor.fromSelector(this.root, {
        ...quoteSelector,
        prefix: selector.prefix,
        suffix: selector.suffix,
      });

      return anchor.toRange({ hint });
    } catch {
      throw new Error("Quote not found");
    }
  }

  /**
   * Wrap a Range with <span> elements (fallback for browsers without
   * CSS Custom Highlight API).
   *
   * @param {Range} range
   * @param {object} ann
   */
  _wrapRangeWithSpan(range, ann) {
    const textNodes = this._getTextNodesInRange(range);
    for (const { node, start, end } of textNodes) {
      // Split text node if needed
      let targetNode = node;
      let localStart = start;
      let localEnd = end;

      if (localStart > 0) {
        targetNode = node.splitText(localStart);
        localEnd -= localStart;
        localStart = 0;
      }
      if (localEnd < targetNode.length) {
        targetNode.splitText(localEnd);
      }

      const span = document.createElement("span");
      span.className = "ld-highlight";
      span.dataset.color = ann.color || "yellow";
      span.dataset.annotationId = String(ann.id);
      targetNode.parentNode.insertBefore(span, targetNode);
      span.appendChild(targetNode);
    }
  }

  /**
   * Get all text nodes within a Range with their local offsets.
   *
   * @param {Range} range
   * @returns {Array<{node: Text, start: number, end: number}>}
   */
  _getTextNodesInRange(range) {
    const result = [];
    const walker = document.createTreeWalker(
      range.commonAncestorContainer,
      NodeFilter.SHOW_TEXT
    );

    let currentOffset = 0;
    let node;
    const startContainer = range.startContainer;
    const endContainer = range.endContainer;
    const startOffset = range.startOffset;
    const endOffset = range.endOffset;

    // Calculate the global text offset of the range
    let rangeStart = -1;
    let rangeEnd = -1;

    // Use TreeWalker to find text nodes
    const allTextWalker = document.createTreeWalker(
      this.root,
      NodeFilter.SHOW_TEXT
    );
    let textOffset = 0;

    while ((node = allTextWalker.nextNode())) {
      const nodeLen = node.textContent.length;

      if (node === startContainer) {
        rangeStart = textOffset + startOffset;
      }
      if (node === endContainer) {
        rangeEnd = textOffset + endOffset;
      }

      if (rangeStart >= 0 && rangeEnd >= 0) break;
      textOffset += nodeLen;
    }

    if (rangeStart < 0 || rangeEnd < 0) return result;

    // Now find all text nodes in the range
    const rangeWalker = document.createTreeWalker(
      this.root,
      NodeFilter.SHOW_TEXT
    );
    textOffset = 0;

    while ((node = rangeWalker.nextNode())) {
      const nodeLen = node.textContent.length;
      const nodeStart = textOffset;
      const nodeEnd = textOffset + nodeLen;

      if (nodeStart < rangeEnd && nodeEnd > rangeStart) {
        result.push({
          node,
          start: Math.max(0, rangeStart - nodeStart),
          end: Math.min(nodeLen, rangeEnd - nodeStart),
        });
      }

      textOffset += nodeLen;
    }

    return result;
  }

  /**
   * Create a new annotation from a DOM Range.
   *
   * @param {Range} range
   * @param {string} [color="yellow"]
   * @param {string} [note=""]
   * @returns {Promise<object|null>} The created annotation data, or null on failure
   */
  async createAnnotation(range, color = "yellow", note = "") {
    if (!this.apiBase || !this.bookmarkId || !this.assetId) {
      console.error("Highlighter: API base, bookmark ID, and asset ID required");
      return null;
    }

    const selectedText = range.toString();

    const { position, quote } = describeRange(this.root, range);

    try {
      const response = await fetch(
        joinPath(this.apiBase, `bookmarks/${this.bookmarkId}/annotations/`),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCSRFToken(),
          },
          body: JSON.stringify({
            article_asset: this.assetId,
            selector: { ...quote, start: position.start, end: position.end },
            selected_text: selectedText,
            color,
            note_content: note,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const ann = await response.json();
      this.annotations.set(String(ann.id), ann);
      this._renderAnnotation(ann);
      this.repositionNoteMarkers();
      this._notifyChange();
      return ann;
    } catch (err) {
      console.error("Failed to create annotation:", err);
      return null;
    }
  }

  /**
   * Update an existing annotation.
   *
   * @param {string} id
   * @param {{color?: string, note_content?: string}} updates
   * @returns {Promise<boolean>}
   */
  async updateAnnotation(id, updates) {
    if (!this.apiBase) return false;

    try {
      const response = await fetch(joinPath(this.apiBase, `annotations/${id}/`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRFToken(),
        },
        body: JSON.stringify(updates),
      });

      if (!response.ok) throw new Error(`API error: ${response.status}`);

      const ann = await response.json();
      this.annotations.set(String(ann.id), ann);
      this.renderAll();
      this._notifyChange();
      return true;
    } catch (err) {
      console.error("Failed to update annotation:", err);
      return false;
    }
  }

  /**
   * Delete an annotation.
   *
   * @param {string} id
   * @returns {Promise<boolean>}
   */
  async deleteAnnotation(id) {
    if (!this.apiBase) return false;

    try {
      const response = await fetch(joinPath(this.apiBase, `annotations/${id}/`), {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCSRFToken(),
        },
      });

      if (!response.ok) throw new Error(`API error: ${response.status}`);

      this.annotations.delete(String(id));
      this.renderAll();
      this._notifyChange();
      return true;
    } catch (err) {
      console.error("Failed to delete annotation:", err);
      return false;
    }
  }

  /**
   * Find the annotation ID associated with a click event target.
   *
   * @param {EventTarget} target
   * @param {number} [x] - Click x coordinate (used for CSS Highlight API)
   * @param {number} [y] - Click y coordinate (used for CSS Highlight API)
   * @returns {string|null} Annotation ID or null
   */
  getAnnotationIdFromTarget(target, x, y) {
    if (this._useCSSHighlight) {
      // CSS Highlight API doesn't modify DOM, so we check click position
      if (x != null && y != null) {
        const id = this._getAnnotationIdAtPoint(x, y);
        if (id) return id;

        // Fallback probe points around click for better hit detection.
        const probes = [
          [x - 2, y], [x + 2, y], [x, y - 2], [x, y + 2],
          [x - 4, y], [x + 4, y], [x, y - 4], [x, y + 4],
        ];
        for (const [px, py] of probes) {
          const probeId = this._getAnnotationIdAtPoint(px, py);
          if (probeId) return probeId;
        }

        // Fallback when position offsets are unavailable/inaccurate:
        // reconstruct annotation ranges and hit-test their client rects.
        const rectHitId = this._getAnnotationIdByRangeRect(x, y);
        if (rectHitId) return rectHitId;
      }
      return null;
    }

    // With span wrapping, check for highlight class
    const el = target.closest?.(".ld-highlight");
    return el?.dataset.annotationId || null;
  }

  /**
   * Find annotation at given viewport coordinates using caretPositionFromPoint.
   * @param {number} x
   * @param {number} y
   * @returns {string|null}
   */
  _getAnnotationIdAtPoint(x, y) {
    let pos = null;
    if (document.caretPositionFromPoint) {
      pos = document.caretPositionFromPoint(x, y);
    } else if (document.caretRangeFromPoint) {
      const range = document.caretRangeFromPoint(x, y);
      if (range) {
        pos = { offsetNode: range.startContainer, offset: range.startOffset };
      }
    }
    if (!pos) return null;

    // Calculate global text offset
    const walker = document.createTreeWalker(this.root, NodeFilter.SHOW_TEXT);
    let textOffset = 0;
    let node;
    while ((node = walker.nextNode())) {
      if (node === pos.offsetNode) {
        textOffset += pos.offset;
        break;
      }
      textOffset += node.textContent.length;
    }

    // Check if offset falls within any annotation
    for (const [id, ann] of this.annotations) {
      const sel = ann.selector;
      if (typeof sel.start === "number" && typeof sel.end === "number") {
        if (textOffset >= sel.start && textOffset <= sel.end) {
          return id;
        }
      }
    }
    return null;
  }

  /**
   * Fallback hit-test by rebuilding each annotation range and checking
   * whether click coordinates lie inside one of its client rects.
   * @param {number} x
   * @param {number} y
   * @returns {string|null}
   */
  _getAnnotationIdByRangeRect(x, y) {
    for (const [id, ann] of this.annotations) {
      try {
        const selector = ann.selector || {};
        const anchor = TextQuoteAnchor.fromSelector(this.root, {
          exact: selector.exact || ann.selected_text,
          prefix: selector.prefix,
          suffix: selector.suffix,
        });
        const hint =
          typeof selector.start === "number" ? selector.start : undefined;
        const range = anchor.toRange({ hint });
        const rects = range.getClientRects();
        for (const rect of rects) {
          if (
            x >= rect.left - 1 &&
            x <= rect.right + 1 &&
            y >= rect.top - 1 &&
            y <= rect.bottom + 1
          ) {
            return id;
          }
        }
      } catch {
        // Ignore unmatched annotations for hit testing.
      }
    }
    return null;
  }
}
