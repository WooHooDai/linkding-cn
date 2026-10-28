import { gettext, interpolate } from "../utils/i18n.js";
import { sliceRange } from "../utils/selection.js";
import { queryBookmarkList } from "../utils/bookmark-list.js";

// Binds bulk-selection state to the bookmark list DOM: renders the state onto
// the checkboxes (items, groups, page header, "All pages") and translates
// pointer gestures back into Selection calls.
//
// Gestures — one primitive (set on a slice of the display order), four mappings:
//   item click          -> toggle one
//   item click + shift  -> range from the anchor, direction from the clicked box
//   group header click  -> whole group rendered on this page
//   long press          -> toggles the anchor and arms a range, the next click
//                          closes it there. Works with touch and mouse, so the
//                          range does not require shift.

const LONG_PRESS_MS = 500;
const MOVE_TOLERANCE_PX = 10;

export class SelectionView {
  constructor(root, { getSelection, isActive, onChange }) {
    this.root = root;
    this.getSelection = getSelection;
    this.isActive = isActive;
    this.onChange = onChange || (() => {});

    // Empty layout until readLayout() runs, so run() is always safe.
    this.order = [];
    this.items = new Map();
    this.groups = [];
    this.total = 0;

    this.anchor = null;
    this.rangeArmed = null;
    this.pressTimer = null;
    this.press = null;
    this.touchPointer = false;
    this.suppressClick = false;

    this.onClick = this.onClick.bind(this);
    this.onPointerDown = this.onPointerDown.bind(this);
    this.onPointerMove = this.onPointerMove.bind(this);
    this.onPointerEnd = this.onPointerEnd.bind(this);
    this.onContextMenu = this.onContextMenu.bind(this);
  }

  // --- DOM layout ---------------------------------------------------------

  // Display order and group membership come from the rendered list only, so
  // grouping never has to be mirrored in JS state.
  readLayout() {
    const list = queryBookmarkList(this.root);
    this.list = list;
    this.order = [];
    this.items = new Map();
    this.groups = [];
    this.total = 0;
    this.allCheckbox = this.root.querySelector(".bulk-edit-checkbox.all input");
    this.selectAcross = this.root.querySelector("label.select-across");
    this.selectAcrossInput = this.selectAcross?.querySelector("input");
    this.selectAcrossTotal = this.selectAcross?.querySelector("span.total");
    this.countElement = this.root.querySelector(".bulk-edit-count");
    this.executeButton = this.root.querySelector("button[name='bulk_execute']");
    this.hint = this.root.querySelector(".selection-hint");

    if (list) {
      this.total = Number(list.dataset.bookmarksTotal || 0);
      let group = null;
      for (const li of Array.from(list.children)) {
        if (li.classList.contains("group-header")) {
          group = {
            key: li.dataset.groupKey,
            ids: [],
            label: li.querySelector(".bulk-group-checkbox"),
            input: li.querySelector(".bulk-group-checkbox input"),
          };
          this.groups.push(group);
          continue;
        }
        if (!li.hasAttribute("ld-bookmark-item")) {
          continue;
        }
        const id = li.dataset.bookmarkId;
        this.order.push(id);
        this.items.set(id, {
          el: li,
          checkbox: li.querySelector(".bulk-edit-checkbox input"),
        });
        if (group) {
          group.ids.push(id);
        }
      }
    }

    // The list was replaced, the anchor may point at a bookmark that is gone.
    if (this.anchor && !this.items.has(this.anchor)) {
      this.anchor = null;
      this._clearRangeArm();
    }
  }

  // --- Rendering ----------------------------------------------------------

  run() {
    const selection = this.getSelection();

    for (const [id, item] of this.items) {
      if (item.checkbox) {
        item.checkbox.checked = selection.isSelected(id);
      }
    }

    for (const group of this.groups) {
      if (!group.input) {
        continue;
      }
      const state = selection.triState(group.ids);
      group.input.checked = state === "all";
      group.input.indeterminate = state === "some";
      group.label?.setAttribute(
        "aria-label",
        state === "all" ? gettext("Clear group") : gettext("Select group"),
      );
    }

    if (this.allCheckbox) {
      const state = selection.triState(this.order);
      this.allCheckbox.checked = state === "all";
      this.allCheckbox.indeterminate = state === "some";
    }

    if (this.selectAcrossInput) {
      this.selectAcrossInput.checked = selection.selectAll;
    }
    if (this.selectAcrossTotal) {
      this.selectAcrossTotal.textContent = this.total;
    }

    this._renderCount(selection);
    this._renderHint();
  }

  _renderCount(selection) {
    if (!this.countElement || !this.executeButton) {
      return;
    }
    const count = selection.count(this.total);
    this.executeButton.disabled = count <= 0;
    if (count <= 0) {
      this.countElement.classList.add("d-none");
      return;
    }
    this.countElement.textContent = interpolate(
      gettext("Selected(%(count)s)"),
      { count },
    );
    this.countElement.classList.remove("d-none");
  }

  _renderHint() {
    if (!this.hint) {
      return;
    }
    if (!this.rangeArmed) {
      this.hint.classList.add("d-none");
      return;
    }
    this.hint.textContent = this.rangeArmed.checked
      ? gettext("Anchor set · tap another bookmark to select the range")
      : gettext("Anchor set · tap another bookmark to clear the range");
    this.hint.classList.toggle("is-clearing", !this.rangeArmed.checked);
    this.hint.classList.remove("d-none");
  }

  // --- Events -------------------------------------------------------------

  mount() {
    this.root.addEventListener("click", this.onClick);
    this.root.addEventListener("pointerdown", this.onPointerDown);
    this.root.addEventListener("pointermove", this.onPointerMove);
    this.root.addEventListener("pointerup", this.onPointerEnd);
    this.root.addEventListener("pointercancel", this.onPointerEnd);
    this.root.addEventListener("contextmenu", this.onContextMenu);
  }

  unmount() {
    this.root.removeEventListener("click", this.onClick);
    this.root.removeEventListener("pointerdown", this.onPointerDown);
    this.root.removeEventListener("pointermove", this.onPointerMove);
    this.root.removeEventListener("pointerup", this.onPointerEnd);
    this.root.removeEventListener("pointercancel", this.onPointerEnd);
    this.root.removeEventListener("contextmenu", this.onContextMenu);
    this._clearPress();
  }

  onClick(event) {
    const target = event.target;

    // Only real checkbox clicks count: a click on the wrapping label would
    // otherwise be handled twice, because the browser forwards it to the input.
    if (
      target.tagName !== "INPUT" ||
      target.type !== "checkbox" ||
      !this.isActive()
    ) {
      return;
    }

    // Swallow the click that belongs to the long press that armed the range.
    if (this.suppressClick) {
      this.suppressClick = false;
      event.preventDefault();
      return;
    }

    if (target.closest(".bulk-edit-checkbox.all")) {
      this.getSelection().set(this.order, target.checked);
      this.anchor = null;
      this._commit();
      return;
    }

    if (target.closest("label.select-across")) {
      this.getSelection().setAll(target.checked);
      this.anchor = null;
      this._commit();
      return;
    }

    if (target.closest(".bulk-group-checkbox")) {
      const group = this._groupFor(target);
      if (group) {
        this.getSelection().set(group.ids, target.checked);
        this.anchor = null;
        this._commit();
      }
      return;
    }

    const item = target.closest("li[ld-bookmark-item]");
    if (!item) {
      return;
    }
    this._toggleItem(item.dataset.bookmarkId, target.checked, event.shiftKey);
  }

  _toggleItem(id, checked, shiftKey) {
    if (!id) {
      return;
    }
    const selection = this.getSelection();

    if (this.rangeArmed) {
      // Touch: the long press decided the direction, the tap only closes it.
      const range = sliceRange(this.order, this.anchor, id);
      selection.set(range || [id], this.rangeArmed.checked);
      this._clearRangeArm();
      this._commit();
      return;
    }

    const ids =
      shiftKey && this.anchor ? sliceRange(this.order, this.anchor, id) : null;
    if (ids) {
      selection.set(ids, checked);
    } else {
      selection.set(id, checked);
      this.anchor = id;
    }
    this._commit();
  }

  onPointerDown(event) {
    // A new gesture means the previous long press never produced a click.
    this.suppressClick = false;
    this.touchPointer = event.pointerType !== "mouse";
    if (event.button !== 0 || !this.isActive()) {
      return;
    }
    const label = event.target.closest(".bulk-edit-checkbox");
    if (!label || label.classList.contains("all")) {
      return;
    }
    const item = label.closest("li[ld-bookmark-item]");
    if (!item) {
      return;
    }
    this.press = {
      id: item.dataset.bookmarkId,
      x: event.clientX,
      y: event.clientY,
    };
    this.pressTimer = setTimeout(() => this._armRange(), LONG_PRESS_MS);
  }

  onPointerMove(event) {
    if (!this.press) {
      return;
    }
    const dx = event.clientX - this.press.x;
    const dy = event.clientY - this.press.y;
    if (Math.hypot(dx, dy) > MOVE_TOLERANCE_PX) {
      this._clearPress();
    }
  }

  onPointerEnd(event) {
    this.touchPointer = event.pointerType !== "mouse";
    this._clearPress();
  }

  onContextMenu(event) {
    // A long press must not open the browser context menu on the checkbox.
    if (this.touchPointer && event.target.closest(".bulk-edit-checkbox")) {
      event.preventDefault();
    }
  }

  _armRange() {
    const id = this.press?.id;
    this.pressTimer = null;
    this.press = null;
    if (!id || !this.isActive()) {
      return;
    }
    const selection = this.getSelection();
    this.anchor = id;
    // The press toggles the anchor itself, so the box the user is holding on
    // already shows which direction the range will take.
    this.rangeArmed = { checked: !selection.isSelected(id) };
    selection.set(id, this.rangeArmed.checked);
    this.suppressClick = true;
    navigator.vibrate?.(10);
    this._commit();
  }

  _clearRangeArm() {
    this.rangeArmed = null;
  }

  _clearPress() {
    if (this.pressTimer) {
      clearTimeout(this.pressTimer);
      this.pressTimer = null;
    }
    this.press = null;
  }

  _groupFor(input) {
    const key = input.closest(".group-header")?.dataset.groupKey;
    return this.groups.find((group) => group.key === key);
  }

  _commit() {
    this.run();
    this.onChange();
  }

  resetAnchor() {
    this.anchor = null;
    this._clearRangeArm();
    this.run();
  }
}
