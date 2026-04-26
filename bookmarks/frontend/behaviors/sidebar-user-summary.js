import { Behavior, registerBehavior } from "./index";

const FILTER_DRAWER_REOPEN_KEY = "ld:reopen-filter-drawer";
const DEFAULT_HEATMAP_MIN_COLUMN_WIDTH = 18;
const DEFAULT_HEATMAP_MIN_VISIBLE_COLUMNS = 8;
const SUMMARY_MODE_STORAGE_KEY = "ld:user-summary-mode";
const SUMMARY_MONTH_STORAGE_KEY = "ld:user-summary-month";
const SUMMARY_WEEK_STORAGE_KEY = "ld:user-summary-week";
const SUMMARY_WEEKDAYS_STORAGE_KEY = "ld:user-summary-show-weekdays";
const SUMMARY_DETAILS_STORAGE_KEY = "ld:user-summary-show-details";
const SUMMARY_SELECTOR = "[ld-sidebar-user-summary]";
const SUMMARY_QUERY_PARAMS = [
  "summary_mode",
  "summary_month",
  "summary_week",
  "summary_year",
  "summary_month_number",
  "summary_show_weekdays",
  "summary_show_details",
];

export function parseStoredBooleanPreference(value) {
  if (value === "1") {
    return true;
  }
  if (value === "0") {
    return false;
  }
  return null;
}

function stripSummaryPreferenceParams(href) {
  const visibleUrl = new URL(href, "http://localhost");

  SUMMARY_QUERY_PARAMS.forEach((key) => {
    visibleUrl.searchParams.delete(key);
  });

  return visibleUrl.toString();
}

function buildStoredSummaryPreferenceState({
  currentMode,
  currentMonth,
  currentWeek,
  currentShowWeekdays,
  currentShowDetails,
  hasSelectedRange,
  storedMode,
  storedMonth,
  storedWeek,
  storedShowWeekdays,
  storedShowDetails,
}) {
  let changed = false;
  const targetState = {
    mode: currentMode,
    month: currentMonth,
    week: currentWeek,
    showWeekdays: currentShowWeekdays,
    showDetails: currentShowDetails,
  };

  if (storedMode && storedMode !== currentMode) {
    targetState.mode = storedMode;
    changed = true;
  }

  if (!hasSelectedRange) {
    if (
      targetState.mode === "calendar" &&
      storedMonth &&
      storedMonth !== currentMonth
    ) {
      targetState.month = storedMonth;
      changed = true;
    }

    if (
      targetState.mode === "heatmap" &&
      storedWeek &&
      storedWeek !== currentWeek
    ) {
      targetState.week = storedWeek;
      changed = true;
    }
  }

  if (storedShowWeekdays !== null && storedShowWeekdays !== currentShowWeekdays) {
    targetState.showWeekdays = storedShowWeekdays;
    changed = true;
  }

  if (storedShowDetails !== null && storedShowDetails !== currentShowDetails) {
    targetState.showDetails = storedShowDetails;
    changed = true;
  }

  return changed ? targetState : null;
}

export function buildSummaryRequestHeaders({
  currentMode,
  currentMonth,
  currentWeek,
  currentShowWeekdays,
  currentShowDetails,
  storedMode,
  storedMonth,
  storedWeek,
  storedShowWeekdays,
  storedShowDetails,
}) {
  const mode = storedMode || currentMode;
  const month = storedMonth || currentMonth;
  const week = storedWeek || currentWeek;
  const showWeekdays = storedShowWeekdays ?? currentShowWeekdays;
  const showDetails = storedShowDetails ?? currentShowDetails;

  if (!mode) {
    return null;
  }

  const headers = {
    "X-Linkding-Summary-Mode": mode,
    "X-Linkding-Summary-Show-Weekdays": showWeekdays ? "1" : "0",
    "X-Linkding-Summary-Show-Details": showDetails ? "1" : "0",
  };

  if (mode === "heatmap") {
    if (week) {
      headers["X-Linkding-Summary-Week"] = week;
    }
    return headers;
  }

  if (month) {
    headers["X-Linkding-Summary-Month"] = month;
  }
  return headers;
}

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

function getRenderedSummaryState() {
  const summaryElement = document.querySelector(SUMMARY_SELECTOR);
  if (!summaryElement) {
    return null;
  }

  return {
    currentMode: summaryElement.dataset.summaryMode,
    currentMonth: summaryElement.dataset.summaryMonth,
    currentWeek: summaryElement.dataset.summaryWeek,
    currentShowWeekdays: summaryElement.dataset.summaryShowWeekdays === "1",
    currentShowDetails: summaryElement.dataset.summaryShowDetails === "1",
  };
}

function getStoredSummaryState() {
  return {
    storedMode: window.localStorage.getItem(SUMMARY_MODE_STORAGE_KEY),
    storedMonth: window.localStorage.getItem(SUMMARY_MONTH_STORAGE_KEY),
    storedWeek: window.localStorage.getItem(SUMMARY_WEEK_STORAGE_KEY),
    storedShowWeekdays: parseStoredBooleanPreference(
      window.localStorage.getItem(SUMMARY_WEEKDAYS_STORAGE_KEY),
    ),
    storedShowDetails: parseStoredBooleanPreference(
      window.localStorage.getItem(SUMMARY_DETAILS_STORAGE_KEY),
    ),
  };
}

function applyRequestHeaders(headerBag, summaryHeaders) {
  if (headerBag instanceof Headers) {
    Object.entries(summaryHeaders).forEach(([key, value]) => {
      headerBag.set(key, value);
    });
    return;
  }

  Object.assign(headerBag, summaryHeaders);
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

    SidebarUserSummaryBehavior.installDrawerReopenHook();
    if (this.applyStoredDisplayPreferences()) {
      return;
    }
    this.syncStoredDisplayPreferencesFromDom();
    this.initializeHeatmapLayout();
  }

  static installDrawerReopenHook() {
    if (SidebarUserSummaryBehavior.drawerHookInstalled) {
      return;
    }

    document.addEventListener("turbo:load", () => {
      if (window.sessionStorage.getItem(FILTER_DRAWER_REOPEN_KEY) !== "1") {
        return;
      }

      window.sessionStorage.removeItem(FILTER_DRAWER_REOPEN_KEY);
      const trigger = document.querySelector("[ld-filter-drawer-trigger]");
      if (trigger && !document.querySelector(".filter-drawer.active")) {
        trigger.click();
      }
    });

    SidebarUserSummaryBehavior.drawerHookInstalled = true;
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
    const requestUrl = stripSummaryPreferenceParams(url);

    try {
      const response = await fetch(requestUrl, {
        headers: {
          Accept: "text/vnd.turbo-stream.html",
          ...this.buildSummaryRequestHeaders(),
        },
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
      this.persistDrawerState();
      Turbo.visit(requestUrl);
    }
  }

  persistDrawerState() {
    if (document.querySelector(".filter-drawer.active")) {
      window.sessionStorage.setItem(FILTER_DRAWER_REOPEN_KEY, "1");
    }
  }

  applyStoredDisplayPreferences() {
    const preferenceState = buildStoredSummaryPreferenceState({
      currentMode: this.element.dataset.summaryMode,
      currentMonth: this.element.dataset.summaryMonth,
      currentWeek: this.element.dataset.summaryWeek,
      currentShowWeekdays: this.element.dataset.summaryShowWeekdays === "1",
      currentShowDetails: this.element.dataset.summaryShowDetails === "1",
      hasSelectedRange: this.hasCommittedRange(),
      storedMode: window.localStorage.getItem(SUMMARY_MODE_STORAGE_KEY),
      storedMonth: window.localStorage.getItem(SUMMARY_MONTH_STORAGE_KEY),
      storedWeek: window.localStorage.getItem(SUMMARY_WEEK_STORAGE_KEY),
      storedShowWeekdays: parseStoredBooleanPreference(
        window.localStorage.getItem(SUMMARY_WEEKDAYS_STORAGE_KEY),
      ),
      storedShowDetails: parseStoredBooleanPreference(
        window.localStorage.getItem(SUMMARY_DETAILS_STORAGE_KEY),
      ),
    });

    if (!preferenceState) {
      return false;
    }

    if (preferenceState.mode) {
      window.localStorage.setItem(SUMMARY_MODE_STORAGE_KEY, preferenceState.mode);
    }
    if (preferenceState.month) {
      window.localStorage.setItem(SUMMARY_MONTH_STORAGE_KEY, preferenceState.month);
    }
    if (preferenceState.week) {
      window.localStorage.setItem(SUMMARY_WEEK_STORAGE_KEY, preferenceState.week);
    }
    window.localStorage.setItem(
      SUMMARY_WEEKDAYS_STORAGE_KEY,
      preferenceState.showWeekdays ? "1" : "0",
    );
    window.localStorage.setItem(
      SUMMARY_DETAILS_STORAGE_KEY,
      preferenceState.showDetails ? "1" : "0",
    );

    this.visitStream(window.location.href);
    return true;
  }

  syncStoredDisplayPreferencesFromDom() {
    window.localStorage.setItem(
      SUMMARY_MODE_STORAGE_KEY,
      this.element.dataset.summaryMode,
    );
    window.localStorage.setItem(
      SUMMARY_MONTH_STORAGE_KEY,
      this.element.dataset.summaryMonth || "",
    );
    window.localStorage.setItem(
      SUMMARY_WEEK_STORAGE_KEY,
      this.element.dataset.summaryWeek || "",
    );
    window.localStorage.setItem(
      SUMMARY_WEEKDAYS_STORAGE_KEY,
      this.element.dataset.summaryShowWeekdays === "1" ? "1" : "0",
    );
    window.localStorage.setItem(
      SUMMARY_DETAILS_STORAGE_KEY,
      this.element.dataset.summaryShowDetails === "1" ? "1" : "0",
    );
  }

  persistDisplayPreferenceFromLink(link) {
    const targetMode = link.dataset.summaryTargetMode;
    const targetMonth = link.dataset.summaryTargetMonth;
    const targetWeek = link.dataset.summaryTargetWeek;
    const targetShowWeekdays = link.dataset.summaryTargetShowWeekdays;
    const targetShowDetails = link.dataset.summaryTargetShowDetails;

    if (targetMode) {
      window.localStorage.setItem(SUMMARY_MODE_STORAGE_KEY, targetMode);
    }
    if (targetMonth) {
      window.localStorage.setItem(SUMMARY_MONTH_STORAGE_KEY, targetMonth);
    }
    if (targetWeek) {
      window.localStorage.setItem(SUMMARY_WEEK_STORAGE_KEY, targetWeek);
    }
    if (targetShowWeekdays === "1" || targetShowWeekdays === "0") {
      window.localStorage.setItem(
        SUMMARY_WEEKDAYS_STORAGE_KEY,
        targetShowWeekdays,
      );
    }
    if (targetShowDetails === "1" || targetShowDetails === "0") {
      window.localStorage.setItem(
        SUMMARY_DETAILS_STORAGE_KEY,
        targetShowDetails,
      );
    }
  }

  buildSummaryRequestHeaders() {
    return buildSummaryRequestHeaders({
      currentMode: this.element.dataset.summaryMode,
      currentMonth: this.element.dataset.summaryMonth,
      currentWeek: this.element.dataset.summaryWeek,
      currentShowWeekdays: this.element.dataset.summaryShowWeekdays === "1",
      currentShowDetails: this.element.dataset.summaryShowDetails === "1",
      storedMode: window.localStorage.getItem(SUMMARY_MODE_STORAGE_KEY),
      storedMonth: window.localStorage.getItem(SUMMARY_MONTH_STORAGE_KEY),
      storedWeek: window.localStorage.getItem(SUMMARY_WEEK_STORAGE_KEY),
      storedShowWeekdays: parseStoredBooleanPreference(
        window.localStorage.getItem(SUMMARY_WEEKDAYS_STORAGE_KEY),
      ),
      storedShowDetails: parseStoredBooleanPreference(
        window.localStorage.getItem(SUMMARY_DETAILS_STORAGE_KEY),
      ),
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

SidebarUserSummaryBehavior.drawerHookInstalled = false;

registerBehavior("ld-sidebar-user-summary", SidebarUserSummaryBehavior);

document.addEventListener("turbo:before-fetch-request", (event) => {
  const summaryHeaders = buildSummaryRequestHeaders({
    ...getRenderedSummaryState(),
    ...getStoredSummaryState(),
  });

  if (!summaryHeaders) {
    return;
  }

  event.detail.fetchOptions.headers ||= {};
  applyRequestHeaders(event.detail.fetchOptions.headers, summaryHeaders);
});
