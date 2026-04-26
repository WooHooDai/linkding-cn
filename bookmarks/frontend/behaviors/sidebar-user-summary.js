import { Behavior, registerBehavior } from "./index";
import {
  buildPagePreferenceRequestHeaders,
  stripPagePreferenceParams,
} from "../state/page-preferences";
import {
  buildStoredSummaryPreferenceState,
  getStoredSummaryState,
  getSummaryStateFromElement,
  storeRenderedSummaryPreferences,
  storeSummaryPreferenceTargets,
  storeSummaryPreferences,
} from "../state/summary-preferences";
import { persistOpenDrawerState } from "../state/filter-drawer-state";

const DEFAULT_HEATMAP_MIN_COLUMN_WIDTH = 18;
const DEFAULT_HEATMAP_MIN_VISIBLE_COLUMNS = 8;

function computeVisibleHeatmapColumns({
  availableWidth,
  totalColumns,
  minColumnWidth,
  gap,
  minColumns = 8,
}) {
  if (!Number.isFinite(totalColumns) || totalColumns <= 0) {
    return 0;
  }

  const safeGap = Number.isFinite(gap) ? gap : 0;
  const safeMinColumnWidth =
    Number.isFinite(minColumnWidth) && minColumnWidth > 0 ? minColumnWidth : 18;
  const safeMinColumns = Math.max(1, Math.min(totalColumns, minColumns));

  if (!Number.isFinite(availableWidth) || availableWidth <= 0) {
    return totalColumns;
  }

  const fittedColumns = Math.floor(
    (availableWidth + safeGap) / (safeMinColumnWidth + safeGap),
  );

  return Math.min(totalColumns, Math.max(safeMinColumns, fittedColumns));
}

function buildHeatmapColumnVisibility(totalColumns, visibleColumns) {
  if (!Number.isFinite(totalColumns) || totalColumns <= 0) {
    return [];
  }

  const clampedVisibleColumns = Math.max(
    0,
    Math.min(totalColumns, visibleColumns),
  );
  const hiddenColumns = totalColumns - clampedVisibleColumns;

  return Array.from(
    { length: totalColumns },
    (_value, index) => index >= hiddenColumns,
  );
}

class SidebarUserSummaryBehavior extends Behavior {
  constructor(element) {
    super(element);

    this.draftStart = null;
    this.heatmapResizeObserver = null;
    this.collectionToggle = this.element.querySelector(
      "[data-summary-collection-toggle]",
    );
    this.collectionStartSummary = this.element.querySelector(
      "[data-summary-collection-start-summary]",
    );

    this.handleClick = this.handleClick.bind(this);
    this.handleCollectionToggle = this.handleCollectionToggle.bind(this);
    this.handleWindowResize = this.handleWindowResize.bind(this);
    this.syncHeatmapLayout = this.syncHeatmapLayout.bind(this);
    this.element.addEventListener("click", this.handleClick);
    if (this.collectionToggle && this.collectionStartSummary) {
      this.collectionToggle.addEventListener("toggle", this.handleCollectionToggle);
      this.handleCollectionToggle();
    }

    if (this.applyStoredDisplayPreferences()) {
      return;
    }
    this.syncStoredDisplayPreferencesFromDom();
    this.initializeHeatmapLayout();
  }

  destroy() {
    this.element.removeEventListener("click", this.handleClick);
    if (this.collectionToggle) {
      this.collectionToggle.removeEventListener("toggle", this.handleCollectionToggle);
    }
    if (this.heatmapResizeObserver) {
      this.heatmapResizeObserver.disconnect();
    }
    window.removeEventListener("resize", this.handleWindowResize);
  }

  handleCollectionToggle() {
    if (!this.collectionStartSummary || !this.collectionToggle) {
      return;
    }

    this.collectionStartSummary.hidden = !this.collectionToggle.open;
  }

  handleClick(event) {
    const link = event.target.closest("a[href]");
    if (!link || !this.element.contains(link) || this.shouldBypassClick(event)) {
      return;
    }

    this.persistDisplayPreferenceFromLink(link);

    if (link.dataset.summaryCalendarDay) {
      this.handleCalendarDayClick(event, link);
      return;
    }

    event.preventDefault();
    this.visitStream(link.href);
  }

  handleCalendarDayClick(event, link) {
    if (this.element.dataset.summaryMode !== "calendar") {
      return;
    }

    const dateValue = link.dataset.summaryDay;
    if (!dateValue) {
      return;
    }

    event.preventDefault();

    if (this.hasCommittedRange()) {
      this.clearCommittedSelection();
      this.draftStart = dateValue;
      this.updateDraftState();
      return;
    }

    if (!this.draftStart) {
      this.draftStart = dateValue;
      this.updateDraftState();
      return;
    }

    this.applyRange(this.draftStart, dateValue);
  }

  shouldBypassClick(event) {
    return (
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    );
  }

  hasCommittedRange() {
    return Boolean(
      this.element.dataset.summarySelectedStart &&
        this.element.dataset.summarySelectedEnd,
    );
  }

  clearCommittedSelection() {
    this.element.dataset.summarySelectedStart = "";
    this.element.dataset.summarySelectedEnd = "";
    this.element
      .querySelectorAll("[data-summary-calendar-day]")
      .forEach((dayElement) => {
        dayElement.classList.remove(
          "is-selected",
          "is-in-range",
          "is-range-start",
          "is-range-end",
          "is-draft-start",
        );
      });
  }

  updateDraftState() {
    this.element
      .querySelectorAll("[data-summary-calendar-day]")
      .forEach((dayElement) => {
        dayElement.classList.toggle(
          "is-draft-start",
          dayElement.dataset.summaryCalendarDay === this.draftStart,
        );
      });
  }

  applyRange(startValue, endValue) {
    const [start, end] = [startValue, endValue].sort();
    const url = new URL(this.element.dataset.summaryRangeUrl, window.location.origin);
    url.searchParams.set("date_filter_start", start);
    url.searchParams.set("date_filter_end", end);
    this.visitStream(url.toString());
  }

  async visitStream(url) {
    const requestUrl = stripPagePreferenceParams(url);

    try {
      const headers = {
        Accept: "text/vnd.turbo-stream.html",
      };
      const pageHeaders = buildPagePreferenceRequestHeaders();
      if (pageHeaders) {
        Object.assign(headers, pageHeaders);
      }

      const response = await fetch(requestUrl, {
        headers,
        credentials: "same-origin",
      });

      if (!response.ok) {
        throw new Error(`Unexpected status ${response.status}`);
      }

      const contentType = response.headers.get("content-type") || "";
      const html = await response.text();
      if (!contentType.includes("text/vnd.turbo-stream.html")) {
        throw new Error("Expected turbo stream response");
      }

      Turbo.renderStreamMessage(html);
      window.history.pushState({}, "", requestUrl);
      this.draftStart = null;
    } catch (_error) {
      persistOpenDrawerState();
      Turbo.visit(requestUrl);
    }
  }

  applyStoredDisplayPreferences() {
    const preferenceState = buildStoredSummaryPreferenceState({
      ...getSummaryStateFromElement(this.element),
      hasSelectedRange: this.hasCommittedRange(),
      ...getStoredSummaryState(),
    });

    if (!preferenceState) {
      return false;
    }

    storeSummaryPreferences(preferenceState);

    this.visitStream(window.location.href);
    return true;
  }

  syncStoredDisplayPreferencesFromDom() {
    storeRenderedSummaryPreferences(getSummaryStateFromElement(this.element));
  }

  persistDisplayPreferenceFromLink(link) {
    storeSummaryPreferenceTargets({
      targetMode: link.dataset.summaryTargetMode,
      targetMonth: link.dataset.summaryTargetMonth,
      targetWeek: link.dataset.summaryTargetWeek,
      targetShowWeekdays: link.dataset.summaryTargetShowWeekdays,
      targetShowDetails: link.dataset.summaryTargetShowDetails,
    });
  }

  initializeHeatmapLayout() {
    if (!this.element.querySelector("[data-summary-heatmap]")) {
      return;
    }

    this.syncHeatmapLayout();

    if (typeof ResizeObserver === "function") {
      this.heatmapResizeObserver = new ResizeObserver(() => {
        this.syncHeatmapLayout();
      });

      this.heatmapResizeObserver.observe(this.element);
      return;
    }

    window.addEventListener("resize", this.handleWindowResize);
  }

  handleWindowResize() {
    this.syncHeatmapLayout();
  }

  syncHeatmapLayout() {
    const heatmap = this.element.querySelector("[data-summary-heatmap]");
    const weeksTrack = heatmap?.querySelector("[data-summary-heatmap-weeks]");
    const headersTrack = heatmap?.querySelector("[data-summary-heatmap-week-headers]");

    if (!heatmap || !weeksTrack || !headersTrack) {
      return;
    }

    const weekColumns = Array.from(
      weeksTrack.querySelectorAll(".summary-heatmap-week"),
    );
    const headerColumns = Array.from(
      headersTrack.querySelectorAll(".summary-heatmap-week-number"),
    );
    const totalColumns =
      Math.min(weekColumns.length, headerColumns.length) || weekColumns.length;

    if (!totalColumns) {
      return;
    }

    const styles = getComputedStyle(heatmap);
    const availableWidth = weeksTrack.getBoundingClientRect().width;
    const gap =
      parseFloat(styles.getPropertyValue("--summary-heatmap-gap")) ||
      parseFloat(getComputedStyle(weeksTrack).columnGap) ||
      0;
    const minColumnWidth =
      parseFloat(styles.getPropertyValue("--summary-heatmap-min-column-width")) ||
      DEFAULT_HEATMAP_MIN_COLUMN_WIDTH;
    const visibleColumns = computeVisibleHeatmapColumns({
      availableWidth,
      totalColumns,
      minColumnWidth,
      gap,
      minColumns: Math.min(DEFAULT_HEATMAP_MIN_VISIBLE_COLUMNS, totalColumns),
    });
    const columnVisibility = buildHeatmapColumnVisibility(
      totalColumns,
      visibleColumns,
    );

    heatmap.style.setProperty(
      "--summary-heatmap-visible-columns",
      String(visibleColumns),
    );
    heatmap.dataset.summaryHeatmapVisibleColumns = String(visibleColumns);

    weekColumns.forEach((column, index) => {
      column.hidden = !columnVisibility[index];
    });
    headerColumns.forEach((column, index) => {
      column.hidden = !columnVisibility[index];
    });
  }
}

registerBehavior("ld-sidebar-user-summary", SidebarUserSummaryBehavior);
