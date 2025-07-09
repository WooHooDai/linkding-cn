import { Behavior, registerBehavior } from "./index";

class DateFilterFieldsBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.onRadioChange = this.onRadioChange.bind(this);
    this.radios = element.querySelectorAll('input[name="date_filter_type"]');
    this.rangeFields = element.querySelector('#date-range-fields');
    if (this.radios.length && this.rangeFields) {
      this.radios.forEach(radio => {
        radio.addEventListener('change', this.onRadioChange);
      });
      this.onRadioChange();
    }
  }

  destroy() {
    if (this.radios) {
      this.radios.forEach(radio => {
        radio.removeEventListener('change', this.onRadioChange);
      });
    }
  }

  onRadioChange() {
    const checked = this.element.querySelector('input[name="date_filter_type"]:checked');
    if (checked && checked.value !== 'off') {
      this.rangeFields.style.display = '';
    } else {
      this.rangeFields.style.display = 'none';
    }
  }
}

registerBehavior('ld-date-filter-fields', DateFilterFieldsBehavior); 