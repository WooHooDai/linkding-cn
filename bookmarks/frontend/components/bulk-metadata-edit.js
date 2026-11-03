import Sortable from "sortablejs";
import { getCSRFToken } from "../utils/csrf.js";
import { gettext, interpolate } from "../utils/i18n.js";
import { confirmDialog } from "./confirm-dialog.js";

// 输入停止多久后重新预览。批量改写不可逆，统计要跟得上手，但也不能每次按键都打库。
const PREVIEW_DELAY = 600;
// 与服务端 MAX_METADATA_REPLACE_RULES 保持一致
const MAX_RULES = 10;
const RULE_MARKERS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];
// 执行走整页提交，草稿先落盘，刷新后才能接着调
const DRAFT_KEY = "linkding:bulk-metadata-draft";

// 批量元数据改写面板：挂在批量条下方，字段之间正交，每个字段只选一种模式。
// 预览与执行共用同一份 ops，预览就是 dry run。
export class MetadataPanel {
  constructor(element, { form, onToggle } = {}) {
    this.element = element;
    this.form = form || element.closest("form");
    this.onToggle = onToggle;
    this.fields = Array.from(element.querySelectorAll("[data-field]"));
    this.warningEl = element.querySelector("[data-warning]");
    this.executeBtn = element.querySelector("[data-execute]");
    this.caseToggle = element.querySelector("[data-case-toggle]");
    this.caseCheckbox = element.querySelector("[data-case-insensitive]");

    this.previewOk = false;
    this.previewExceeded = false;
    this.previewStats = null;
    this.previewUpdated = 0;
    this.previewTotal = 0;
    this.previewTimer = null;
    this.previewAbort = null;
    this.sortables = [];
    this.abort = new AbortController();

    this._bindFields();
    this._initRuleSorting();
    this._initGutter();
    this._syncFieldInputs();
  }

  destroy() {
    clearTimeout(this.previewTimer);
    this.previewAbort?.abort();
    this.abort.abort();
    this.gutterObserver?.disconnect();
    this.sortables.forEach((sortable) => sortable.destroy());
    this.sortables = [];
  }

  // 底部按钮在滚动容器之外，要把滚动条占掉的宽度补回去才对得齐字段行
  _initGutter() {
    this.bodyEl = this.element.querySelector(".bulk-metadata-panel-body");
    this.actionsEl = this.element.querySelector(".bulk-metadata-actions");
    if (!this.bodyEl || !this.actionsEl) return;

    this.gutterObserver = new ResizeObserver(() => this._syncGutter());
    this.gutterObserver.observe(this.bodyEl);
    this._syncGutter();
  }

  _syncGutter() {
    const gutter = this.bodyEl.offsetWidth - this.bodyEl.clientWidth;
    this.actionsEl.style.paddingRight = gutter > 0 ? `${gutter}px` : "";
  }

  // --- 开关 ---------------------------------------------------------------

  open() {
    const wasHidden = this.element.hidden;
    this.element.hidden = false;
    // 已展开时不重复通知，避免调用方在 onToggle 里再调 close 造成往返
    if (wasHidden) {
      this.onToggle?.(true);
    }
    this._schedulePreview(0);
  }

  close() {
    if (this.element.hidden) {
      return;
    }
    this.element.hidden = true;
    this.onToggle?.(false);
  }

  isOpen() {
    return !this.element.hidden;
  }

  // 选区变化后重新算一遍：面板里的统计必须跟着勾选走
  refreshScope() {
    if (!this.isOpen()) return;
    // 只重算，不落草稿——此时表单可能还没回填，写了会把已有草稿冲掉
    clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => this._preview(), 0);
  }

  // --- 草稿 ---------------------------------------------------------------

  loadDraft() {
    try {
      const raw = sessionStorage.getItem(DRAFT_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  saveDraft() {
    try {
      const ops = this._collectOps();
      if (Object.keys(ops).length) {
        sessionStorage.setItem(DRAFT_KEY, JSON.stringify(ops));
      } else {
        sessionStorage.removeItem(DRAFT_KEY);
      }
    } catch (error) {
      // sessionStorage 不可用时忽略，不影响本次编辑
    }
  }

  clearDraft() {
    try {
      sessionStorage.removeItem(DRAFT_KEY);
    } catch (error) {
      // ignore
    }
  }

  // 把一份 ops 回填到表单，用于刷新后恢复草稿
  applyOps(ops) {
    if (!ops) return;

    for (const fieldEl of this.fields) {
      const field = fieldEl.dataset.field;
      const op = ops[field];

      if (field === "tags") {
        // 草稿里存的是输入框文本，后端 ops 里是数组，两种都要吃得下
        this._setTagInput(fieldEl.querySelector("[data-tag-add]"), op?.add);
        this._setTagInput(fieldEl.querySelector("[data-tag-remove]"), op?.remove);
        continue;
      }

      const modeSelect = fieldEl.querySelector("[data-mode]");
      if (!modeSelect) continue;

      let mode = op?.mode || "none";
      // 标题/备注没有「设为」，旧草稿里的该模式降级为清空
      if (mode === "set" && !modeSelect.querySelector('option[value="set"]')) {
        mode = "clear";
      }
      modeSelect.value = mode;
      this._syncFieldInputs(fieldEl);

      if (mode === "set") {
        fieldEl.querySelector("[data-value]").value = op.value || "";
      } else if (mode === "replace") {
        this._setRules(fieldEl, op.rules || []);
      }
    }

    this.caseCheckbox.checked = Object.values(ops).some(
      (op) => op?.mode === "replace" && op.case_insensitive,
    );
    this._syncCaseToggle();
    this._schedulePreview(0);
  }

  reset() {
    for (const fieldEl of this.fields) {
      if (fieldEl.dataset.field === "tags") {
        this._setTagInput(fieldEl.querySelector("[data-tag-add]"), "");
        this._setTagInput(fieldEl.querySelector("[data-tag-remove]"), "");
        continue;
      }

      const modeSelect = fieldEl.querySelector("[data-mode]");
      if (!modeSelect) continue;
      modeSelect.value = "none";
      fieldEl.querySelector("[data-value]").value = "";
      this._setRules(fieldEl, []);
    }

    this._syncFieldInputs();
    this.caseCheckbox.checked = false;
    this._syncCaseToggle();
    this.clearDraft();
    this.previewOk = false;
    this.previewStats = null;
    this.previewUpdated = 0;
    this._renderStats(null, null);
    this._syncExecute();
  }

  // --- 控件 ---------------------------------------------------------------

  _bindFields() {
    const { signal } = this.abort;

    for (const fieldEl of this.fields) {
      fieldEl.querySelector("[data-mode]")?.addEventListener(
        "change",
        () => {
          this._syncFieldInputs(fieldEl);
          this._syncCaseToggle();
          this._schedulePreview();
        },
        { signal },
      );
      fieldEl.addEventListener("input", () => this._schedulePreview(), {
        signal,
      });
      this._renumberRules(fieldEl);
    }

    // 规则行的增删集中委托，克隆出来的新行不需要重新绑定
    this.element.addEventListener(
      "click",
      (event) => {
        const addBtn = event.target.closest("[data-rule-add]");
        if (addBtn) {
          const fieldEl = addBtn.closest("[data-field]");
          if (fieldEl) this._addRule(fieldEl);
          return;
        }
        const removeBtn = event.target.closest("[data-rule-remove]");
        if (removeBtn) {
          const fieldEl = removeBtn.closest("[data-field]");
          if (fieldEl) {
            this._removeRule(fieldEl, removeBtn.closest("[data-rule]"));
          }
        }
      },
      { signal },
    );

    this.caseCheckbox?.addEventListener("change", () => this._schedulePreview(), {
      signal,
    });
    this.executeBtn?.addEventListener("click", () => this._execute(), { signal });
    this.element
      .querySelector("[data-reset]")
      ?.addEventListener("click", () => this.reset(), { signal });
  }

  _modeOf(fieldEl) {
    return fieldEl.querySelector("[data-mode]")?.value || "none";
  }

  _syncFieldInputs() {
    for (const fieldEl of this.fields) {
      const input = fieldEl.querySelector("[data-input]");
      if (!input) continue; // 标签行没有模式切换，两行输入始终可见

      const mode = this._modeOf(fieldEl);
      input.hidden = !["set", "replace"].includes(mode);
      const valueInput = fieldEl.querySelector("[data-value]");
      const replace = fieldEl.querySelector("[data-replace]");
      if (valueInput) valueInput.hidden = mode !== "set";
      if (replace) replace.hidden = mode !== "replace";
    }
  }

  _syncCaseToggle() {
    const hasReplace = this.fields.some(
      (fieldEl) => this._modeOf(fieldEl) === "replace",
    );
    this.caseToggle.hidden = !hasReplace;
    if (!hasReplace && this.caseCheckbox) {
      this.caseCheckbox.checked = false;
    }
  }

  _addRule(fieldEl) {
    const rules = fieldEl.querySelector("[data-rules]");
    const rows = rules.querySelectorAll("[data-rule]");
    if (rows.length >= MAX_RULES) {
      return;
    }

    const row = rows[0].cloneNode(true);
    row.querySelectorAll("input").forEach((input) => (input.value = ""));
    rules.appendChild(row);
    this._renumberRules(fieldEl);
    row.querySelector("[data-find]").focus();
    this._schedulePreview();
  }

  _removeRule(fieldEl, row) {
    const rows = fieldEl.querySelectorAll("[data-rule]");
    if (rows.length <= 1) {
      // 最后一行保留结构，只清空内容
      row.querySelectorAll("input").forEach((input) => (input.value = ""));
    } else {
      row.remove();
    }
    this._renumberRules(fieldEl);
    this._schedulePreview();
  }

  _setRules(fieldEl, rules) {
    const list = fieldEl.querySelector("[data-rules]");
    if (!list) return;

    const rows = Array.from(list.querySelectorAll("[data-rule]"));
    const target = Math.max(1, rules.length);
    while (rows.length < target) {
      const row = rows[0].cloneNode(true);
      row.querySelectorAll("input").forEach((input) => (input.value = ""));
      list.appendChild(row);
      rows.push(row);
    }
    while (rows.length > target) {
      rows.pop().remove();
    }

    rows.forEach((row, index) => {
      const rule = rules[index];
      row.querySelector("[data-find]").value = rule?.find || "";
      row.querySelector("[data-replacement]").value = rule?.replace || "";
    });
    this._renumberRules(fieldEl);
  }

  _renumberRules(fieldEl) {
    const rows = Array.from(fieldEl.querySelectorAll("[data-rule]"));
    rows.forEach((row, index) => {
      row.querySelector("[data-rule-number]").textContent = String(index + 1);
      row.querySelector("[data-rule-remove]").hidden = rows.length <= 1;
    });

    const addBtn = fieldEl.querySelector("[data-rule-add]");
    if (addBtn) {
      addBtn.disabled = rows.length >= MAX_RULES;
    }
  }

  // 序号即手柄：拖动改变执行顺序，拖完重排序号并重新预览
  _initRuleSorting() {
    this.sortables = [];
    for (const fieldEl of this.fields) {
      const list = fieldEl.querySelector("[data-rules]");
      if (!list) continue;

      this.sortables.push(
        Sortable.create(list, {
          draggable: "[data-rule]",
          handle: "[data-rule-handle]",
          animation: 150,
          ghostClass: "is-dragging",
          onEnd: () => {
            this._renumberRules(fieldEl);
            this._schedulePreview();
          },
        }),
      );
    }
  }

  // --- 收集 ops -----------------------------------------------------------

  _collectOps() {
    const ops = {};
    const caseInsensitive = !!this.caseCheckbox?.checked;

    for (const fieldEl of this.fields) {
      const field = fieldEl.dataset.field;

      if (field === "tags") {
        const add = this._tagValue(fieldEl.querySelector("[data-tag-add]"));
        const remove = this._tagValue(fieldEl.querySelector("[data-tag-remove]"));
        if (add || remove) ops.tags = { add, remove };
        continue;
      }

      const mode = this._modeOf(fieldEl);
      if (mode === "none") continue;

      if (mode === "set") {
        // 固定值留空交给"清空"，避免两种模式语义重叠
        const value = fieldEl.querySelector("[data-value]").value;
        if (value) ops[field] = { mode: "set", value };
      } else if (mode === "replace") {
        const rules = Array.from(fieldEl.querySelectorAll("[data-rule]"))
          .map((row) => ({
            find: row.querySelector("[data-find]").value,
            replace: row.querySelector("[data-replacement]").value,
          }))
          .filter((rule) => rule.find);
        if (!rules.length) continue;
        ops[field] = {
          mode: "replace",
          case_insensitive: caseInsensitive,
          rules,
        };
      } else {
        ops[field] = { mode };
      }
    }

    return ops;
  }

  _tagValue(container) {
    const input = container?.querySelector("input");
    return (input?.value || "").trim();
  }

  _setTagInput(container, value) {
    const input = container?.querySelector("input");
    if (!input) return;
    input.value = Array.isArray(value) ? value.join(" ") : value || "";
  }

  // --- 预览 ---------------------------------------------------------------

  _schedulePreview(delay = PREVIEW_DELAY) {
    clearTimeout(this.previewTimer);
    // 翻页、刷新随时可能发生，输入即落盘，不等预览
    this.saveDraft();
    this.previewTimer = setTimeout(() => this._preview(), delay);
  }

  async _preview() {
    const ops = this._collectOps();
    if (!Object.keys(ops).length) {
      this.previewOk = false;
      this.previewExceeded = false;
      this.previewUpdated = 0;
      this._renderStats(null, null);
      this._renderWarning(null);
      this._syncExecute();
      return;
    }

    this.previewAbort?.abort();
    const controller = new AbortController();
    this.previewAbort = controller;

    const data = new FormData(this.form);
    data.set("mode", this._mode());
    data.set("bulk_metadata_ops", JSON.stringify(ops));

    try {
      const response = await fetch(this.element.dataset.previewUrl, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
        body: data,
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (controller.signal.aborted) return;

      this.previewOk = true;
      this.previewExceeded = !!payload.exceeded;
      this.previewStats = payload.stats || null;
      this.previewUpdated = payload.stats?.updated || 0;
      this.previewTotal = payload.stats?.total || 0;
      this._renderStats(payload.stats, ops);
      this._renderWarning(payload);
    } catch (error) {
      if (error.name === "AbortError") return;
      this.previewOk = false;
      this.previewExceeded = false;
      this.previewUpdated = 0;
      this._renderStats(null, null);
      this._renderWarning(null);
    }

    this._syncExecute();
  }

  _renderStats(stats, ops) {
    for (const fieldEl of this.fields) {
      const field = fieldEl.dataset.field;
      const statEl = fieldEl.querySelector("[data-stat]");
      if (!stats) {
        statEl.textContent = "";
        statEl.title = "";
        continue;
      }

      if (field === "tags") {
        if (!ops?.tags) {
          statEl.textContent = "";
          continue;
        }
        // 标签按增删的关联数计"处"，与文本字段的统计口径一致
        const count = (stats.tags?.added || 0) + (stats.tags?.removed || 0);
        statEl.textContent = interpolate(gettext("Changes: %(count)s"), {
          count,
        });
        continue;
      }

      if (!(field in (stats.fields || {}))) {
        statEl.textContent = "";
        statEl.title = "";
        continue;
      }

      const count = stats.fields[field];
      let text = interpolate(gettext("Changes: %(count)s"), { count });
      const steps = stats.steps?.[field];

      if (steps?.length) {
        // 分步命中数放 title，主文案只提示"哪条规则没起作用"
        statEl.title = steps
          .map((hits, index) => `${index + 1}: ${hits}`)
          .join(" 　");
        const missed = steps
          .map((hits, index) => (hits === 0 ? RULE_MARKERS[index] : null))
          .filter((marker) => marker !== null);
        if (missed.length && count > 0) {
          text +=
            " · " +
            interpolate(gettext("%(rules)s matched nothing"), {
              rules: missed.join(""),
            });
        }
      } else {
        statEl.title = "";
      }

      statEl.textContent = text;
    }
  }

  _renderWarning(payload) {
    if (!payload?.exceeded) {
      this.warningEl.hidden = true;
      this.warningEl.textContent = "";
      return;
    }
    this.warningEl.textContent = interpolate(
      gettext(
        "Selected %(count)s bookmarks exceed the limit of %(limit)s. Narrow the selection and try again.",
      ),
      { count: payload.count, limit: payload.limit },
    );
    this.warningEl.hidden = false;
  }

  // 每个生效字段各自的书签数，用于确认弹窗里的分项说明
  _fieldCounts(ops) {
    const counts = [];
    for (const fieldEl of this.fields) {
      const field = fieldEl.dataset.field;
      if (!ops[field]) continue;

      const count =
        field === "tags"
          ? this.previewStats?.tags?.bookmarks || 0
          : this.previewStats?.fields?.[field] || 0;
      if (!count) continue;

      counts.push({
        label: fieldEl.querySelector(".bulk-metadata-label").textContent.trim(),
        count,
      });
    }
    return counts;
  }

  _renderScope() {
    this.scopeEl.textContent = interpolate(
      gettext("Applies to %(count)s bookmarks"),
      { count: this.scopeCount },
    );
  }

  _syncExecute() {
    const hasOps = Object.keys(this._collectOps()).length > 0;
    // 预览显示 0 处变化时执行没有意义，直接禁用
    this.executeBtn.disabled =
      !hasOps ||
      !this.previewOk ||
      this.previewExceeded ||
      this.previewUpdated <= 0;
  }

  // --- 执行 ---------------------------------------------------------------

  _mode() {
    const action = this.form?.getAttribute("action") || "";
    return action.includes("/archived/action") ? "archived" : "index";
  }

  async _execute() {
    if (this.executeBtn.disabled || !this.form) return;
    const ops = this._collectOps();
    if (!Object.keys(ops).length) return;

    // 改写不可撤销，落库前必须让用户看清作用范围和每个字段的影响面
    const details = this._fieldCounts(ops)
      .map(({ label, count }) =>
        interpolate(gettext("%(field)s of %(count)s bookmarks"), {
          field: label,
          count,
        }),
      )
      .join(", ");

    const confirmed = await confirmDialog({
      title: gettext("Apply changes?"),
      warning: gettext("This cannot be undone!"),
      message: interpolate(gettext("This will change: %(details)s."), {
        details,
      }),
      confirmLabel: gettext("Confirm"),
      cancelLabel: gettext("Cancel"),
    });
    if (!confirmed) return;

    this.saveDraft();

    this._removeHidden("bulk_metadata_ops");
    this._removeHidden("bulk_execute");
    this._appendHidden("bulk_metadata_ops", JSON.stringify(ops));
    this._appendHidden("bulk_execute", "1");

    // 标记后 onSubmit 会跳过"打开面板"，直接走原有批量提交链路
    this.form.dataset.metadataSubmitting = "1";
    this.form.requestSubmit();
  }

  _removeHidden(name) {
    this.form
      .querySelectorAll(`input[name="${name}"]`)
      .forEach((element) => element.remove());
  }

  _appendHidden(name, value) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    this.form.appendChild(input);
  }
}

export default MetadataPanel;
