import { Behavior, registerBehavior } from "./index";

class DateFilterFieldsBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.initElements();
    this.bindEventHandlers();
    this.updateVisibility();
    
    window.dateFilterBehavior = this; // 添加实例到全局，供特定页面使用（如预览页面）
  }

  initElements() {
    // 获取所有需要控制显示/隐藏的字段组
    this.elements = {
      groups: {
        type: this.element.querySelector('#date-filter-type-fields'),
        absolute: this.element.querySelector('#date-filter-absolute-fields'),
        relative: this.element.querySelector('#date-filter-relative-fields'),
        preset: this.element.querySelector('#date-filter-relative-preset-fields'),
        custom: this.element.querySelector('#date-filter-relative-custom-fields')
      },
      fields: {
        presetSelect: this.element.querySelector('select[name="date_filter_relative_preset"]'),
        customValue: this.element.querySelector('input[name="date_filter_relative_value"]'),
        customUnit: this.element.querySelector('select[name="date_filter_relative_unit"]'),
        relativeString: this.element.querySelector('input[name="date_filter_relative_string"]')
      }
    };
  }

  bindEventHandlers() {
    // 监听日期筛选方式变化
    const byRadios = this.element.querySelectorAll('input[name="date_filter_by"]');
    byRadios.forEach(radio => {
      radio.addEventListener('change', () => {
        this.updateVisibility();
        this.updateRelativeString();
      });
    });

    // 监听日期类型变化
    const typeRadios = this.element.querySelectorAll('input[name="date_filter_type"]');
    typeRadios.forEach(radio => {
      radio.addEventListener('change', () => {
        this.updateVisibility();
        this.updateRelativeString();
      });
    });

    // 监听相对日期模式变化
    const modeRadios = this.element.querySelectorAll('input[name="relative_filter_mode"]');
    this.updateRelativeString();
    modeRadios.forEach(radio => {
      radio.addEventListener('change', () => {
        this.updateVisibility();
        this.updateRelativeString();
      });
    });

    // 监听预设选项变化
    if (this.elements.fields.presetSelect) {
      this.elements.fields.presetSelect.addEventListener('change', () => this.updateRelativeString());
    }

    // 监听自定义值变化
    if (this.elements.fields.customValue) {
      this.elements.fields.customValue.addEventListener('input', () => this.updateRelativeString());
    }

    // 监听自定义单位变化
    if (this.elements.fields.customUnit) {
      this.elements.fields.customUnit.addEventListener('change', () => this.updateRelativeString());
    }
  }

  // 获取选中的radio值
  getSelectedValue(name) {
    const checked = this.element.querySelector(`input[name="${name}"]:checked`);
    return checked ? checked.value : null;
  }

  // 更新字段显示/隐藏状态
  updateVisibility() {
    const selectedBy = this.getSelectedValue('date_filter_by');
    const selectedType = this.getSelectedValue('date_filter_type');
    const selectedMode = this.getSelectedValue('relative_filter_mode');

    // 如果选择关闭，隐藏所有相关字段
    if (selectedBy === 'off') {
      this.hideAllGroups();
      this.clearRelativeString();
      return;
    }

    // 显示类型选择
    if (this.elements.groups.type) {
      this.elements.groups.type.style.display = '';
    }

    // 根据选择的类型显示对应字段
    if (selectedType === 'absolute') {
      this.elements.groups.absolute.style.display = '';
      this.elements.groups.relative.style.display = 'none';
      this.clearRelativeString();
    } else if (selectedType === 'relative') {
      this.elements.groups.absolute.style.display = 'none';
      this.elements.groups.relative.style.display = '';
      this.elements.groups.preset.style.display = '';
      this.elements.groups.custom.style.display = '';

      // 根据相对日期模式设置字段状态
      this.setFieldsState({
        preset: selectedMode === 'preset',
        custom: selectedMode === 'custom'
      });
    }
  }

  hideAllGroups() {
    Object.values(this.elements.groups).forEach(group => {
      if (group) group.style.display = 'none';
    });
  }

  // 设置字段启用/禁用状态
  setFieldsState({ preset, custom }) {
    // 处理预设字段
    const presetSelect = this.elements.groups.preset.querySelector('select');
    if (presetSelect) presetSelect.disabled = !preset;

    // 处理自定义字段
    const customInputs = [this.elements.fields.customValue, this.elements.fields.customUnit];
    customInputs.forEach(input => {
      if (input) input.disabled = !custom;
    });
  }

  // 更新相对日期字符串
  updateRelativeString() {
    const selectedBy = this.getSelectedValue('date_filter_by');
    const selectedType = this.getSelectedValue('date_filter_type');
    const selectedMode = this.getSelectedValue('relative_filter_mode');

    if (selectedBy === 'off' || selectedType !== 'relative') {
      this.clearRelativeString();
      return;
    }

    if (selectedMode === 'preset') {
      const presetValue = this.elements.fields.presetSelect.value;
      if (presetValue) {
        this.setRelativeString(presetValue);
      }
    } else if (selectedMode === 'custom') {
      const value = this.elements.fields.customValue.value;
      const unit = this.elements.fields.customUnit.value;
      if (value && unit) {
        this.setRelativeString(`last_${value}_${unit}`);
      } else {
        this.clearRelativeString();
      }
    }
  }

  setRelativeString(value) {
    if (this.elements.fields.relativeString) {
      this.elements.fields.relativeString.value = value;
    }
  }

  clearRelativeString() {
    this.setRelativeString('');
  }

  destroy() {
    const radios = this.element.querySelectorAll('input[type="radio"]');
    radios.forEach(radio => {
      radio.removeEventListener('change', () => this.updateVisibility());
    });

    if (this.elements.fields.presetSelect) {
      this.elements.fields.presetSelect.removeEventListener('change', () => this.updateRelativeString());
    }

    if (this.elements.fields.customValue) {
      this.elements.fields.customValue.removeEventListener('input', () => this.updateRelativeString());
    }

    if (this.elements.fields.customUnit) {
      this.elements.fields.customUnit.removeEventListener('change', () => this.updateRelativeString());
    }
  }
}

registerBehavior('ld-date-filter-fields', DateFilterFieldsBehavior);