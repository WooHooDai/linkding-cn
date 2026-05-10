import { Behavior, registerBehavior } from "./runtime.js";
import Sortable from 'sortablejs';

class BundlesPageBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.init();
  }

  init() {
    const tableBody = this.element.querySelector(".crud-table tbody");
    if (!tableBody) return;

    this.sortable = new Sortable(tableBody, {
      handle: ".drag-handle",
      animation: 150,
      ghostClass: "dragging",
      onEnd: (evt) => {
        if (evt.oldIndex === evt.newIndex) return;

        const form = evt.item.closest('form');
        const moveBundleInput = form.querySelector('input[name="move_bundle"]');
        const movePositionInput = form.querySelector('input[name="move_position"]');

        moveBundleInput.value = evt.item.getAttribute('data-bundle-id');
        movePositionInput.value = evt.newIndex;

        form.requestSubmit(moveBundleInput);
      },
    });
  }

  destroy() {
    if (this.sortable) {
      this.sortable.destroy();
    }
  }
}

registerBehavior("ld-bundles-page", BundlesPageBehavior);