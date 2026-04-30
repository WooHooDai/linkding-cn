import { setAfterPageLoadFocusTarget } from "../utils/focus.js";
import { Modal } from "./modal.js";

class DetailsModal extends Modal {
  doClose() {
    super.doClose();

    const bookmarkId = this.dataset.bookmarkId;
    if (bookmarkId) {
      setAfterPageLoadFocusTarget(
        `ul.bookmark-list li[data-bookmark-id='${bookmarkId}'] a.view-action`,
      );
    }
  }
}

customElements.define("ld-details-modal", DetailsModal);
