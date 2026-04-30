import { HeadlessElement } from "../utils/element.js";

class Form extends HeadlessElement {
  constructor() {
    super();
    this.onKeyDown = this.onKeyDown.bind(this);
    this.onChange = this.onChange.bind(this);
  }

  init() {
    this.addEventListener("keydown", this.onKeyDown);
    this.addEventListener("change", this.onChange);

    if (this.hasAttribute("data-form-reset")) {
      this.initFormReset();
    }
  }

  disconnectedCallback() {
    this.removeEventListener("keydown", this.onKeyDown);
    this.removeEventListener("change", this.onChange);
    if (this.hasAttribute("data-form-reset") && this.controls) {
      this.resetForm();
    }
  }

  onChange(event) {
    if (event.target.hasAttribute("data-submit-on-change")) {
      this.querySelector("form")?.requestSubmit();
    }
  }

  onKeyDown(event) {
    if (
      this.hasAttribute("data-submit-on-ctrl-enter") &&
      event.key === "Enter" &&
      (event.metaKey || event.ctrlKey)
    ) {
      event.preventDefault();
      event.stopPropagation();
      this.querySelector("form")?.requestSubmit();
    }
  }

  initFormReset() {
    this.controls = this.querySelectorAll("input, select");
    this.controls.forEach((control) => {
      if (control.type === "checkbox" || control.type === "radio") {
        control.__initialValue = control.checked;
      } else {
        control.__initialValue = control.value;
      }
    });
  }

  resetForm() {
    this.controls.forEach((control) => {
      if (control.type === "checkbox" || control.type === "radio") {
        control.checked = control.__initialValue;
      } else {
        control.value = control.__initialValue;
      }
      delete control.__initialValue;
    });
  }
}

customElements.define("ld-form", Form);
