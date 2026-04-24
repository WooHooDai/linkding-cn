# i18n 维护说明

本项目使用 Django 原生 gettext 方案维护国际化。

当前基线如下：

- 源语言：英文 `en`
- 首个翻译包：简体中文 `zh_Hans`（运行时语言代码为 `zh-hans`）
- 翻译域：
  - `django`：模板与 Python 文案
  - `djangojs`：前端 JavaScript 文案

## 核心约束

1. 所有用户可见源码文案统一使用英文。
2. 不要在模板、Python、JavaScript 中直接硬编码中文等翻译结果。
3. 所有翻译只维护在 `locale/<language>/LC_MESSAGES/*.po` 中。
4. 任何 `.po` 改动后，都必须重新执行 `compilemessages`。
5. 新增语言时，只新增 locale 翻译包，不复制业务模板或业务代码。

## 相关文件

当前中文翻译文件：

- `locale/zh_Hans/LC_MESSAGES/django.po`
- `locale/zh_Hans/LC_MESSAGES/djangojs.po`

语言配置与切换相关代码：

- `bookmarks/settings/base.py`
- `bookmarks/middlewares.py`
- `bookmarks/urls.py`
- `bookmarks/templates/shared/language_switcher.html`

当前语言解析优先级：

1. 登录用户的个人语言设置
2. Django `set_language` 写入的 cookie / session
3. 浏览器 `Accept-Language`
4. 默认英文

## 新增文案时应该怎么写

### 模板

模板中使用 Django i18n 标签：

```django
{% load i18n %}
{% translate "Save" %}
{% blocktranslate trimmed with count=item_count %}
  {{ count }} item selected
{% endblocktranslate %}
```

规则：

- 简单静态文案用 `{% translate %}`
- 带变量或多行句子用 `{% blocktranslate %}`

### Python

Python 中使用 Django gettext：

```python
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
```

建议：

- 运行时文案使用 `gettext` / `_()`
- 模型字段名、表单字段名、choices 等在 import 阶段定义的文案使用 `gettext_lazy`
- 复数文案使用 `ngettext`

### JavaScript

JavaScript 文案通过 `gettext(...)` 提取到 `djangojs.po`。

统一使用：

- `bookmarks/frontend/behaviors/i18n.js`

示例：

```js
import { gettext } from "./i18n";

button.textContent = gettext("Confirm");
```

注意：

- JavaScript 中必须保留 `gettext(...)` 这个函数名，`makemessages -d djangojs` 才能正确提取
- 不要随意改成别的包装函数名，除非同时更新提取策略

## 日常维护流程

当你新增或修改了任何用户可见文案时，按下面流程执行：

1. 先把源码文案改成英文
2. 提取最新消息到 `.po`
3. 翻译对应语言的 `msgstr`
4. 编译 `.mo`
5. 运行相关测试并重建前端资源

### 提取模板 / Python 文案

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py makemessages -l zh_Hans \
  --ignore ".venv/*" \
  --ignore "node_modules/*" \
  --ignore "docs/*" \
  --ignore "chromium-profile/*"
```

### 提取 JavaScript 文案

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py makemessages -d djangojs -l zh_Hans \
  --ignore ".venv/*" \
  --ignore "node_modules/*" \
  --ignore "docs/*" \
  --ignore "chromium-profile/*"
```

### 编译翻译文件

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py compilemessages
```

### 重建前端资源

```bash
npm run build
```

## 如何新增一个语言

假设要新增法语 `fr`。

### 第 1 步：注册语言

在 `bookmarks/settings/base.py` 的 `LANGUAGES` 中加入新语言，例如：

```python
LANGUAGES = [
    ("en", "English"),
    ("zh-hans", "Simplified Chinese"),
    ("fr", "French"),
]
```

### 第 2 步：生成 locale 翻译包

推荐做法：直接从源码提取。

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py makemessages -l fr \
  --ignore ".venv/*" \
  --ignore "node_modules/*" \
  --ignore "docs/*" \
  --ignore "chromium-profile/*"

DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py makemessages -d djangojs -l fr \
  --ignore ".venv/*" \
  --ignore "node_modules/*" \
  --ignore "docs/*" \
  --ignore "chromium-profile/*"
```

执行后会生成：

- `locale/fr/LC_MESSAGES/django.po`
- `locale/fr/LC_MESSAGES/djangojs.po`

### 第 3 步：逐条翻译 `msgstr`

把下面两个文件中的 `msgstr` 逐条翻译完成：

- `locale/fr/LC_MESSAGES/django.po`
- `locale/fr/LC_MESSAGES/djangojs.po`

这里的英文 `msgid` 就是“英文模板字段”。

也就是说，后续新增语言时，维护方式已经变成：

- 不复制业务模板文件
- 只对英文 `msgid` 对应的 `msgstr` 逐条翻译

这就是当前方案下，最接近“复制英文模板后逐字段翻译”的标准化做法，而且比复制模板更易维护。

### 第 4 步：编译并验证

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py compilemessages

npm run build
```

然后至少验证：

- 登录页可以切换到新语言
- 设置页保存语言后可持久生效
- JavaScript 的确认弹窗 / 筛选器等文案也会切换

## 可选的快速引导方式

如果你希望先复制一份现有翻译包作为起点，也可以把：

- `locale/zh_Hans`

复制为：

- `locale/<new_language>`

然后：

1. 修改 PO 头信息（如 `Language`、`Language-Team`）
2. 把其中的 `msgstr` 改成目标语言
3. 执行 `compilemessages`

这可以作为快速起步方式；但从长期维护角度，仍然推荐优先用 `makemessages` 从源码重新生成。

## 合并前检查清单

合并 i18n 相关改动前，至少执行：

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py compilemessages

npm run build
```

建议再跑这组聚焦测试：

```bash
DJANGO_SETTINGS_MODULE=bookmarks.settings.base \
.venv/bin/python manage.py test \
  bookmarks.tests.test_i18n \
  bookmarks.tests.test_settings_general_view \
  bookmarks.tests.test_bookmarks_list_template \
  bookmarks.tests.test_utils -v 1
```

## 常见坑

- `makemessages` 可能扫到 `chromium-profile` 下的缓存文件，记得加 ignore
- 英文源码文案一旦改动，对应 `msgid` 就变了，翻译条目也要同步更新
- 只改 `.po` 不改 `.mo`，运行时不会生效
- JavaScript 如果不是通过 `gettext(...)` 调用，`djangojs.po` 无法自动提取
- 测试默认应保持英文优先，只对语言切换和翻译渲染补充定向测试

## 一句话总结

请始终记住：

- 代码里的用户可见源码文案保持英文
- 翻译只放在 `locale/`
- 新增语言靠新增 locale pack，不靠复制业务模板或 Python 文件
