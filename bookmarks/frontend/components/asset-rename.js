import { Behavior, registerBehavior } from "./runtime.js";
import { gettext } from "../utils/i18n.js";

class AssetRenameBehavior extends Behavior {
  constructor(element) {
    super(element);

    this.assetId = element.dataset.assetId;
    this.originalText = "";
    this.displayNameSpan = null;
    this.container = null;
    this.input = null;
    this.form = null;

    this.onClick = this.onClick.bind(this);
    this.onKeyDown = this.onKeyDown.bind(this);
    this.onSubmit = this.onSubmit.bind(this);

    element.addEventListener("click", this.onClick);
  }

  destroy() {
    this.reset();
    this.element.removeEventListener("click", this.onClick);
  }

  onClick(event) {
    event.preventDefault();
    this.showConfirmation();
    this.startEditing();
  }

  onKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      this.reset();
    }
  }

  onSubmit(event) {
    if (!this.input) {
      return;
    }

    const value = this.input.value.trim();
    if (!value) {
      event.preventDefault();
      this.showInputError(this.input, gettext("Name cannot be empty."));
      this.input.focus();
    }
  }

  startEditing() {
    document.querySelectorAll(".btn[ld-asset-rename]").forEach((button) => {
      if (button !== this.element) {
        button.disabled = true;
        button.classList.add("btn-disabled");
      }
    });

    const listItem = this.element.closest(".list-item");
    this.displayNameSpan = listItem?.querySelector("[data-display-name]") || null;
    if (!this.displayNameSpan) {
      return;
    }

    this.originalText = this.displayNameSpan.textContent.trim();

    this.input = document.createElement("input");
    this.input.type = "text";
    this.input.name = "new_display_name";
    this.input.className = "form-input input-sm";
    this.input.value = this.originalText;

    this.displayNameSpan.before(this.input);
    this.displayNameSpan.style.display = "none";

    this.input.addEventListener("keydown", this.onKeyDown);
    this.input.focus();
    this.input.select();

    this.form = this.input.closest("form");
    this.form?.addEventListener("submit", this.onSubmit);
  }

  reset() {
    this.container?.remove();
    this.container = null;
    this.element.classList.remove("d-none");

    if (this.displayNameSpan) {
      this.displayNameSpan.style.display = "";
      this.displayNameSpan = null;
    }

    document.querySelectorAll(".btn[ld-asset-rename]").forEach((button) => {
      button.disabled = false;
      button.classList.remove("btn-disabled");
    });

    this.form?.removeEventListener("submit", this.onSubmit);
    this.form = null;

    if (this.input) {
      this.clearInputError(this.input);
      this.input.removeEventListener("keydown", this.onKeyDown);
      this.input.remove();
      this.input = null;
    }
  }

  showConfirmation() {
    const container = document.createElement("span");
    container.className = "confirmation";

    const buttonClasses = Array.from(this.element.classList.values())
      .filter((cls) => cls.startsWith("btn"))
      .join(" ");

    const cancelButton = document.createElement(this.element.nodeName);
    cancelButton.type = "button";
    cancelButton.innerText = gettext("Cancel");
    cancelButton.className = `${buttonClasses} mr-1`;
    cancelButton.addEventListener("click", () => this.reset());

    const confirmButton = document.createElement(this.element.nodeName);
    confirmButton.type = "submit";
    confirmButton.name = this.element.name;
    confirmButton.value = this.element.value;
    confirmButton.innerText = gettext("Confirm");
    confirmButton.className = buttonClasses;

    container.append(cancelButton, confirmButton);
    this.container = container;
    this.element.before(container);
    this.element.classList.add("d-none");
  }

  showInputError(input, message) {
    input.value = "";
    input.placeholder = message;
    input.classList.add("is-error");
  }

  clearInputError(input) {
    input.placeholder = "";
    input.classList.remove("is-error");
  }
}

registerBehavior("ld-asset-rename", AssetRenameBehavior);
