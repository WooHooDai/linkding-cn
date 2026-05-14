const VIEWPORT_HEIGHT_VAR = "--app-viewport-height";

let started = false;
let rafId = null;

function resolveViewportHeight() {
  const visualHeight = window.visualViewport?.height;
  const innerHeight = window.innerHeight;

  if (visualHeight && visualHeight > 0) {
    return Math.round(visualHeight);
  }
  return Math.round(innerHeight);
}

function updateViewportHeightVar() {
  rafId = null;
  const height = resolveViewportHeight();
  if (height > 0) {
    document.documentElement.style.setProperty(VIEWPORT_HEIGHT_VAR, `${height}px`);
  }
}

function scheduleViewportHeightVarUpdate() {
  if (rafId !== null) {
    cancelAnimationFrame(rafId);
  }
  rafId = requestAnimationFrame(updateViewportHeightVar);
}

export function setupViewportHeightVar() {
  if (started || typeof window === "undefined") {
    return;
  }
  started = true;

  scheduleViewportHeightVarUpdate();
  window.addEventListener("resize", scheduleViewportHeightVarUpdate, { passive: true });
  window.addEventListener("orientationchange", scheduleViewportHeightVarUpdate, { passive: true });
  window.visualViewport?.addEventListener("resize", scheduleViewportHeightVarUpdate, {
    passive: true,
  });
  window.visualViewport?.addEventListener("scroll", scheduleViewportHeightVarUpdate, {
    passive: true,
  });
  document.addEventListener("turbo:load", scheduleViewportHeightVarUpdate);
}
