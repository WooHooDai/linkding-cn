import { Modal } from "./modal.js";

// 一次性确认框：返回 Promise<boolean>。调用方负责本地化文案。
export function confirmDialog({
  title,
  warning,
  message,
  confirmLabel,
  cancelLabel,
}) {
  return new Promise((resolve) => {
    const modal = document.createElement("ld-modal");
    modal.className = "modal active confirm-dialog";
    modal.innerHTML = `
      <div class="modal-overlay" data-close-modal></div>
      <div class="modal-container" role="dialog" aria-modal="true">
        <div class="modal-header"><h2></h2></div>
        <div class="modal-body">
          <p class="confirm-dialog-warning"></p>
          <p class="confirm-dialog-message"></p>
        </div>
        <div class="modal-footer">
          <div class="confirm-dialog-actions">
            <button type="button" class="btn btn-link" data-cancel></button>
            <button type="button" class="btn btn-primary" data-confirm></button>
          </div>
        </div>
      </div>`;

    modal.querySelector("h2").textContent = title;
    modal.querySelector(".confirm-dialog-message").textContent = message;
    modal.querySelector("[data-cancel]").textContent = cancelLabel;
    modal.querySelector("[data-confirm]").textContent = confirmLabel;

    const warningEl = modal.querySelector(".confirm-dialog-warning");
    if (warning) {
      warningEl.textContent = warning;
    } else {
      warningEl.remove();
    }

    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    // 遮罩、Esc、取消、确认都会走 close，统一在这里落定结果
    modal.addEventListener("modal:close", () => finish(false));
    modal.querySelector("[data-cancel]").addEventListener("click", () => {
      finish(false);
      modal.doClose();
    });
    modal.querySelector("[data-confirm]").addEventListener("click", () => {
      finish(true);
      modal.doClose();
    });

    document.querySelector(".modals")?.appendChild(modal);
  });
}
