import { BulkEdit } from "./bulk-edit.js";
import { registerBehavior } from "./runtime.js";
import { gettext, interpolate } from "../utils/i18n.js";
import { getCSRFToken } from "../utils/csrf.js";
import { renderCopyText } from "../utils/highlight-copy-format.js";
import { showToast } from "./toast.js";

const DEFAULT_ITEM_FORMAT = "> ${highlight}\n\n${annotation}";
const DEFAULT_SEPARATOR = "\n\n---\n\n";
const COPY_ACTIONS = new Set([
  "bulk_copy_highlight",
  "bulk_copy_note",
  "bulk_copy_all",
]);

function readJsonScript(id, fallback) {
  const el = document.getElementById(id);
  if (!el) return fallback;
  try {
    return JSON.parse(el.textContent) || fallback;
  } catch (error) {
    return fallback;
  }
}

// 高亮页的批量编辑：选择/提交/记忆全部复用 BulkEdit，这里只补三件页面自己的事——
// 颜色选择器的显隐、带条数的确认文案、以及复制（要跨页取文本，只能问后端）。
class HighlightsBulkEdit extends BulkEdit {
  constructor(element) {
    super(element);

    this.colorSelect = element.querySelector("[data-bulk-color]");
    this.copyUrl = element.dataset.bulkCopyUrl || "";
    this.copyConfig = {
      itemFormat: readJsonScript("ld-hl-item-format", DEFAULT_ITEM_FORMAT),
      separator: readJsonScript("ld-hl-separator", DEFAULT_SEPARATOR),
    };

    this.onCopySettings = this.onCopySettings.bind(this);
    document.addEventListener("highlights-copy-settings", this.onCopySettings);

    // 父类构造时 action 已经还原过了，那时这些字段还不存在，补一次同步
    this._afterActionSync(this._currentAction());
  }

  destroy() {
    document.removeEventListener(
      "highlights-copy-settings",
      this.onCopySettings,
    );
    super.destroy();
  }

  // 布局抽屉里改了复制格式：就地更新，不用重载页面
  onCopySettings(event) {
    const detail = event.detail || {};
    if (detail.field === "item_format") {
      this.copyConfig.itemFormat = detail.value || DEFAULT_ITEM_FORMAT;
    } else if (detail.field === "separator") {
      this.copyConfig.separator = detail.value || DEFAULT_SEPARATOR;
    }
  }

  _afterActionSync(action) {
    if (this.colorSelect) {
      this.colorSelect.hidden = action !== "bulk_change_color";
    }
    this._syncConfirmQuestion(action);
  }

  _onSelectionChange() {
    this._syncConfirmQuestion(this._currentAction());
  }

  _skipConfirm(action) {
    return COPY_ACTIONS.has(action);
  }

  // 表单靠隐藏的 action 字段分派，批量执行时写回 bulk_execute
  _prepareSubmit(form, action) {
    if (!this.config.actionField) return;
    const input = form.querySelector(
      `input[name='${this.config.actionField}']`,
    );
    if (input) {
      input.value = this.config.actionValue || action;
    }
  }

  _handleLocalAction(form, action) {
    if (!COPY_ACTIONS.has(action)) {
      return false;
    }
    this._copySelection(form, action);
    return true;
  }

  _syncConfirmQuestion(action) {
    const button = this.element.querySelector("button[name='bulk_execute']");
    if (!button || COPY_ACTIONS.has(action)) {
      return;
    }

    const count = this.selection.count(this.view.total);
    // 单复数各写一条 msgid：JS catalog 的 ngettext 只认 msgid_plural 条目，
    // 这里两条是独立词条，直接按 count 选一条更可控
    const plural = (one, many) =>
      count === 1 ? gettext(one) : interpolate(gettext(many), { count });

    let question = gettext("Are you sure?");
    if (action === "bulk_delete") {
      question = plural(
        "Delete this highlight?",
        "Delete %(count)s highlights?",
      );
    } else if (action === "bulk_clear_note") {
      question = plural(
        "Clear the annotation of this highlight?",
        "Clear the annotations of %(count)s highlights?",
      );
    } else if (action === "bulk_change_color") {
      question = plural(
        "Change the color of this highlight?",
        "Change the color of %(count)s highlights?",
      );
    }
    button.setAttribute("ld-confirm-question", question);
    button.toggleAttribute("ld-confirm-danger", action === "bulk_delete");
  }

  // 复制只在浏览器里能完成（格式、剪贴板），但选中项可能散在多页，
  // 所以文本向后端要，渲染仍按当前设置走。
  async _copySelection(form, action) {
    if (!this.copyUrl) {
      return;
    }

    try {
      const response = await fetch(`${this.copyUrl}${window.location.search}`, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
        body: new FormData(form),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      const text = this._renderItems(payload.items || [], action);
      if (!text) {
        showToast(gettext("Nothing to copy"), { tone: "info" });
        return;
      }
      await navigator.clipboard.writeText(text);
      showToast(gettext("Copied!"), { tone: "success" });
    } catch (error) {
      showToast(gettext("Copy failed"), { tone: "error" });
    }
  }

  _renderItems(items, action) {
    const itemFormat = this.copyConfig.itemFormat;
    const hasHighlight = itemFormat.includes("${highlight}");
    const hasAnnotation = itemFormat.includes("${annotation}");

    return items
      .map(({ text, note }) => {
        if (action === "bulk_copy_highlight") {
          return renderCopyText(
            hasAnnotation ? "> ${highlight}" : itemFormat,
            text,
            "",
          );
        }
        if (action === "bulk_copy_note") {
          return renderCopyText(
            hasHighlight ? "${annotation}" : itemFormat,
            "",
            note,
          );
        }
        return renderCopyText(itemFormat, text, note);
      })
      .filter((part) => part.trim())
      .join(this.copyConfig.separator);
  }
}

registerBehavior("ld-highlights-bulk-edit", HighlightsBulkEdit);

export { HighlightsBulkEdit };
