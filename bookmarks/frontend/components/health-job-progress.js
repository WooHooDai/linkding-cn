import { Behavior, registerBehavior, applyBehaviors } from "./runtime.js";
import { showToast } from "./toast.js";

/**
 * 健康检查任务进度胶囊（书签列表页挂分页器 / Bundle 列表页右下角共用）。
 *
 * 布局：
 * - 完整模式：暂停/继续按钮 · 运行时间 · 进度百分比 (已完成/总数) · 中止 · 迷你
 * - 迷你模式：暂停/继续按钮 · 进度百分比（点击百分比区域展开为完整模式）
 *
 * - 轮询间隔 1s：页面加载即先拉一次状态再定时间隔轮询，初始计数/百分比/
 *   时长直接以模板渲染值就地更新，暂停态（interrupted）下保留胶囊并显示
 *   Paused 标记、百分比保持真实值。
 * - 定位：fixed 滚动跟随，水平对齐内容容器 main 右缘（书签列表页 main.col-2、
 *   bundle 页 main.bundles-page），书签列表页垂直抬升避开 sticky 分页器条。
 * - 迷你/展开样式用 localStorage 记忆，刷新后恢复；6s 自动迷你仅在没有
 *   记忆展开时触发。
 * - 三按钮：暂停/继续（pause/resume）、中止（cancel，经项目 ld-confirm-button
 *   确认弹窗）、迷你（mini），经 health.job_control 控制后台任务。
 */
class HealthJobProgress extends Behavior {
  constructor(element) {
    super(element);

    this.element = element;
    this.destroyed = false;
    this.finished = false;
    this.paused = false;
    this.miniTimer = null;
    this.storageKey = "ld-health-capsule-mini";
    this.pollInterval = 1000;

    this.tick = this.tick.bind(this);
    this.onToggle = this.onToggle.bind(this);
    this.onPause = this.onPause.bind(this);
    this.onCancel = this.onCancel.bind(this);
    this.onMini = this.onMini.bind(this);
    this.positionCapsule = this.positionCapsule.bind(this);
    // resume 进行中：暂停态到运行态的过渡期，需继续轮询等待状态确认
    this.resuming = false;

    this.pauseBtn = this.element.querySelector("[data-health-job-pause]");
    this.mainEl = this.element.querySelector("[data-health-job-toggle]");
    this.elapsedEl = this.element.querySelector("[data-health-job-elapsed]");
    this.percentEl = this.element.querySelector("[data-health-job-percent]");
    this.countEl = this.element.querySelector("[data-health-job-count]");
    this.miniPercentEl = this.element.querySelector(
      "[data-health-job-mini-percent]"
    );
    this.stateEl = this.element.querySelector("[data-health-job-state]");
    this.cancelBtn = this.element.querySelector("[data-health-job-cancel]");
    this.miniBtn = this.element.querySelector("[data-health-job-mini]");
    this.controlUrl = this.element.dataset.healthControlUrl;


    // 锚定内容容器 main（书签列表页 main.col-2 / bundle 页 main.bundles-page）：
    // fixed 保持滚动跟随，水平位置用 JS 计算容器右缘到视口右缘的距离；
    // ResizeObserver 覆盖窗口缩放与侧边栏开合引起的容器宽度变化
    this.anchor = this.element.closest("main");
    if (this.anchor) {
      this.positionCapsule();
      // 右缘重算触发源：
      // 1. ResizeObserver 观察 body——任何视口/内容尺寸变化都触发，
      //    覆盖窗口缩放、响应式调试模式进出、侧边栏开合；
      //    观察 main 不可靠（main.col-2 宽度固定，仅平移不触发 RO）
      // 2. window resize 兜底（部分场景 RO 回调延迟）
      this.resizeObserver = new ResizeObserver(this.positionCapsule);
      this.resizeObserver.observe(document.body);
      window.addEventListener("resize", this.positionCapsule, { passive: true });
      // 页面完全加载（字体/图片就绪）后校正一次，覆盖加载期布局未稳定的场景
      window.addEventListener("load", this.positionCapsule, { once: true });
      // 响应式调试模式（设备仿真）进出时 visualViewport 尺寸变化最可靠；
      // 与 window resize 双保险
      if (window.visualViewport) {
        this.onVisualViewportResize = this.positionCapsule.bind(this);
        window.visualViewport.addEventListener(
          "resize",
          this.onVisualViewportResize,
        );
      }
      // 窗口重新获得焦点时布局可能已变化（含退出设备仿真），轻量校正一次
      this.onFocusResize = this.positionCapsuleOnce.bind(this);
      window.addEventListener("focus", this.onFocusResize);
    }

    // 书签列表页：胶囊底部随滚动动态避让分页器——
    // 跟随开启时分页器始终吸底（胶囊始终抬高）；跟随关闭时仅滚动到
    // 分页器进入视口才抬高（平时贴底）。
    // 分页器从锚定容器 main 内查找（胶囊 DOM 独立于分页器渲染）
    this.paginator = this.anchor
      ? this.anchor.querySelector(".bookmark-pagination")
      : null;
    this.updateCapsuleBottom = this.updateCapsuleBottom.bind(this);
    if (this.paginator) {
      window.addEventListener("scroll", this.updateCapsuleBottom, {
        passive: true,
      });
      this.updateCapsuleBottom();
    }

    if (this.mainEl) this.mainEl.addEventListener("click", this.onToggle);
    if (this.pauseBtn) this.pauseBtn.addEventListener("click", this.onPause);
    // 中止走项目 ld-confirm-button 确认弹窗：确认后触发 _onConfirm
    if (this.cancelBtn) {
      this.cancelBtn._onConfirm = this.onCancel;
    }
    if (this.miniBtn) this.miniBtn.addEventListener("click", this.onMini);

    // 暂停态初始值来自模板渲染（state=interrupted 时服务端已加 is-paused）：
    // 避免刷新后按钮误发 pause 导致"继续后立即又暂停"
    this.paused = this.element.classList.contains("is-paused");
    this.setPauseUi();

    // 用模板渲染的计数立即算出初始百分比（暂停态/刷新态都正确，不再 0.0%）
    const m = (this.countEl ? this.countEl.textContent : "").match(
      /(\d+)\s*\/\s*(\d+)/
    );
    if (m) {
      this.setProgress(parseInt(m[1], 10), parseInt(m[2], 10));
    } else {
      this.setIndeterminate();
    }

    // 恢复上次的迷你/展开样式（仅当任务仍活跃时才有意义）
    try {
      if (localStorage.getItem(this.storageKey) === "1") {
        this.element.classList.add("is-mini");
      }
    } catch (error) {
      /* localStorage 不可用时忽略 */
    }

    // 先立即拉一次状态（消除初始 00:00/0.0% 闪现），随后 1s 轮询
    this.timer = window.setTimeout(this.tick, 0);

    // 完整模式展示片刻后自动收起为迷你模式（除非用户上次选择了展开）
    if (!this.element.classList.contains("is-mini")) {
      this.armAutoMini();
    }
  }

  armAutoMini() {
    if (this.miniTimer) {
      window.clearTimeout(this.miniTimer);
    }
    this.miniTimer = window.setTimeout(() => {
      if (!this.destroyed && !this.finished && !this.paused) {
        this.setMini(true);
      }
    }, 6000);
  }

  setMini(mini) {
    this.element.classList.toggle("is-mini", mini);
    try {
      localStorage.setItem(this.storageKey, mini ? "1" : "0");
    } catch (error) {
      /* ignore */
    }
  }

  setProgress(done, total) {
    const percent = this.percent(done, total);
    if (this.percentEl) this.percentEl.textContent = percent;
    if (this.miniPercentEl) this.miniPercentEl.textContent = percent;
    if (this.countEl) this.countEl.textContent = `(${done}/${total})`;
  }

  percent(done, total) {
    if (!total) return "0.0%";
    return `${((done / total) * 100).toFixed(1)}%`;
  }

  setIndeterminate() {
    if (this.percentEl) this.percentEl.textContent = "…";
    if (this.miniPercentEl) this.miniPercentEl.textContent = "…";
    if (this.countEl) this.countEl.textContent = "(…/…)";
  }

  setElapsed(sec) {
    if (!this.elapsedEl) return;
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = (n) => String(n).padStart(2, "0");
    this.elapsedEl.textContent =
      h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
  }

  onToggle() {
    // 完整模式点击主区无操作；仅迷你模式点击百分比展开
    if (this.element.classList.contains("is-mini")) {
      this.setMini(false);
      this.armAutoMini();
    }
  }

  async onPause() {
    if (!this.controlUrl) return;
    const action = this.paused ? "resume" : "pause";
    const prevPaused = this.paused;
    // 乐观更新：立即切换 UI，失败时撤回，避免请求往返造成的卡顿
    this.paused = !prevPaused;
    this.element.classList.toggle("is-paused", this.paused);
    this.setPauseUi();
    if (action === "resume") {
      this.resuming = true;
    } else if (this.timer) {
      // pause：立即停止已排程的轮询（in-flight 请求返回后由 tick 尾部检查跳过续排）
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    const rollback = () => {
      this.resuming = false;
      this.paused = prevPaused;
      this.element.classList.toggle("is-paused", prevPaused);
      this.setPauseUi();
    };
    try {
      const body = new URLSearchParams({ action });
      const response = await fetch(this.controlUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken": this.csrfToken(),
        },
        body: body.toString(),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        rollback();
        showToast(data.error || "Control failed", { tone: "error" });
        return;
      }
      if (action === "resume") {
        // 后端已接受 resume，但状态转 running 前仍是 interrupted：
        // 过渡态继续轮询，等状态确认后稳定在运行 UI
        if (this.timer) window.clearTimeout(this.timer);
        this.timer = window.setTimeout(this.tick, 0);
      }
    } catch (error) {
      rollback();
      showToast("Control failed", { tone: "error" });
    }
  }

  onCancel() {
    if (!this.controlUrl) return;
    const body = new URLSearchParams({ action: "cancel" });
    fetch(this.controlUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": this.csrfToken(),
      },
      body: body.toString(),
    })
      .then((response) => {
        if (!response.ok) {
          return response.json().then((d) => {
            throw new Error(d.error || "Cancel failed");
          });
        }
        // 立即收起并 toast：任务处于暂停（interrupted）态时轮询已停止，
        // 不能依赖下一次轮询感知 cancelled，否则胶囊会停留到刷新页面
        this.finishCancelled();
      })
      .catch((error) => showToast(error.message, { tone: "error" }));
  }

  finishCancelled() {
    if (this.finished) return;
    this.finished = true;
    showToast(this.element.dataset.msgCancelled || "Health Check cancelled", {
      tone: "error",
    });
    this.element.classList.add("is-finishing");
    this.finish();
    window.setTimeout(() => this.element.remove(), 300);
  }

  onMini() {
    this.setMini(true);
    if (this.miniTimer) {
      window.clearTimeout(this.miniTimer);
      this.miniTimer = null;
    }
  }

  positionCapsule() {
    if (!this.anchor || !this.element) return;
    // 布局可能仍在过渡（如浏览器退出响应式调试模式/设备仿真、字体与图片
    // 加载）——浏览器先发尺寸事件、布局尚未恢复，单帧 rAF 可能拿到过渡中
    // 的旧 rect；连续多帧校正直到布局稳定后再停
    let frames = 0;
    const maxFrames = 6;
    const apply = () => {
      if (this.destroyed || frames >= maxFrames) return;
      frames += 1;
      if (!this.anchor || !this.element) return;
      const rect = this.anchor.getBoundingClientRect();
      // 容器水平居中时 rect.right 的 x 不随滚动变化，仅尺寸变化需要重算；
      // 胶囊右缘与容器右缘贴合（无额外间隔）
      this.element.style.right =
        Math.max(0, document.documentElement.clientWidth - rect.right) + "px";
      window.requestAnimationFrame(apply);
    };
    window.requestAnimationFrame(apply);
  }

  // 单帧校正：用于每秒 tick 的轻量兜底（布局稳定时无需多帧）
  positionCapsuleOnce() {
    if (!this.anchor || !this.element) return;
    window.requestAnimationFrame(() => {
      if (!this.anchor || !this.element) return;
      const rect = this.anchor.getBoundingClientRect();
      this.element.style.right =
        Math.max(0, document.documentElement.clientWidth - rect.right) + "px";
    });
  }

  updateCapsuleBottom() {
    if (!this.paginator || !this.element) return;
    const pr = this.paginator.getBoundingClientRect();
    const vh = window.innerHeight;
    if (pr.top < vh) {
      // 分页器进入视口：抬高到分页器上方并留空隙。
      // 抬升量 = 视口底到分页器顶的距离 + 12px——sticky 开启时条始终吸底
      // （持续抬高），关闭时仅滚动到底部时抬高；分页器高度变化（如滚动
      // 到底后条增高）不影响避让
      const lift = Math.min(vh - pr.top + 12, vh - 64);
      this.element.style.bottom = Math.max(12, lift) + "px";
    } else {
      // 未滚到底部：贴底（回落到 CSS 默认 bottom）
      this.element.style.bottom = "";
    }
  }

  setPauseUi() {
    // 暂停态不再显示文字标记（按钮图标已表达状态）；仅运行态清空
    if (this.stateEl) {
      this.stateEl.textContent = "";
    }
    if (this.pauseBtn) {
      const label = this.paused
        ? this.element.dataset.labelResume || "Resume"
        : this.element.dataset.labelPause || "Pause";
      this.pauseBtn.title = label;
      this.pauseBtn.setAttribute("aria-label", label);
      this.pauseBtn.classList.toggle("is-resume", this.paused);
      this.pauseBtn.innerHTML = this.paused
        ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>'
        : '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>';
    }
  }

  csrfToken() {
    const cookie = document.cookie
      .split("; ")
      .find((c) => c.startsWith("ld_csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
  }

  async tick() {
    if (this.destroyed || this.finished) {
      return;
    }
    const statusUrl = this.element.dataset.healthStatusUrl;
    try {
      const response = await fetch(statusUrl, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`status failed: ${response.status}`);
      }
      const data = await response.json();

      if (data.elapsed_sec != null) {
        this.setElapsed(data.elapsed_sec);
      }

      if (data.state === "interrupted") {
        if (this.resuming) {
          // resume 后状态尚未转回 running：继续轮询（500ms）等待确认
          this.timer = window.setTimeout(this.tick, 500);
          return;
        }
        // 已暂停：保留胶囊，显示暂停标记与真实进度；任务已停止推进，
        // 暂停期间不再轮询（时长/进度均冻结），恢复后由 onPause 重新拉起
        this.paused = true;
        this.element.classList.add("is-paused");
        this.setPauseUi();
        if (data.total > 0) {
          this.setProgress(data.done, data.total);
        }
        // 暂停态也校正一次位置：暂停任务不会进入 active 分支，退出响应式
        // 调试模式等布局变化后需在此兜底
        this.positionCapsuleOnce();
        return;
      }

      if (data.active) {
        this.resuming = false;
        this.paused = false;
        this.element.classList.remove("is-paused");
        this.setPauseUi();
        // 每次轮询顺带校正胶囊右缘——任何原因（布局恢复延迟、缓存等）
        // 导致的错位会在任务运行期间每秒自动修复
        this.positionCapsuleOnce();
        if (data.total > 0) {
          this.setProgress(data.done, data.total);
        } else {
          this.setIndeterminate();
        }
        // 乐观暂停后可能已有 in-flight 请求返回：暂停态（非 resuming）不续排
        if (this.paused && !this.resuming) return;
        this.timer = window.setTimeout(this.tick, this.pollInterval);
      } else {
        this.finished = true;
        const tone = data.state === "completed" ? "success" : "error";
        let message =
          this.element.dataset.msgCompleted || "Health Check completed";
        if (data.state === "failed") {
          message = this.element.dataset.msgFailed || "Health Check failed";
        } else if (data.state === "cancelled") {
          message = this.element.dataset.msgCancelled || "Health Check cancelled";
        }
        showToast(message, { tone });
        this.element.classList.add("is-finishing");
        this.finish();
        window.setTimeout(() => this.element.remove(), 300);
      }
    } catch (error) {
      // 网络异常等：延迟后重试
      this.timer = window.setTimeout(this.tick, this.pollInterval);
    }
  }

  finish() {
    this.finished = true;
    if (this.timer) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.miniTimer) {
      window.clearTimeout(this.miniTimer);
      this.miniTimer = null;
    }
    try {
      localStorage.removeItem(this.storageKey);
    } catch (error) {
      /* ignore */
    }
  }

  destroy() {
    this.destroyed = true;
    if (this.timer) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    if (window.visualViewport && this.onVisualViewportResize) {
      window.visualViewport.removeEventListener(
        "resize",
        this.onVisualViewportResize,
      );
    }
    if (this.onFocusResize) {
      window.removeEventListener("focus", this.onFocusResize);
    }
    if (this.resizeObserver && this.anchor) {
      this.resizeObserver.disconnect();
    }
    if (this.anchor) {
      window.removeEventListener("resize", this.positionCapsule);
      window.removeEventListener("load", this.positionCapsule);
    }
    if (this.updateCapsuleBottom && this.paginator) {
      window.removeEventListener("scroll", this.updateCapsuleBottom);
    }
    if (this.miniTimer) {
      window.clearTimeout(this.miniTimer);
      this.miniTimer = null;
    }
    if (this.mainEl) this.mainEl.removeEventListener("click", this.onToggle);
    if (this.pauseBtn) this.pauseBtn.removeEventListener("click", this.onPause);
    if (this.miniBtn) this.miniBtn.removeEventListener("click", this.onMini);
  }
}

registerBehavior("data-health-job-progress", HealthJobProgress);

// 兜底绑定：正常路径由 runtime 在 DOMContentLoaded / Turbo 导航后扫描绑定。
// 若 Turbo body 替换等竞态、或 bundle 加载慢于模板渲染导致胶囊错过扫描
// （表现为页面加载后胶囊未初始化、停在 CSS 默认位置），此处每 200ms 补
// 扫描，绑定成功即停（最多 50 次 = 10s）。applyBehaviors 内部有
// __behaviors 防重，重复调用安全。
(function bootstrapHealthJobProgress() {
  let tries = 0;
  const bound = () => {
    const el = document.querySelector("[data-health-job-progress]");
    return (
      el &&
      el.__behaviors &&
      el.__behaviors.some((b) => b instanceof HealthJobProgress)
    );
  };
  const scan = () => {
    // 关键：bundle 可能在 body 解析完成前加载（document.body 为 null），
    // 直接 applyBehaviors(null) 会顶层抛错中止整个 bundle（曾导致
    // dropdown/搜索框等后续组件全部未注册）
    if (!document.body) {
      return;
    }
    if (bound() || ++tries > 50) {
      window.clearInterval(timer);
      return;
    }
    applyBehaviors(document.body, ["data-health-job-progress"]);
  };
  const timer = window.setInterval(scan, 200);
  scan();
})();
