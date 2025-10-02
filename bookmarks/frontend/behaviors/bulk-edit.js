import { Behavior, registerBehavior } from "./index";

class BulkEdit extends Behavior {
  constructor(element) {
    super(element);

    this.active = element.classList.contains("active");

    this.init = this.init.bind(this);
    this.onToggleActive = this.onToggleActive.bind(this);
    this.onToggleAll = this.onToggleAll.bind(this);
    this.onToggleBookmark = this.onToggleBookmark.bind(this);
    this.onActionSelected = this.onActionSelected.bind(this);

    // 批量编辑工具栏粘性吸顶
    this.headerControls = element.querySelector("div.header-controls");
    const isStickyOn = this.headerControls.dataset.stickyOn === 'true'
    if(isStickyOn) {
      this.isStickyOn = isStickyOn
      this.bulkEditBar = document.querySelector('.bulk-edit-bar');
      this.searchContainer = document.querySelector('.search-container');
      this.scroller = document.querySelector('.body-container') || window;
      this.onScroll = this.onScroll.bind(this);
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
    this.actionSelect.addEventListener("change", this.onActionSelected);
    this.allCheckbox.addEventListener("change", this.onToggleAll);
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.addEventListener("change", this.onToggleBookmark);
    });
  }

  removeListeners() {
    this.activeToggle.removeEventListener("click", this.onToggleActive);
    this.actionSelect.removeEventListener("change", this.onActionSelected);
    this.allCheckbox.removeEventListener("change", this.onToggleAll);
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.removeEventListener("change", this.onToggleBookmark);
    });
    if(this.isStickyOn) {
      this.scroller.removeEventListener('scroll', this.onScroll);
    }
  }

  onToggleActive() {
    this.active = !this.active;
    if (this.active) {
      this.element.classList.add("active", "activating");
      setTimeout(() => {
        this.element.classList.remove("activating");
      }, 500);
      this.searchContainer.style.opacity = 0;
      // 粘性吸附
      if(this.isStickyOn) {
        this.scroller.addEventListener('scroll', this.onScroll, { passive: true });
      }
    } else {
      this.element.classList.remove("active");
      this.searchContainer.style.opacity = 1;
      if(this.isStickyOn) {
        this.scroller.removeEventListener('scroll', this.onScroll);
      }
    }
  }

  onToggleBookmark() {
    const allChecked = this.bookmarkCheckboxes.every((checkbox) => {
      return checkbox.checked;
    });
    this.allCheckbox.checked = allChecked;
    this.updateSelectAcross(allChecked);
  }

  onToggleAll() {
    const allChecked = this.allCheckbox.checked;
    this.bookmarkCheckboxes.forEach((checkbox) => {
      checkbox.checked = allChecked;
    });
    this.updateSelectAcross(allChecked);
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
  }

  onScroll() {
    if(this.headerControls.classList.contains("sticky")){
      this.bulkEditBar.classList.add("sticky")
    } else {
      this.bulkEditBar.classList.remove("sticky")
    }
  }
}

registerBehavior("ld-bulk-edit", BulkEdit);
