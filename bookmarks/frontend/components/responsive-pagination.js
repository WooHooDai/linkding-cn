export class ResponsivePagination extends HTMLElement {
  connectedCallback() {
    this.adjust = this.adjust.bind(this);
    this.ro = new ResizeObserver(this.adjust);
    this.ro.observe(this);
  }

  disconnectedCallback() {
    if (this.ro) {
      this.ro.disconnect();
    }
  }

  adjust() {
    const pagesContainer = this.querySelector('.pagination-pages');
    const ul = pagesContainer?.querySelector('ul');
    if (!pagesContainer || !ul) return;

    const items = Array.from(ul.querySelectorAll('.page-item[data-number]'));
    if (items.length === 0) return;

    // Filter out items that should always be visible (first, last, current)
    const hideableItems = items.filter(item => 
      !item.classList.contains('is-first') && 
      !item.classList.contains('is-last') && 
      !item.classList.contains('active')
    );
    
    // Sort by distance descending
    hideableItems.sort((a, b) => parseInt(b.dataset.distance) - parseInt(a.dataset.distance));

    const updateGaps = () => {
      // Remove all existing gaps
      ul.querySelectorAll('.gap').forEach(g => g.remove());
      
      const visibleItems = items.filter(item => item.style.display !== 'none');
      // Sort visible items by page number
      visibleItems.sort((a, b) => parseInt(a.dataset.number) - parseInt(b.dataset.number));
      
      // Add gaps where page numbers are not consecutive
      for (let i = 0; i < visibleItems.length - 1; i++) {
        const currentNum = parseInt(visibleItems[i].dataset.number);
        const nextNum = parseInt(visibleItems[i+1].dataset.number);
        if (nextNum - currentNum > 1) {
          const gap = document.createElement('li');
          gap.className = 'page-item gap';
          gap.style.flexShrink = '0';
          gap.innerHTML = '<span>...</span>';
          visibleItems[i].after(gap);
        }
      }
    };

    // Initially show all
    items.forEach(i => i.style.display = '');
    updateGaps();

    // Check if we need to hide items due to overflow
    for (const item of hideableItems) {
      // +2 is a small margin of error
      if (pagesContainer.scrollWidth <= pagesContainer.clientWidth + 2) {
        break;
      }
      item.style.display = 'none';
      updateGaps();
    }
  }
}

customElements.define("ld-responsive-pagination", ResponsivePagination);
