/**
 * Anchor classes for converting between DOM Range objects and W3C selectors.
 *
 * Adapted from Hypothesis client (https://github.com/hypothesis/client),
 * file: src/annotator/anchoring/types.ts
 * Licensed under the 2-Clause BSD License.
 *
 * Provides TextQuoteAnchor and TextPositionAnchor for serializing and
 * deserializing text selections using W3C Web Annotation selectors.
 */

import { matchQuote } from "./match-quote";
import { TextRange, TextPosition } from "./text-range";

/**
 * Converts between TextPositionSelector selectors and Range objects.
 */
export class TextPositionAnchor {
  /**
   * @param {Element} root
   * @param {number} start
   * @param {number} end
   */
  constructor(root, start, end) {
    this.root = root;
    this.start = start;
    this.end = end;
  }

  /**
   * Create a TextPositionAnchor from a DOM Range.
   *
   * @param {Element} root
   * @param {Range} range
   * @returns {TextPositionAnchor}
   */
  static fromRange(root, range) {
    const textRange = TextRange.fromRange(range).relativeTo(root);
    return new TextPositionAnchor(
      root,
      textRange.start.offset,
      textRange.end.offset
    );
  }

  /**
   * Create a TextPositionAnchor from a serialized TextPositionSelector.
   *
   * @param {Element} root
   * @param {{start: number, end: number}} selector
   * @returns {TextPositionAnchor}
   */
  static fromSelector(root, selector) {
    return new TextPositionAnchor(root, selector.start, selector.end);
  }

  /**
   * Serialize to a TextPositionSelector.
   *
   * @returns {{type: string, start: number, end: number}}
   */
  toSelector() {
    return {
      type: "TextPositionSelector",
      start: this.start,
      end: this.end,
    };
  }

  /**
   * Resolve to a DOM Range.
   *
   * @returns {Range}
   */
  toRange() {
    return TextRange.fromOffsets(this.root, this.start, this.end).toRange();
  }
}

/**
 * Converts between TextQuoteSelector selectors and Range objects.
 */
export class TextQuoteAnchor {
  /**
   * @param {Element} root
   * @param {string} exact
   * @param {{prefix?: string, suffix?: string}} [context={}]
   */
  constructor(root, exact, context = {}) {
    this.root = root;
    this.exact = exact;
    this.context = context;
  }

  /**
   * Create a TextQuoteAnchor from a DOM Range.
   *
   * @param {Element} root
   * @param {Range} range
   * @returns {TextQuoteAnchor}
   */
  static fromRange(root, range) {
    const text = root.textContent;
    const textRange = TextRange.fromRange(range).relativeTo(root);

    const start = textRange.start.offset;
    const end = textRange.end.offset;

    // Number of characters around the quote to capture as context.
    const contextLen = 32;

    return new TextQuoteAnchor(root, text.slice(start, end), {
      prefix: text.slice(Math.max(0, start - contextLen), start),
      suffix: text.slice(end, Math.min(text.length, end + contextLen)),
    });
  }

  /**
   * Create a TextQuoteAnchor from a serialized TextQuoteSelector.
   *
   * @param {Element} root
   * @param {{exact: string, prefix?: string, suffix?: string}} selector
   * @returns {TextQuoteAnchor}
   */
  static fromSelector(root, selector) {
    const { prefix, suffix } = selector;
    return new TextQuoteAnchor(root, selector.exact, { prefix, suffix });
  }

  /**
   * Serialize to a TextQuoteSelector.
   *
   * @returns {{type: string, exact: string, prefix?: string, suffix?: string}}
   */
  toSelector() {
    return {
      type: "TextQuoteSelector",
      exact: this.exact,
      prefix: this.context.prefix,
      suffix: this.context.suffix,
    };
  }

  /**
   * Resolve to a DOM Range.
   *
   * @param {{hint?: number}} [options={}]
   * @returns {Range}
   */
  toRange(options = {}) {
    return this.toPositionAnchor(options).toRange();
  }

  /**
   * Resolve to a TextPositionAnchor.
   *
   * @param {{hint?: number}} [options={}]
   * @returns {TextPositionAnchor}
   */
  toPositionAnchor(options = {}) {
    const text = this.root.textContent;
    const match = matchQuote(text, this.exact, {
      ...this.context,
      hint: options.hint,
    });
    if (!match) {
      throw new Error("Quote not found");
    }
    return new TextPositionAnchor(this.root, match.start, match.end);
  }
}

/**
 * Create selectors from a DOM Range.
 *
 * @param {Element} root
 * @param {Range} range
 * @returns {{position: object, quote: object}}
 */
export function describeRange(root, range) {
  const position = TextPositionAnchor.fromRange(root, range).toSelector();
  const quote = TextQuoteAnchor.fromRange(root, range).toSelector();
  return { position, quote };
}

/**
 * Anchor a selector back to a DOM Range.
 *
 * @param {Element} root
 * @param {{type: string, exact?: string, prefix?: string, suffix?: string, start?: number, end?: number}} selector
 * @param {{hint?: number}} [options={}]
 * @returns {Range}
 */
export function anchorSelector(root, selector, options = {}) {
  if (selector.type === "TextQuoteSelector") {
    return TextQuoteAnchor.fromSelector(root, selector).toRange(options);
  }
  if (selector.type === "TextPositionSelector") {
    return TextPositionAnchor.fromSelector(root, selector).toRange();
  }
  throw new Error(`Unsupported selector type: ${selector.type}`);
}

/**
 * Rightmost text rect on the last line of a Range.
 *
 * A collapsed range at an Element boundary (drag selections often end as
 * "parent element + child index") resolves to the container's top-left, so it
 * cannot be used to anchor floating UI. Zero-sized rects come from empty lines
 * or split text nodes and have to be skipped as well.
 *
 * @param {Range} range
 * @returns {DOMRect}
 */
export function lastVisibleClientRect(range) {
  let last = null;
  for (const rect of range.getClientRects()) {
    if (rect.width <= 0 || rect.height <= 0) continue;
    if (
      !last ||
      rect.bottom > last.bottom + 2 ||
      (Math.abs(rect.bottom - last.bottom) <= 2 && rect.right > last.right)
    ) {
      last = rect;
    }
  }
  return last || range.getBoundingClientRect();
}
