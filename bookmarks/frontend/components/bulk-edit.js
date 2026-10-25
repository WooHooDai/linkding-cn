import { Behavior, registerBehavior } from "./runtime.js";
import { Selection } from "../utils/selection.js";
import { SelectionView } from "./selection-view.js";

const STORAGE_KEY = "linkding:bulk-edit";

// List-removing actions clear the selection; others keep it for the next op.
const REMOVING_ACTIONS = new Set([
  "bulk_archive",
  "bulk_unarchive",
  "bulk_trash",
  "bulk_restore",
  "bulk_delete",
]);

// Bulk edit shell: owns the mode (active), the selection state and the form
// payload. Rendering and gestures live in SelectionView, the state itself in
// Selection. Controls and checkboxes are handled by delegation on the root
// element, so a replaced list needs no listener rebinding.
class BulkEdit extends Behavior {
  constructor(element) {
    super(element);

    this.active = element.classList.contains("active");
    this.isStickyOn =
      element.querySelector(".section-header")?.dataset.stickyOn === "true";
    this.bulkEditBar = element.querySelector(".bulk-edit-bar");
    this.selection = Selection.from(this._loadState());

    this.view = new SelectionView(element, {
      getSelection: () => this.selection,
      isActive: () => this.active,
      onChange: () => this._persist(),
    });

    this.onToggleActive = this.onToggleActive.bind(this);
    this.onActionSelected = this.onActionSelected.bind(this);
    this.onSubmit = this.onSubmit.bind(this);
    this.refresh = this.refresh.bind(this);

    this._bindControls();
    this.view.mount();
    this.refresh();
    this._restore();

    document.addEventListener("bookmark-list-updated", this.refresh);
  }

  destroy() {
    document.removeEventListener("bookmark-list-updated", this.refresh);
    this._unbindControls();
    this.view.unmount();
  }

  // Re-read the list after it was replaced (turbo stream, pagination, filter).
  refresh() {
    this.view.readLayout();
    this.view.run();
  }

  // --- Mode ---------------------------------------------------------------

  _bindControls() {
    this.element.addEventListener("click", this.onToggleActive);
    this.element.addEventListener("change", this.onActionSelected);
    this.element.addEventListener("submit", this.onSubmit);
  }

  _unbindControls() {
    this.element.removeEventListener("click", this.onToggleActive);
    this.element.removeEventListener("change", this.onActionSelected);
    this.element.removeEventListener("submit", this.onSubmit);
  }

  onToggleActive(event) {
    const toggle = event.target.closest(
      ".bulk-edit-active-toggle, [name='bulk_cancel']",
    );
    if (toggle) {
      this._setActive(!this.active);
    }
  }

  _setActive(active) {
    this.active = active;
    this.element.classList.toggle("active", active);
    if (this.isStickyOn) {
      this.bulkEditBar?.classList.toggle("sticky", active);
    }
    this.view.selectAcross?.classList.toggle("d-none", !active);
    if (active) {
      this.view.run();
      return;
    }
    this.selection = new Selection();
    this.view.resetAnchor();
    this._clearState();
  }

  onActionSelected(event) {
    if (!event.target.matches("select[name='bulk_action']")) {
      return;
    }
    const tagAutocomplete = this.element.querySelector(".tag-autocomplete");
    if (!tagAutocomplete) {
      return;
    }
    const action = event.target.value;
    tagAutocomplete.classList.toggle(
      "d-none",
      action !== "bulk_tag" && action !== "bulk_untag",
    );
  }

  // --- Form ---------------------------------------------------------------

  onSubmit(event) {
    if (!event.target.matches("form.bookmark-actions")) {
      return;
    }
    this._injectHiddenFields(event.target);
    const action = this.element.querySelector(
      "select[name='bulk_action']",
    )?.value;
    if (REMOVING_ACTIONS.has(action)) {
      // Clear the state only — the DOM still has to submit its checkbox values.
      this.selection = new Selection();
      this._clearState();
    }
  }

  _injectHiddenFields(form) {
    form
      .querySelectorAll("input[data-bulk-sync]")
      .forEach((element) => element.remove());

    const { selectAcross, ids, excludeIds } = this.selection.payload(
      this.view.order,
    );
    if (selectAcross) {
      this._appendHiddenFields(form, "bulk_exclude_id", excludeIds);
    } else {
      this._appendHiddenFields(form, "bookmark_id", ids);
    }
  }

  _appendHiddenFields(form, name, values) {
    for (const value of values) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      input.dataset.bulkSync = "1";
      form.appendChild(input);
    }
  }

  // --- State persistence --------------------------------------------------

  _loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) {
        return JSON.parse(raw);
      }
    } catch (e) {
      // ignore
    }
    return null;
  }

  _persist() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.selection));
    } catch (e) {
      // ignore
    }
  }

  _clearState() {
    sessionStorage.removeItem(STORAGE_KEY);
  }

  // A saved selection survives a reload; it also implies bulk mode is on.
  _restore() {
    if (this.selection.isEmpty) {
      return;
    }
    this._setActive(true);
  }
}

registerBehavior("ld-bulk-edit", BulkEdit);
