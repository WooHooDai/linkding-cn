import { Behavior, registerBehavior } from "./runtime.js";

class RandomSingleButton extends Behavior {
  constructor(element) {
    super(element);
    this.onClick = this.onClick.bind(this);
    element.addEventListener("click", this.onClick);
  }

  destroy() {
    this.element.removeEventListener("click", this.onClick);
  }

  async onClick() {
    const btn = this.element;
    btn.disabled = true;

    const fd = new FormData();
    fd.append("csrfmiddlewaretoken", btn.dataset.csrfToken);
    fd.append("random_single", "1");
    fd.append("random_target", btn.dataset.randomTarget);

    // 从 data-param-* 属性中还原筛选参数
    for (const [key, value] of Object.entries(btn.dataset)) {
      if (key.startsWith("param") && key !== "param") {
        const paramName = key.slice(5).toLowerCase();
        if (paramName) fd.append(paramName, value);
      }
    }

    try {
      const resp = await fetch("", {
        method: "POST",
        body: fd,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const data = await resp.json();
      if (!data.url) return;

      const target = btn.dataset.randomTarget;
      if (target === "details") {
        // Turbo Frame 局部加载详情弹窗，不刷新页面
        const a = document.createElement("a");
        a.href = data.url;
        a.dataset.turboAction = "replace";
        a.dataset.turboFrame = "details-modal";
        a.style.display = "none";
        document.body.appendChild(a);
        a.click();
        a.remove();
      } else if (btn.dataset.linkTarget === "_blank") {
        // url / reader / snapshot 都是链接式目标，跟随「打开方式」设置；details 是弹窗，不适用
        window.open(data.url, "_blank");
      } else {
        window.location.href = data.url;
      }
    } catch {
      // 静默失败
    } finally {
      btn.disabled = false;
    }
  }
}

registerBehavior("ld-random-single", RandomSingleButton);
