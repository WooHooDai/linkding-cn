import { Behavior, registerBehavior } from "./runtime.js";

class BulkEdit extends Behavior {
  constructor(element) {
    super(element);

    this.active = element.classList.contains("active");

    this.init = this.init.bind(this);
    this.onToggleActive = this.onToggleActive.bind(this);
    this.onToggleAll = this.onToggleAll.bind(this);
    this.onToggleBookmark = this.onToggleBookmark.bind(this);
    this.onActionSelected = this.onActionSelected.bind(this);

    this.isStickyOn = element.querySelector(".section-header")?.dataset.stickyOn === 'true'
    this.bulkEditBar = element.querySelector('.bulk-edit-bar');

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
    this.executeButton = this.element.querySelector("button[name='bulk_execute']");
    this.cancelButton = this.element.querySelector("button[name='bulk_cancel']");
    this.selectAcross = this.element.querySelector("label.select-across");
    this.allCheckbox = this.element.querySelector(
      ".bulk-edit-checkbox.all input",
    );
    this.bookmarkCheckboxes = Array.from(
      this.element.querySelectorAll(".bulk-edit-checkbox:not(.all) input"),
    );

    // Add listeners, ensure there are no dupes by possibly removing existing listeners
    this.removeListeners();
    this.addListeners();

    // Reset checkbox states
    this.reset();

    // Update total number of bookmarks
    const totalHolder = this.element.querySelector("[data-bookmarks-total]");
    const total = totalHolder?.dataset.bookmarksTotal || 0;
    const totalSpan = this.selectAcross.querySelector("span.total");
    totalSpan.textContent = total;
  }

  addListeners() {
    this.activeToggle.addEventListener("click", this.onToggleActive);
    this.cancelButton.addEventListener("click", this.onToggleActive);
    this.actionSelect.addEventListener("change", this.onActionSelected);
    this.allCheckbox.addEventListener("change", this.onToggleAll);
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.addEventListener("change", this.onToggleBookmark);
    });
  }

  removeListeners() {
    this.activeToggle.removeEventListener("click", this.onToggleActive);
    this.cancelButton.removeEventListener("click", this.onToggleActive);
    this.actionSelect.removeEventListener("change", this.onActionSelected);
    this.allCheckbox.removeEventListener("change", this.onToggleAll);
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.removeEventListener("change", this.onToggleBookmark);
    });
  }

  onToggleActive() {
    this.active = !this.active;
    if (this.active) {
      this.element.classList.add("active");
      if(this.isStickyOn) {
        this.bulkEditBar.classList.add("sticky");
      }
    } else {
      this.element.classList.remove("active");
      if(this.isStickyOn) {
        this.bulkEditBar.classList.remove("sticky");
      }
    }
  }

  onToggleBookmark() {
    const allChecked = this.bookmarkCheckboxes.every((checkbox) => {
      return checkbox.checked;
    });
    this.allCheckbox.checked = allChecked;
    this.updateSelectAcross(allChecked);
    this.updateExecuteState();
  }

  onToggleAll() {
    const allChecked = this.allCheckbox.checked;
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.checked = allChecked;
    });
    this.updateSelectAcross(allChecked);
    this.updateExecuteState();
  }

  onActionSelected() {
    const action = this.actionSelect.value;

    if (action === "bulk_tag" || action === "bulk_untag") {
      this.tagAutoComplete.classList.remove("d-none");
    } else {
      this.tagAutoComplete.classList.add("d-none");
    }
  }

  updateSelectAcross(allChecked) {
    if (allChecked) {
      this.selectAcross.classList.remove("d-none");
    } else {
      this.selectAcross.classList.add("d-none");
      this.selectAcross.querySelector("input").checked = false;
    }
  }

  reset() {
    this.allCheckbox.checked = false;
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.checked = false;
    });
    this.updateSelectAcross(false);
    this.updateExecuteState();
  }

  updateExecuteState() {
    if (!this.executeButton) {
      return;
    }
    const hasSelection = this.bookmarkCheckboxes.some((checkbox) => {
      return checkbox.checked;
    });
    this.executeButton.disabled = !hasSelection;
  }

}

registerBehavior("ld-bulk-edit", BulkEdit);
