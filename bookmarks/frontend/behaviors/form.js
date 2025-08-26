import { Behavior, registerBehavior } from "./index";

class FormSubmit extends Behavior {
  constructor(element) {
    super(element);

    this.onKeyDown = this.onKeyDown.bind(this);
    this.element.addEventListener("keydown", this.onKeyDown);
  }

  destroy() {
    this.element.removeEventListener("keydown", this.onKeyDown);
  }

  onKeyDown(event) {
    // Check for Ctrl/Cmd + Enter combination
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      event.stopPropagation();
      this.element.requestSubmit();
    }
  }
}

class AutoSubmitBehavior extends Behavior {
  constructor(element) {
    super(element);

    this.submit = this.submit.bind(this);
    element.addEventListener("change", this.submit);
  }

  destroy() {
    this.element.removeEventListener("change", this.submit);
  }

  submit() {
    this.element.closest("form").requestSubmit();
  }
}

// Resets form controls to their initial values before Turbo caches the DOM.
// Useful for filter forms where navigating back would otherwise still show
// values from after the form submission, which means the filters would be out
// of sync with the URL.
class FormResetBehavior extends Behavior {
  constructor(element) {
    super(element);

    this.controls = this.element.querySelectorAll("input, select");
    this.controls.forEach((control) => {
      if (control.type === "checkbox" || control.type === "radio") {
        control.__initialValue = control.checked;
      } else {
        control.__initialValue = control.value;
      }
    });
  }

  destroy() {
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

class UploadButton extends Behavior {
  constructor(element) {
    super(element);
    this.fileInput = element.nextElementSibling;

    this.onClick = this.onClick.bind(this);
    this.onChange = this.onChange.bind(this);

    element.addEventListener("click", this.onClick);
    this.fileInput.addEventListener("change", this.onChange);
  }

  destroy() {
    this.element.removeEventListener("click", this.onClick);
    this.fileInput.removeEventListener("change", this.onChange);
  }

  onClick(event) {
    event.preventDefault();
    this.fileInput.click();
  }

  onChange() {
    // Check if the file input has a file selected
    if (!this.fileInput.files.length) {
      return;
    }
    const form = this.fileInput.closest("form");
    form.requestSubmit(this.element);
    // remove selected file so it doesn't get submitted again
    this.fileInput.value = "";
  }
}

class AssetRename extends Behavior {
  constructor(element) {
    super(element);

    this.assetId = element.dataset.assetId;
    this.originalText = "";
    this.displayNameSpan = null;

    this.onClick = this.onClick.bind(this);
    this.onKeyDown = this.onKeyDown.bind(this);

    element.addEventListener("click", this.onClick);
  }

  destroy() {
    setTimeout(() => this.reset(), 0);
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

  startEditing() {
    // 禁用其他重命名按钮
    document.querySelectorAll('.btn[ld-asset-rename]').forEach(btn => {
      if (btn !== this.element) {
        btn.disabled = true;
        btn.classList.add('btn-disabled');
      }
    });
    // 找到快照名所在元素
    const listItem = this.element.closest(".list-item");
    this.displayNameSpan = listItem.querySelector("[data-display-name]");
    if (!this.displayNameSpan) {
      console.error("Snapshot's name was not found.");
      return;
    }

    // 保存原始文本
    this.originalText = this.displayNameSpan.textContent.trim();
    
    // 创建输入框
    const input = document.createElement("input");
    input.type = "text";
    input.name = "new_display_name"
    input.className = "form-input input-sm";
    input.value = this.originalText;

    this.input = input;
    this.displayNameSpan.before(this.input);
    this.displayNameSpan.style.display = "none";
    
    this.input.addEventListener("keydown", this.onKeyDown);
    this.input.focus();
    this.input.select();

    const form = this.input.closest("form");
    form.addEventListener("submit", (event) => {
      const value = this.input.value.trim();
      if (value==="") {
        event.preventDefault();
        this.showInputError(this.input, "名称不能为空！");
        this.input.focus();
      }
    });
  }

  reset() {
    if (this.container) {
      this.container.remove();
      this.container = null;
    }
    this.element.classList.remove("d-none");

    if (this.displayNameSpan) {
      this.displayNameSpan.style.display = "";
      this.displayNameSpan = null;
    }

    // 恢复所有重命名按钮
    document.querySelectorAll('.btn[ld-asset-rename]').forEach(btn => {
      btn.disabled = false;
      btn.classList.remove('btn-disabled');
    });

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
    cancelButton.innerText = "取消"
    cancelButton.className = `${buttonClasses} mr-1`;
    cancelButton.addEventListener("click", this.reset.bind(this));

    const confirmButton = document.createElement(this.element.nodeName);
    confirmButton.type = "submit";
    confirmButton.name = this.element.name;
    confirmButton.value = this.element.value;
    confirmButton.innerText = "确认";
    confirmButton.className = buttonClasses;
    
    container.append(cancelButton, confirmButton);
    this.container = container;
    this.element.before(container);
    this.element.classList.add("d-none");
  }

  showInputError(input, message) {
    input.value = "";
    input.placeholder = message;
    input.classList.add('is-error');
  }

  clearInputError(input) {
    input.placeholder = "";
    input.classList.remove('is-error');
  }
}

registerBehavior("ld-form-submit", FormSubmit);
registerBehavior("ld-auto-submit", AutoSubmitBehavior);
registerBehavior("ld-form-reset", FormResetBehavior);
registerBehavior("ld-upload-button", UploadButton);
registerBehavior("ld-asset-rename", AssetRename);