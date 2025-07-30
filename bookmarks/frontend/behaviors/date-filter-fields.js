import { Behavior, registerBehavior } from "./index";

// 常量配置
const CONFIG = {
  SELECTORS: {
    GROUPS: {
      BY: '#date-filter-by-group',
      TYPE: '#date-filter-type-fields',
      ABSOLUTE: '#date-filter-absolute-fields',
      RELATIVE: '#date-filter-relative-fields',
      PRESET: '#date-filter-relative-preset-fields',
      CUSTOM: '#date-filter-relative-custom-fields'
    },
    RADIOS: {
      BY: 'input[name="date_filter_by"]',
      TYPE: 'input[name="date_filter_type"]',
      MODE: 'input[name="relative_filter_mode"]'
    },
    FIELDS: {
      CUSTOM_VALUE: 'input[name="date_filter_relative_value"]',
      CUSTOM_UNIT: 'select[name="date_filter_relative_unit"]',
      PRESET: 'select[name="date_filter_relative_preset"]',
      HIDDEN: 'input[name="date_filter_relative_string"]'
    }
  },
  VALUES: {
    BY: {
      OFF: 'off'
    },
    TYPE: {
      ABSOLUTE: 'absolute',
      RELATIVE: 'relative'
    },
    MODE: {
      PRESET: 'preset',
      CUSTOM: 'custom'
    }
  },

};

class DateFilterFieldsBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.initElements();
    this.bindEventHandlers();
    this.updateVisibility();
    this.updateRelativeString();
    this.initFromSavedDefaultValues();
  }

  initElements() {
    this.elements = {
      groups: {
        by: this.element.querySelector(CONFIG.SELECTORS.GROUPS.BY),
        type: this.element.querySelector(CONFIG.SELECTORS.GROUPS.TYPE),
        absolute: this.element.querySelector(CONFIG.SELECTORS.GROUPS.ABSOLUTE),
        relative: this.element.querySelector(CONFIG.SELECTORS.GROUPS.RELATIVE),
        preset: this.element.querySelector(CONFIG.SELECTORS.GROUPS.PRESET),
        custom: this.element.querySelector(CONFIG.SELECTORS.GROUPS.CUSTOM)
      },
      radios: {
        by: this.element.querySelectorAll(CONFIG.SELECTORS.RADIOS.BY),
        type: this.element.querySelectorAll(CONFIG.SELECTORS.RADIOS.TYPE),
        mode: this.element.querySelectorAll(CONFIG.SELECTORS.RADIOS.MODE)
      },
      fields: {
        customValue: this.element.querySelector(CONFIG.SELECTORS.FIELDS.CUSTOM_VALUE),
        customUnit: this.element.querySelector(CONFIG.SELECTORS.FIELDS.CUSTOM_UNIT),
        preset: this.element.querySelector(CONFIG.SELECTORS.FIELDS.PRESET)
      }
    };
    
    this.form = this.element.closest('form');
  }

  bindEventHandlers() {
    this.bindRadioEvents();
    this.bindFieldEvents();
    if (this.form) {
      this.form.addEventListener('submit', this.handleFormSubmit.bind(this));
    }
  }

  bindRadioEvents() {
    const { by, type, mode } = this.elements.radios;
    by.forEach(radio => radio.addEventListener('change', () => this.updateVisibility()));
    if (type.length) {
      type.forEach(radio => radio.addEventListener('change', () => this.updateVisibility()));
    }
    if (mode.length) {
      mode.forEach(radio => radio.addEventListener('change', () => this.updateVisibility()));
    }
  }

  bindFieldEvents() {
    const { customValue, customUnit, preset } = this.elements.fields;
    
    if (customValue) {
      customValue.addEventListener('input', () => this.updateRelativeString());
    }
    
    if (customUnit) {
      customUnit.addEventListener('change', () => this.updateRelativeString());
    }
    
    if (preset) {
      preset.addEventListener('change', () => this.updateRelativeString());
    }
    
    // 监听单选按钮变化，实时更新相对日期字符串
    const modeRadios = this.element.querySelectorAll('input[name="relative_filter_mode"]');
    modeRadios.forEach(radio => {
      radio.addEventListener('change', () => {
        this.updateRelativeString();
      });
    });
  }

  destroy() {
    this.elements.radios.by.forEach(radio => 
      radio.removeEventListener('change', () => this.updateVisibility())
    );
    this.elements.radios.type.forEach(radio => 
      radio.removeEventListener('change', () => this.updateVisibility())
    );
    this.elements.radios.mode.forEach(radio => 
      radio.removeEventListener('change', () => this.updateVisibility())
    );

    const { customValue, customUnit, preset } = this.elements.fields;
    if (customValue) {
      customValue.removeEventListener('input', () => this.updateRelativeString());
    }
    if (customUnit) {
      customUnit.removeEventListener('change', () => this.updateRelativeString());
    }
    if (preset) {
      preset.removeEventListener('change', () => this.updateRelativeString());
    }

    if (this.form) {
      this.form.removeEventListener('submit', this.handleFormSubmit.bind(this));
    }
  }

  // 获取选中的radio值
  getSelectedValue(container, name) {
    if (!container) return null;
    const checked = container.querySelector(`input[name="${name}"]:checked`);
    return checked ? checked.value : null;
  }

  // 控制日期筛选项间的联动
  updateVisibility() {
    const selectedBy = this.getSelectedValue(this.elements.groups.by, 'date_filter_by');
    const selectedType = this.getSelectedValue(this.elements.groups.type, 'date_filter_type');
    const selectedMode = this.getSelectedValue(this.elements.groups.relative, 'relative_filter_mode');

    if (selectedBy === CONFIG.VALUES.BY.OFF) {
      this.hideAllGroups();
    } else {
      this.showTypeGroup();
      this.updateTypeVisibility(selectedType, selectedMode);
    }
  }

  hideAllGroups() {
    const { type, absolute, relative } = this.elements.groups;
    if (type) type.style.display = 'none';
    if (absolute) absolute.style.display = 'none';
    if (relative) relative.style.display = 'none';
  }

  showTypeGroup() {
    if (this.elements.groups.type) {
      this.elements.groups.type.style.display = '';
    }
  }

  updateTypeVisibility(selectedType, selectedMode) {
    const { absolute, relative } = this.elements.groups;
    
    if (selectedType === CONFIG.VALUES.TYPE.ABSOLUTE) {
      if (absolute) absolute.style.display = '';
      if (relative) relative.style.display = 'none';
    } else if (selectedType === CONFIG.VALUES.TYPE.RELATIVE) {
      if (absolute) absolute.style.display = 'none';
      if (relative) relative.style.display = '';
      this.updateRelativeModeVisibility(selectedMode);
    }
  }

  updateRelativeModeVisibility(selectedMode) {
    const { preset, custom } = this.elements.groups;
    
    if (preset) preset.style.display = '';
    if (custom) custom.style.display = '';
    
    if (selectedMode === CONFIG.VALUES.MODE.PRESET) {
      this.setFieldsDisabled({ preset: false, custom: true });
    } else if (selectedMode === CONFIG.VALUES.MODE.CUSTOM) {
      this.setFieldsDisabled({ preset: true, custom: false });
    }
  }

  setFieldsDisabled({ preset, custom }) {
    const { preset: presetField, customValue, customUnit } = this.elements.fields;
    
    if (presetField) presetField.disabled = preset;
    if (customValue) customValue.disabled = custom;
    if (customUnit) customUnit.disabled = custom;
  }

  // 更新相对日期字符串
  updateRelativeString() {
    const selectedBy = this.getSelectedValue(this.elements.groups.by, 'date_filter_by');
    const selectedType = this.getSelectedValue(this.elements.groups.type, 'date_filter_type');
    const selectedMode = this.getSelectedValue(this.elements.groups.relative, 'relative_filter_mode');

    if (selectedBy === CONFIG.VALUES.BY.OFF || selectedType === CONFIG.VALUES.TYPE.ABSOLUTE) {
      this.clearHiddenField();
      return;
    }

    if (selectedType === CONFIG.VALUES.TYPE.RELATIVE) {
      if (selectedMode === CONFIG.VALUES.MODE.CUSTOM) {
        this.updateCustomRelativeString();
      } else {
        this.updatePresetRelativeString();
      }
    }
  }

  updateCustomRelativeString() {
    const { customValue, customUnit } = this.elements.fields;
    
    if (customValue && customUnit && customValue.value && customUnit.value) {
      const value = customValue.value;
      const unit = customUnit.value;
      const relativeString = `last_${value}_${unit}`;
      this.setHiddenField(relativeString);
    } else {
      this.clearHiddenField();
    }
  }

  updatePresetRelativeString() {
    const { preset } = this.elements.fields;
    if (preset && preset.value) {
      this.setHiddenField(preset.value);
    } else {
      this.setHiddenField('yesterday');
    }
  }

  setHiddenField(value) {
    let hiddenField = this.form.querySelector(CONFIG.SELECTORS.FIELDS.HIDDEN); 
    if (!hiddenField) {
      hiddenField = document.createElement('input');
      hiddenField.type = 'hidden';
      hiddenField.name = 'date_filter_relative_string';
      this.form.appendChild(hiddenField);
    }
    
    hiddenField.value = value;
  }

  clearHiddenField() {
    const hiddenField = this.form.querySelector(CONFIG.SELECTORS.FIELDS.HIDDEN);
    if (hiddenField) {
      hiddenField.value = '';
    }
  }

  // 表单提交处理
  handleFormSubmit(event) {
    const selectedBy = this.getSelectedValue(this.elements.groups.by, 'date_filter_by');
    const selectedType = this.getSelectedValue(this.elements.groups.type, 'date_filter_type');
    if (selectedBy === CONFIG.VALUES.BY.OFF) {
      this.clearAllFields();
    } else if (selectedType === CONFIG.VALUES.TYPE.ABSOLUTE) {
      this.clearRelativeFields();
    } else if (selectedType === CONFIG.VALUES.TYPE.RELATIVE) {
      this.clearAbsoluteFields();
      this.updateRelativeString();
    }
  }

  clearAllFields() {
    const fields = [
      'date_filter_by',
      'date_filter_type', 
      'date_filter_start',
      'date_filter_end',
      'date_filter_relative_string'
    ];
    
    fields.forEach(field => this.clearField(field));
  }

  clearRelativeFields() {
    this.clearField('date_filter_relative_string');
  }

  clearAbsoluteFields() {
    this.clearField('date_filter_start');
    this.clearField('date_filter_end');
  }

  clearField(fieldName) {
    const field = this.form.querySelector(`[name="${fieldName}"]`);
    if (field) {
      field.value = '';
      field.disabled = false;
    }
  }

  // 根据保存的值初始化表单字段
  initFromSavedDefaultValues() {
    const hiddenField = this.form.querySelector(CONFIG.SELECTORS.FIELDS.HIDDEN);
    if (!hiddenField || !hiddenField.value) {
      return;
    }

    const relativeString = hiddenField.value;
    
    // 检查是否是预设值
    const presetValues = ['today', 'yesterday', 'this_week', 'this_month', 'this_year'];
    if (presetValues.includes(relativeString)) {
      // 设置预设模式
      const presetRadio = this.element.querySelector('input[name="relative_filter_mode"][value="preset"]');
      if (presetRadio) {
        presetRadio.checked = true;
      }
      
      // 设置预设选择
      const presetSelect = this.element.querySelector(CONFIG.SELECTORS.FIELDS.PRESET);
      if (presetSelect) {
        presetSelect.value = relativeString;
      }
    } else {
      // 检查是否是自定义格式 (last_X_unit)
      const match = relativeString.match(/^last_(\d+)_(day|week|month|year)s?$/);
      if (match) {
        const [, value, unit] = match;
        
        // 设置自定义模式
        const customRadio = this.element.querySelector('input[name="relative_filter_mode"][value="custom"]');
        if (customRadio) {
          customRadio.checked = true;
        }
        
        // 设置自定义值
        const customValueField = this.element.querySelector(CONFIG.SELECTORS.FIELDS.CUSTOM_VALUE);
        if (customValueField) {
          customValueField.value = value;
        }
        
        // 设置自定义单位
        const customUnitField = this.element.querySelector(CONFIG.SELECTORS.FIELDS.CUSTOM_UNIT);
        if (customUnitField) {
          customUnitField.value = unit + 's'; // 添加复数形式
        }
      }
    }
    
    // 更新可见性和状态
    this.updateVisibility();
    
    // 触发change事件以确保界面正确更新
    const selectedMode = this.getSelectedValue(this.elements.groups.relative, 'relative_filter_mode');
    if (selectedMode) {
      this.updateRelativeModeVisibility(selectedMode);
    }
    
    // 触发单选按钮的change事件以确保界面正确更新
    const selectedRadio = this.element.querySelector('input[name="relative_filter_mode"]:checked');
    if (selectedRadio) {
      selectedRadio.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
}

registerBehavior('ld-date-filter-fields', DateFilterFieldsBehavior); 