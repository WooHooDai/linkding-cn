import { Behavior, registerBehavior } from "./runtime.js";
import { gettext, interpolate } from "../utils/i18n.js";

const STORAGE_KEY = "linkding:bulk-edit";

class BulkEdit extends Behavior {
  constructor(element) {
    super(element);

    this.active = element.classList.contains("active");

    this.init = this.init.bind(this);
    this.onToggleActive = this.onToggleActive.bind(this);
    this.onToggleAll = this.onToggleAll.bind(this);
    this.onToggleBookmark = this.onToggleBookmark.bind(this);
    this.onToggleSelectAcross = this.onToggleSelectAcross.bind(this);
    this.onActionSelected = this.onActionSelected.bind(this);
    this.onSubmit = this.onSubmit.bind(this);

    this.isStickyOn =
      element.querySelector(".section-header")?.dataset.stickyOn === "true";
    this.bulkEditBar = element.querySelector(".bulk-edit-bar");

    // 初始状态：页面加载时如已激活，同步粘性类
    if (this.isStickyOn) {
      this.bulkEditBar.classList.add("sticky");
    }

    this.init();
    // Reset when bookmarks are updated
    document.addEventListener("bookmark-list-updated", this.init);
  }

  destroy() {
    this.removeListeners();
    document.removeEventListener("bookmark-list-updated", this.init);
  }

  init() {
    // Update elements
    this.activeToggle = this.element.querySelector(".bulk-edit-active-toggle");
    this.actionSelect = this.element.querySelector(
      "select[name='bulk_action']",
    );
    this.tagAutoComplete = this.element.querySelector(".tag-autocomplete");
    this.executeButton = this.element.querySelector(
      "button[name='bulk_execute']",
    );
    this.cancelButton = this.element.querySelector(
      "button[name='bulk_cancel']",
    );
    this.selectAcross = this.element.querySelector("label.select-across");
    this.selectAcrossInput = this.selectAcross.querySelector("input");
    this.allCheckbox = this.element.querySelector(
      ".bulk-edit-checkbox.all input",
    );
    this.bookmarkCheckboxes = Array.from(
      this.element.querySelectorAll(".bulk-edit-checkbox:not(.all) input"),
    );

    this.form = this.element.querySelector("form.bookmark-actions");
    this.countElement = this.element.querySelector(".bulk-edit-count");

    // Add listeners, ensure there are no dupes by possibly removing existing listeners
    this.removeListeners();
    this.addListeners();

    // Update total number of bookmarks
    const totalHolder = this.element.querySelector("[data-bookmarks-total]");
    this.total = totalHolder?.dataset.bookmarksTotal || 0;
    const totalSpan = this.selectAcross.querySelector("span.total");
    totalSpan.textContent = this.total;

    // Restore saved state from sessionStorage
    this.restoreState();
  }

  addListeners() {
    this.activeToggle.addEventListener("click", this.onToggleActive);
    this.cancelButton.addEventListener("click", this.onToggleActive);
    this.actionSelect.addEventListener("change", this.onActionSelected);
    this.allCheckbox.addEventListener("change", this.onToggleAll);
    this.selectAcrossInput.addEventListener(
      "change",
      this.onToggleSelectAcross,
    );
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.addEventListener("change", this.onToggleBookmark);
    });
    if (this.form) {
      this.form.addEventListener("submit", this.onSubmit);
    }
  }

  removeListeners() {
    this.activeToggle.removeEventListener("click", this.onToggleActive);
    this.cancelButton.removeEventListener("click", this.onToggleActive);
    this.actionSelect.removeEventListener("change", this.onActionSelected);
    this.allCheckbox.removeEventListener("change", this.onToggleAll);
    this.selectAcrossInput.removeEventListener(
      "change",
      this.onToggleSelectAcross,
    );
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.removeEventListener("change", this.onToggleBookmark);
    });
    if (this.form) {
      this.form.removeEventListener("submit", this.onSubmit);
    }
  }

  onToggleActive() {
    this.active = !this.active;
    if (this.active) {
      this.element.classList.add("active");
      if (this.isStickyOn) {
        this.bulkEditBar.classList.add("sticky");
      }
      // "All pages" option is always available while bulk editing
      this.selectAcross.classList.remove("d-none");
    } else {
      this.element.classList.remove("active");
      if (this.isStickyOn) {
        this.bulkEditBar.classList.remove("sticky");
      }
      this.clearState();
    }
  }

  onSubmit() {
    this._injectHiddenFields();
    this.clearState();
  }

  onToggleBookmark(event) {
    const checkbox = event.target;
    const id = checkbox.value;
    const state = this._loadState() || this._emptyState();

    if (state.selectAll) {
      // Select-all mode: an unchecked bookmark is recorded as an exclusion
      const excludedIds = new Set(state.excludedIds);
      if (checkbox.checked) {
        excludedIds.delete(id);
      } else if (!excludedIds.has(id)) {
        excludedIds.add(id);
      }
      state.excludedIds = Array.from(excludedIds);
    } else {
      const selectedIds = new Set(state.selectedIds);
      if (checkbox.checked) {
        selectedIds.add(id);
      } else {
        selectedIds.delete(id);
      }
      state.selectedIds = Array.from(selectedIds);
    }
    this._saveState(state);

    this._syncHeaderCheckbox(state);
    this._updateSelectAcrossInput(state);
    this._updateExecuteState();
  }

  onToggleAll() {
    const allChecked = this.allCheckbox.checked;
    const state = this._loadState() || this._emptyState();
    const pageIds = this.bookmarkCheckboxes.map((cb) => cb.value);

    if (state.selectAll) {
      // Select-all mode: toggling the header excludes/re-includes the whole page
      const excludedIds = new Set(state.excludedIds);
      if (allChecked) {
        // Re-include the whole page
        pageIds.forEach((id) => excludedIds.delete(id));
      } else {
        // Exclude the whole page
        pageIds.forEach((id) => excludedIds.add(id));
      }
      state.excludedIds = Array.from(excludedIds);
    } else {
      // Individual mode: toggling the header selects/deselects the whole page
      const selectedIds = new Set(state.selectedIds);
      if (allChecked) {
        pageIds.forEach((id) => selectedIds.add(id));
      } else {
        pageIds.forEach((id) => selectedIds.delete(id));
      }
      state.selectedIds = Array.from(selectedIds);
    }
    this._saveState(state);

    this._syncPageCheckboxes(state);
    // Header reflects the toggle the user just made
    this.allCheckbox.checked = allChecked;
    this._updateSelectAcrossInput(state);
    this._updateExecuteState();
  }

  onToggleSelectAcross() {
    // "All pages" checkbox:
    // - checked   -> select every bookmark (when any bookmark is not selected yet)
    // - unchecked -> deselect every bookmark (when all bookmarks are selected)
    const selectAll = this.selectAcrossInput.checked;
    const state = this._emptyState();

    state.selectAll = selectAll;

    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.checked = selectAll;
    });
    this.allCheckbox.checked = selectAll;

    this._saveState(state);
    this._updateExecuteState();
  }

  onActionSelected() {
    const action = this.actionSelect.value;

    if (action === "bulk_tag" || action === "bulk_untag") {
      this.tagAutoComplete.classList.remove("d-none");
    } else {
      this.tagAutoComplete.classList.add("d-none");
    }
  }

  reset() {
    this.allCheckbox.checked = false;
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.checked = false;
    });
    this.selectAcrossInput.checked = false;
    this._updateExecuteState();
  }

  _updateExecuteState() {
    if (!this.executeButton) {
      return;
    }
    const count = this._count();
    this.executeButton.disabled = count <= 0;
    this._updateCount(count);
  }

  _updateCount(count) {
    if (!this.countElement) return;
    if (count > 0) {
      this.countElement.textContent = interpolate(
        gettext("Selected(%(count)s)"),
        { count },
      );
      this.countElement.classList.remove("d-none");
    } else {
      this.countElement.classList.add("d-none");
    }
  }

  // --- State persistence ---

  _emptyState() {
    return { selectAll: false, selectedIds: [], excludedIds: [] };
  }

  _loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        return {
          selectAll: !!parsed.selectAll,
          selectedIds: parsed.selectedIds || [],
          excludedIds: parsed.excludedIds || [],
        };
      }
    } catch (e) {
      // ignore
    }
    return null;
  }

  _saveState(state) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // ignore
    }
  }

  clearState() {
    sessionStorage.removeItem(STORAGE_KEY);
  }

  restoreState() {
    const state = this._loadState();

    if (
      !state ||
      (!state.selectAll &&
        state.selectedIds.length === 0 &&
        state.excludedIds.length === 0)
    ) {
      // No saved state, reset checkboxes
      this.reset();
      return;
    }

    // Restore active mode
    this.active = true;
    this.element.classList.add("active");
    if (this.isStickyOn) {
      this.bulkEditBar.classList.add("sticky");
    }
    this.selectAcross.classList.remove("d-none");

    this._syncPageCheckboxes(state);
    this._syncHeaderCheckbox(state);
    this._updateSelectAcrossInput(state);
    this._updateExecuteState();
  }

  // --- Selection helpers ---

  _count() {
    const state = this._loadState();
    if (!state) return 0;
    if (state.selectAll) {
      return Math.max(0, this.total - state.excludedIds.length);
    }
    return state.selectedIds.length;
  }

  _isChecked(cb, state) {
    return state.selectAll
      ? !state.excludedIds.includes(cb.value)
      : state.selectedIds.includes(cb.value);
  }

  _syncPageCheckboxes(state) {
    this.bookmarkCheckboxes.forEach((cb) => {
      cb.checked = this._isChecked(cb, state);
    });
  }

  _syncHeaderCheckbox(state) {
    this.allCheckbox.checked =
      this.bookmarkCheckboxes.length > 0 &&
      this.bookmarkCheckboxes.every((cb) => this._isChecked(cb, state));
  }

  _updateSelectAcrossInput(state) {
    // The "All pages" checkbox reflects whether every bookmark is selected.
    this.selectAcrossInput.checked = !!state && !!state.selectAll;
  }

  // --- Form submission ---

  _injectHiddenFields() {
    if (!this.form) return;
    // Drop previously injected fields
    this.form
      .querySelectorAll("input[data-bulk-sync]")
      .forEach((el) => el.remove());

    const state = this._loadState();
    if (!state) return;

    if (state.selectAll) {
      // Select-all mode: send the exclusion list alongside bulk_select_across
      state.excludedIds.forEach((id) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "bulk_exclude_id";
        input.value = id;
        input.dataset.bulkSync = "1";
        this.form.appendChild(input);
      });
    } else {
      // Individual mode: send every selected bookmark id, including those on
      // pages that are not currently rendered. Ids rendered as checkboxes on
      // the current page are already submitted by those checkboxes, so skip
      // them to avoid duplicates.
      const renderedIds = new Set(this.bookmarkCheckboxes.map((cb) => cb.value));
      state.selectedIds.forEach((id) => {
        if (renderedIds.has(id)) {
          return;
        }
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "bookmark_id";
        input.value = id;
        input.dataset.bulkSync = "1";
        this.form.appendChild(input);
      });
    }
  }
}

registerBehavior("ld-bulk-edit", BulkEdit);
