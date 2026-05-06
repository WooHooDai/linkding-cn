import { LitElement } from "lit";

/**
 * Base class for custom elements that wrap existing server-rendered DOM.
 *
 * Handles timing issues where connectedCallback fires before child elements
 * are parsed during initial page load. With Turbo navigation, children are
 * always available, but on fresh page loads they may not be.
 */
export class HeadlessElement extends HTMLElement {
  connectedCallback() {
    if (this.__initialized) {
      // Re-initialize after being moved in the DOM (e.g. filter drawer teleport)
      this.init();
      return;
    }
    this.__initialized = true;
    if (document.readyState === "loading") {
      document.addEventListener("turbo:load", () => this.init(), {
        once: true,
      });
    } else {
      this.init();
    }
  }

  init() {}
}

let isTopFrameVisit = false;

document.addEventListener("turbo:visit", (event) => {
  const url = event.detail.url;
  isTopFrameVisit =
    document.querySelector(`turbo-frame[src="${url}"][target="_top"]`) !==
    null;
});

document.addEventListener("turbo:render", () => {
  isTopFrameVisit = false;
});

document.addEventListener("turbo:before-morph-element", (event) => {
  if (event.target instanceof TurboLitElement) {
    event.preventDefault();
  }
});

export class TurboLitElement extends LitElement {
  constructor() {
    super();
    this.__prepareForCache = this.__prepareForCache.bind(this);
  }

  createRenderRoot() {
    return this;
  }

  connectedCallback() {
    document.addEventListener("turbo:before-cache", this.__prepareForCache);
    super.connectedCallback();
  }

  disconnectedCallback() {
    document.removeEventListener("turbo:before-cache", this.__prepareForCache);
    super.disconnectedCallback();
  }

  __prepareForCache() {
    if (!isTopFrameVisit) {
      this.innerHTML = "";
    }
  }
}
