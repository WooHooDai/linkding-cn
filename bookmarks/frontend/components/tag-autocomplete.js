import { html, nothing } from "lit";
import { keyed } from "lit/directives/keyed.js";
import { cache } from "../utils/tag-cache.js";
import { TurboLitElement } from "../utils/element.js";
import { PositionController } from "../utils/position-controller.js";
import { getCurrentWord, getCurrentWordBounds } from "../utils/input.js";
import { scoreTag } from "../utils/tag-match.js";

// 候选列表上限：前缀命中（强信号）几乎总能被完整覆盖，
  // 子串命中（弱信号）按频次取前 N 条补位，避免低相关候选撑爆下拉菜单
const MAX_SUGGESTIONS = 100;

export class TagAutocomplete extends TurboLitElement {
  static properties = {
    inputId: { type: String, attribute: "input-id" },
    inputName: { type: String, attribute: "input-name" },
    inputValue: { type: String, attribute: "input-value" },
    inputPlaceholder: { type: String, attribute: "input-placeholder" },
    inputAriaDescribedBy: {
      type: String,
      attribute: "input-aria-describedby",
    },
    variant: { type: String },
    isFocus: { state: true },
    isOpen: { state: true },
    suggestions: { state: true },
    selectedIndex: { state: true },
  };

  constructor() {
    super();
    this.inputId = "";
    this.inputName = "";
    this.inputValue = "";
    this.inputPlaceholder = "";
    this.inputAriaDescribedBy = "";
    this.variant = "default";
    this.isFocus = false;
    this.isOpen = false;
    this.suggestions = [];
    this.selectedIndex = 0;
    this.input = null;
    this.suggestionList = null;
  }

  firstUpdated() {
    this.input = this.querySelector("input");
    this.suggestionList = this.querySelector(".menu");
    this.positionController = new PositionController({
      anchor: this.input,
      overlay: this.suggestionList,
      autoWidth: true,
      placement: "bottom-start",
    });
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.close();
  }

  handleFocus() {
    this.isFocus = true;
  }

  handleBlur() {
    this.isFocus = false;
    this.close();
  }

  async handleInput(event) {
    this.input = event.target;

    const tags = await cache.getTags();
    const word = getCurrentWord(this.input);

    const search = word.toLowerCase();
    this.suggestions = word
      ? tags
          .map((tag) => ({ tag, score: scoreTag(tag, search) }))
          .filter((entry) => entry.score >= 0)
          // 稳定排序：同一分值层内保持 tag-cache 的频次降序
          .sort((a, b) => a.score - b.score)
          .slice(0, MAX_SUGGESTIONS)
          .map((entry) => entry.tag)
      : [];

    if (word && this.suggestions.length > 0) {
      this.open();
    } else {
      this.close();
    }
  }

  handleKeyDown(event) {
    // Ignore keys while an IME is composing (e.g. Chinese Pinyin).
    if (event.isComposing) {
      return;
    }

    if (this.isOpen && (event.key === "Enter" || event.key === "Tab")) {
      const suggestion = this.suggestions[this.selectedIndex];
      this.complete(suggestion);
      event.preventDefault();
    }
    if (event.key === "Escape") {
      this.close();
      event.preventDefault();
    }
    // 回车键：当下拉框关闭时，触发 commit 事件（表示保存并退出）
    if (event.key === "Enter" && !event.defaultPrevented) {
      this.dispatchEvent(new CustomEvent("commit", { bubbles: true }));
    }
    if (event.key === "ArrowUp") {
      this.updateSelection(-1);
      event.preventDefault();
    }
    if (event.key === "ArrowDown") {
      this.updateSelection(1);
      event.preventDefault();
    }
  }

  open() {
    this.isOpen = true;
    this.selectedIndex = 0;
    this.positionController.enable();
  }

  close() {
    this.isOpen = false;
    this.suggestions = [];
    this.selectedIndex = 0;
    this.positionController?.disable();
  }

  complete(suggestion) {
    const bounds = getCurrentWordBounds(this.input);
    const value = this.input.value;
    this.input.value =
      value.substring(0, bounds.start) +
      suggestion.name +
      " " +
      value.substring(bounds.end);
    this.dispatchEvent(new CustomEvent("input", { bubbles: true }));
    this.close();
  }

  async updateSelection(dir) {
    const length = this.suggestions.length;
    let newIndex = this.selectedIndex + dir;

    if (newIndex < 0) newIndex = Math.max(length - 1, 0);
    if (newIndex >= length) newIndex = 0;

    this.selectedIndex = newIndex;

    await this.updateComplete;
    const selectedListItem = this.suggestionList?.querySelector("li.selected");
    selectedListItem?.scrollIntoView({ block: "nearest" });
  }

  render() {
    return html`
      <div class="form-autocomplete ${this.variant === "small" ? "small" : ""}">
        <div
          class="form-autocomplete-input form-input ${this.isFocus
            ? "is-focused"
            : ""}"
        >
          <input
            id="${this.inputId || nothing}"
            name="${this.inputName || nothing}"
            .value="${this.inputValue || ""}"
            placeholder="${this.inputPlaceholder || " "}"
            class="form-input"
            type="text"
            autocomplete="off"
            autocapitalize="off"
            aria-describedby="${this.inputAriaDescribedBy || nothing}"
            @input=${this.handleInput}
            @keydown=${this.handleKeyDown}
            @focus=${this.handleFocus}
            @blur=${this.handleBlur}
          />
        </div>

        <ul
          class="menu ${this.isOpen && this.suggestions.length > 0
            ? "open"
            : ""}"
        >
          ${this.suggestions.map(
            (tag, index) => html`
              ${keyed(tag.name, html`
                <li class="menu-item ${this.selectedIndex === index ? "selected" : ""}">
                  <a
                    href="#"
                    @mousedown=${(event) => {
                      event.preventDefault();
                      this.complete(tag);
                    }}
                  >
                    ${tag.name}
                  </a>
                </li>
              `)}
            `,
          )}
        </ul>
      </div>
    `;
  }
}

customElements.define("ld-tag-autocomplete", TagAutocomplete);
