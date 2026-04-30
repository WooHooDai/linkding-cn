import { HeadlessElement } from "../utils/element.js";

class UploadButton extends HeadlessElement {
  init() {
    this.fileInput = this.nextElementSibling;
    this.onClick = this.onClick.bind(this);
    this.onChange = this.onChange.bind(this);

    this.addEventListener("click", this.onClick);
    this.fileInput?.addEventListener("change", this.onChange);
  }

  disconnectedCallback() {
    this.removeEventListener("click", this.onClick);
    this.fileInput?.removeEventListener("change", this.onChange);
  }

  onClick(event) {
    event.preventDefault();
    this.fileInput?.click();
  }

  onChange() {
    if (!this.fileInput?.files.length) {
      return;
    }
    const form = this.fileInput.closest("form");
    form?.requestSubmit(this.querySelector("button") || undefined);
    this.fileInput.value = "";
  }
}

customElements.define("ld-upload-button", UploadButton);
