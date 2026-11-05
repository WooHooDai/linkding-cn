import { html, nothing } from "lit";
import { keyed } from "lit/directives/keyed.js";
import { cache } from "../utils/tag-cache.js";
import { TurboLitElement } from "../utils/element.js";
import { PositionController } from "../utils/position-controller.js";
import { getCurrentWord, getCurrentWordBounds } from "../utils/input.js";
import { swallowNextClickIn } from "../utils/swallow-click.js";
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
    multiline: { type: Boolean },
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
    this.multiline = false;
    this.isFocus = false;
    this.isOpen = false;
    this.suggestions = [];
    this.selectedIndex = 0;
    this.input = null;
    this.suggestionList = null;
  }

  firstUpdated() {
    this.input = this.querySelector("input, textarea");
    this.suggestionList = this.querySelector(".menu");
    if (this.multiline) this.autoResize();
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

  handleFocus(event) {
    this.isFocus = true;
    // 失焦会清空候选，重新聚焦时光标前可能已经是一个完整的词（例如
    // "Sof" 想补成 "Software"），这时按输入时的同一套规则重建候选
    this.input = event.target;
    // focus 事件里浏览器还没把光标落到点击位置（selectionStart 仍是 0），
    // 此刻取词必然为空，等一帧再算
    requestAnimationFrame(() => {
      if (!this.isFocus) return;
      this.refreshSuggestions();
    });
  }

  /** 多行模式下让 textarea 高度跟随内容：软换行后 scrollHeight 增长，手动撑开 */
  autoResize() {
    if (!this.multiline || !this.input) return;
    this.input.style.height = "auto";
    this.input.style.height = `${this.input.scrollHeight}px`;
  }

  handleBlur() {
    this.isFocus = false;
    this.close();
  }

  /** 同一次聚焦内点击换光标位置不会再触发 focus，这里补一次重建 */
  handleClick() {
    this.refreshSuggestions();
  }

  async handleInput(event) {
    this.input = event.target;

    if (this.multiline) this.autoResize();

    await this.refreshSuggestions();
  }

  async refreshSuggestions() {
    if (!this.input) return;

    const tags = await cache.getTags();
    // 取标签是异步的，等待期间可能已经失焦，此时不能再把面板拉回来
    if (!this.isFocus) return;

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

    // 唯一候选与当前词一模一样时没有可补的内容，弹出来只是挡视线；
    // 大小写不同仍要弹，补全能顺带修正成标签的大小写
    const nothingToComplete =
      this.suggestions.length === 1 && this.suggestions[0].name === word;

    if (word && this.suggestions.length > 0 && !nothingToComplete) {
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
          ${this.multiline
            ? html`
                <textarea
                  id="${this.inputId || nothing}"
                  name="${this.inputName || nothing}"
                  .value="${this.inputValue || ""}"
                  placeholder="${this.inputPlaceholder || " "}"
                  class="form-input"
                  rows="1"
                  autocomplete="off"
                  autocapitalize="off"
                  aria-describedby="${this.inputAriaDescribedBy || nothing}"
                  @input=${this.handleInput}
                  @keydown=${this.handleKeyDown}
                  @focus=${this.handleFocus}
                  @blur=${this.handleBlur}
                  @click=${this.handleClick}
                ></textarea>
              `
            : html`
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
                  @click=${this.handleClick}
                />
              `}
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
                  <!-- 按下即选中：触屏的兼容 mousedown 可能晚到失焦之后，那时菜单已被收起 -->
                  <a
                    href="#"
                    @pointerdown=${(event) => {
                      event.preventDefault();
                      // 菜单马上收起，补发的 click 会按坐标落到下面的行上
                      swallowNextClickIn(
                        event.currentTarget.getBoundingClientRect(),
                      );
                      this.complete(tag);
                    }}
                    @click=${(event) => event.preventDefault()}
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
