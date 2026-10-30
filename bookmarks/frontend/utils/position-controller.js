import {
  arrow,
  autoUpdate,
  computePosition,
  flip,
  offset,
  shift,
} from "@floating-ui/dom";

export class PositionController {
  constructor(options) {
    this.anchor = options.anchor;
    this.overlay = options.overlay;
    this.arrow = options.arrow;
    this.placement = options.placement || "bottom";
    this.offsetValue = options.offset;
    this.autoWidth = options.autoWidth || false;
    this.widthAnchor = options.widthAnchor || this.anchor;
    this.autoUpdateCleanup = null;
  }

  enable() {
    this.overlay.style.position = "fixed";
    if (typeof this.overlay.showPopover === "function") {
      this.overlay.setAttribute("popover", "manual");
      if (!this.overlay.matches(":popover-open")) {
        this.overlay.showPopover();
      }
    }

    this.updatePosition();
    if (!this.autoUpdateCleanup) {
      this.autoUpdateCleanup = autoUpdate(this.anchor, this.overlay, () =>
        this.updatePosition(),
      );
    }
  }

  disable() {
    if (this.autoUpdateCleanup) {
      this.autoUpdateCleanup();
      this.autoUpdateCleanup = null;
    }
    if (this.overlay.matches(":popover-open")) {
      this.overlay.hidePopover();
    }
    this.overlay.removeAttribute("popover");
    this.overlay.style.removeProperty("position");
    this.overlay.style.removeProperty("left");
    this.overlay.style.removeProperty("top");
    this.overlay.style.removeProperty("right");
    this.overlay.style.removeProperty("bottom");
    if (this.autoWidth) {
      this.overlay.style.removeProperty("width");
    }
  }

  updatePosition() {
    if (this.autoWidth) {
      this.overlay.style.width = `${this.widthAnchor.offsetWidth}px`;
    }

    const middleware = [flip(), shift()];
    if (this.arrow) {
      middleware.push(arrow({ element: this.arrow }));
    }
    if (this.offsetValue) {
      middleware.push(offset(this.offsetValue));
    }

    computePosition(this.anchor, this.overlay, {
      placement: this.placement,
      strategy: "fixed",
      middleware,
    }).then(({ x, y, placement, middlewareData }) => {
      Object.assign(this.overlay.style, {
        left: `${x}px`,
        top: `${y}px`,
        right: "auto",
        bottom: "auto",
      });

      this.overlay.classList.remove("top-aligned", "bottom-aligned");
      this.overlay.classList.add(`${placement}-aligned`);

      if (this.arrow && middlewareData.arrow) {
        const { x: arrowX, y: arrowY } = middlewareData.arrow;
        Object.assign(this.arrow.style, {
          left: arrowX != null ? `${arrowX}px` : "",
          top: arrowY != null ? `${arrowY}px` : "",
        });
      }
    });

  }
}
