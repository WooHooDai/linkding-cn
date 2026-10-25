// Selection state for bulk edit: pure data, no DOM, no storage.
// Every bulk gesture (item, group, page, shift range, all pages) ends up as a
// set()/setAll() call on this object; the view only renders its state.

// Range of ids between two endpoints in display order, or null if either
// endpoint is not part of the order (e.g. after the list was replaced).
export function sliceRange(orderedIds, fromId, toId) {
  const from = orderedIds.indexOf(fromId);
  const to = orderedIds.indexOf(toId);
  if (from === -1 || to === -1) return null;
  return orderedIds.slice(Math.min(from, to), Math.max(from, to) + 1);
}

export class Selection {
  constructor({ selectAll = false, selectedIds = [], excludedIds = [] } = {}) {
    this.selectAll = selectAll;
    this.selected = new Set(selectedIds.map(String));
    this.excluded = new Set(excludedIds.map(String));
  }

  get isEmpty() {
    return !this.selectAll && this.selected.size === 0;
  }

  count(total) {
    return this.selectAll
      ? Math.max(0, total - this.excluded.size)
      : this.selected.size;
  }

  isSelected(id) {
    return this.selectAll
      ? !this.excluded.has(String(id))
      : this.selected.has(String(id));
  }

  // "none" | "some" | "all" — the single place that derives tri-state, shared
  // by the page header and every group header.
  triState(ids) {
    if (ids.length === 0) return "none";
    let selected = 0;
    for (const id of ids) {
      if (this.isSelected(id)) selected++;
    }
    if (selected === 0) return "none";
    return selected === ids.length ? "all" : "some";
  }

  set(ids, checked) {
    const list = Array.isArray(ids) ? ids : [ids];
    if (this.selectAll) {
      // In select-all mode unchecking records an exclusion, checking drops it
      for (const id of list) {
        if (checked) this.excluded.delete(String(id));
        else this.excluded.add(String(id));
      }
    } else {
      for (const id of list) {
        if (checked) this.selected.add(String(id));
        else this.selected.delete(String(id));
      }
    }
    return this;
  }

  toggle(id) {
    const checked = !this.isSelected(id);
    this.set(id, checked);
    return checked;
  }

  // "All pages" collapses the selection into a single mode: the excluded set is
  // the only meaningful state, so any materialised ids are dropped.
  setAll(selectAll) {
    this.selectAll = !!selectAll;
    this.selected.clear();
    this.excluded.clear();
    return this;
  }

  // Form payload: rendered ids are already submitted by their own checkboxes.
  payload(renderedIds) {
    if (this.selectAll) {
      return { selectAcross: true, ids: [], excludeIds: [...this.excluded] };
    }
    const rendered = new Set(renderedIds);
    return {
      selectAcross: false,
      ids: [...this.selected].filter((id) => !rendered.has(id)),
      excludeIds: [],
    };
  }

  toJSON() {
    return {
      v: 2,
      selectAll: this.selectAll,
      selectedIds: [...this.selected],
      excludedIds: [...this.excluded],
    };
  }

  static from(raw) {
    if (!raw || typeof raw !== "object") return new Selection();
    return new Selection({
      selectAll: !!raw.selectAll,
      selectedIds: raw.selectedIds || [],
      excludedIds: raw.excludedIds || [],
    });
  }
}
