# script-hooks.md — 脚本钩子 API 参考（中文）

本文档介绍 linkding-cn Site Adapters 的自定义脚本钩子系统。脚本钩子目前只用于 `metadata` 和 `snapshot` 两个 section；`reader` 模式不支持自定义脚本。

## 执行生命周期

```text
before hooks -> [replace hook 或 内置引擎] -> after hooks
```

- `before`：在主引擎运行前执行。可以修改配置、预取内容，或在快照流程中返回 HTML。
- `replace`：完全替换内置引擎，由脚本自己完成全部工作。
- `after`：在内置引擎（或 replace 钩子）之后执行，可以修改结果或输出文件。

`scripts` 数组中可定义多个 `before` 和 `after` 钩子；每个 section 只允许一个 `replace` 钩子。

## 脚本类型

| 类型 | 执行方式 | 运行环境 |
|------|----------|----------|
| `.py` | 进程内线程 | Python，可使用 linkding-cn 内部导入 |
| `.js`（snapshot，`builtin_engine = "singlefile"`） | 在 SingleFile 浏览器内执行 | 浏览器 DOM API，无 Node API |
| `.js`（snapshot，`builtin_engine = ""` 或 `null`） | 独立 Node 进程 | 完整 Node.js API |
| `.js`（metadata） | 独立 Node 进程 | 完整 Node.js API |

## 所有钩子可用的配置键

传入脚本的配置已经过清洗：内部 `_` 前缀键会被映射为面向用户的名称。

### 通用键

| 键 | 类型 | 说明 |
|-----|------|------|
| `headers` | dict | HTTP 请求头 |
| `timeout` | int\|null | 超时时间（秒） |
| `proxy` | str\|null | HTTP 代理 URL |
| `request_url` | str\|null | 已解析的实际请求 URL（来自 `request_url` 规则） |
| `rewrite_url` | str\|null | 已解析的重写 URL（仅 metadata） |
| `auth` | dict | 合并后的认证配置 |
| `cookie` | dict | Cookie 配置 |
| `user_cookie` | str\|null | 当前可用的最佳 Cookie 字符串 |
| `domain_key` | str | 命中的域名 key |

### metadata 专属键

| 键 | 类型 | 说明 |
|-----|------|------|
| `select_title` | list[str] | 标题 CSS 选择器 |
| `select_description` | list[str] | 描述 CSS 选择器 |
| `select_image` | list[str] | 预览图 CSS 选择器 |
| `rewrite_title` | list\|null | 标题重写规则 |
| `rewrite_description` | list\|null | 描述重写规则 |
| `rewrite_image` | list\|null | 预览图 URL 重写规则 |
| `load_full_page` | bool | 是否加载完整页面 |
| `scripts` | list[dict] | 脚本钩子配置 |

### snapshot 专属键

| 键 | 类型 | 说明 |
|-----|------|------|
| `keep_elements` | list[str] | 快照中保留的 CSS 选择器 |
| `remove_elements` | list[str] | 快照中移除的 CSS 选择器 |
| `process_lazy_images` | bool\|list[str] | 懒加载图片处理 |
| `remove_classes` | dict | 要移除的 CSS class |
| `set_styles` | dict | 要设置的内联样式 |
| `singlefile_args` | dict | SingleFile CLI 参数 |
| `toggles` | dict | 用户开关控件配置 |
| `scripts` | list[dict] | 脚本钩子配置 |

## metadata 钩子

### Python metadata 钩子

```python
def before(url: str, config: dict) -> dict | None:
    """返回一个局部配置 dict，在主引擎前合并；无需修改时返回 None。
    支持的返回键：request_url、user_cookie、headers、timeout、proxy
    """
    return None

def replace(url: str, config: dict) -> dict:
    """完全替换内置引擎。框架不会自动发起 HTTP 请求。
    返回：{"title": str|None, "description": str|None, "image": str|None, "url": str|None}
    """
    import requests
    resp = requests.get(url, headers=config.get("headers", {}))
    # ... 解析 resp.text ...
    return {"title": "...", "description": None, "image": None, "url": url}

def after(result: dict, url: str, config: dict) -> None:
    """原地修改 result。result 键：title、description、image、url"""
    if result.get("title"):
        result["title"] = result["title"].replace(" - Suffix", "")
```

metadata 的 `replace` / `before` 返回带 `error` 字段的 dict 时表示**主动放弃抓取并给出原因**：结果为兜底空元数据，测试面板把原因显示到 `metadata_error`（正式抓取行为不变）。与 snapshot 的拒绝契约一致，只是不会中断整条流水线。

### JavaScript metadata 钩子

stdin 输入 JSON：`{ hook, url, config, html_path, result, output_path }`
stdout 输出 JSON 编码的返回值。

```javascript
const fs = require('fs');
const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const { hook, url, config, html_path, result } = input;

function before(url, config) {
    return null; // 或返回局部配置 dict
}

function replace(url, config) {
    return { title: null, description: null, image: null, url };
}

function after(result, url, config) {
    return result; // 返回修改后的结果 dict
}

(async () => {
    let output = null;
    switch (hook) {
        case 'before':  output = before(url, config); break;
        case 'replace': output = await replace(url, config); break;
        case 'after':   output = after(result, url, config); break;
    }
    console.log(JSON.stringify(output));
})();
```

## snapshot 钩子

### Python snapshot 钩子（external，始终在 SingleFile 外运行）

```python
def before(url: str, config: dict) -> str | None:
    """返回要交给 SingleFile 的 HTML 字符串；返回 None 时让 SingleFile 正常抓取。
    在这里可以用项目浏览器预渲染页面。
    """
    from site_adapters.services.engine.browser_provider import launch_browser
    from site_adapters.services.auth.cookies import cookie_string_to_playwright_list
    from urllib.parse import urlparse

    browser = launch_browser(headless=True)
    playwright = getattr(browser, "__playwright__", None)
    try:
        context = browser.new_context(user_agent=config.get("headers", {}).get("User-Agent", ""))
        cookie_str = config.get("user_cookie") or ""
        if cookie_str:
            cookies = cookie_string_to_playwright_list(cookie_str, urlparse(url).hostname or "")
            if cookies:
                context.add_cookies(cookies)
        page = context.new_page()
        page.goto(config.get("request_url") or url, wait_until="domcontentloaded")
        return page.content()
    finally:
        browser.close()
        if playwright:
            playwright.stop()

def replace(url: str, config: dict, output_path: str) -> None:
    """将完整 HTML 文件写入 output_path。不经过 SingleFile。"""
    pass

def after(output_path: str, config: dict) -> None:
    """原地修改已保存的快照 HTML 文件。"""
    pass
```

**主动拒绝并带上原因**：`before` / `replace` 返回带 `error` 字段的 dict 时，框架不写盘、不覆盖已有快照，抛 `SnapshotRejectedError(status, reason)`；管理页测试面板会显示 `(status) reason`。`status` 可省略（默认 `failed`），建议沿用健康状态词 `blocked` / `missing` / `dead` / `failed`。这与 `precheck_health` 的拒绝走同一条通道。

```python
def replace(url, config, output_path=None):
    if not can_fetch(url):
        return {"status": "missing", "error": "内容已删除或无权限查看"}
    ...
```

不带 `error` 的 dict 仍按 `before` 的配置覆盖语义处理；返回 `None` 且未写出文件则仍报 `Replace hook did not produce a non-empty snapshot`。

### JavaScript snapshot 钩子 — SingleFile 浏览器模式

在文件顶部声明 `const builtin_engine = "singlefile";`。

`before` 在 SingleFile 浏览器内运行，可以使用完整的 DOM API，但**不能使用 Node API**。

`after` 在 Linkedom DOM 中运行（不是真实浏览器）。需要持久化的属性请使用 `setAttribute()` / `getAttribute()`，不要只给属性赋值。

```javascript
const builtin_engine = "singlefile";

async function before(url, config) {
    // 在 SingleFile 捕获前于页面内运行。
    // 修改实时 DOM；修改结果会包含在快照中。
    document.querySelectorAll('.RichContent.is-collapsed').forEach((el) => {
        el.classList.remove('is-collapsed');
    });
}

async function after(url, config) {
    // 在快照 HTML 写入后运行，运行环境为 Linkedom DOM。
    // DOM 修改会序列化回文件。
    const style = document.createElement('style');
    style.textContent = 'body { background: #111; color: #eee; }';
    document.head.appendChild(style);
}
```

### JavaScript snapshot 钩子 — external Node 模式

在文件顶部声明 `const builtin_engine = "";`。

可以使用完整的 Node.js API（`fs`、`path`、`child_process` 等）。

```javascript
const builtin_engine = "";
const fs = require('fs');
const input = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
const { hook, url, config, output_path } = input;

async function before(url, config) {
    // 返回要交给 SingleFile 的 HTML 字符串，或返回 null
    return null;
}

async function replace(url, config, output_path) {
    // 将完整 HTML 写入 output_path
    fs.writeFileSync(output_path, '<html>...</html>');
}

function after(output_path, config) {
    // 原地修改 output_path
}

(async () => {
    try {
        switch (hook) {
            case 'before':   console.log(JSON.stringify(await before(url, config))); break;
            case 'replace':  await replace(url, config, output_path); console.log('null'); break;
            case 'after':    after(output_path, config); console.log('null'); break;
        }
    } catch (e) {
        fs.writeSync(process.stderr.fd, 'Script error: ' + e.message + '\n');
        console.log('null');
    }
})();
```

## Python 钩子可用的项目 helper

以下导入可用于 `.py` 钩子脚本，因为它们在进程内运行：

```python
# 浏览器引擎（根据设置使用 CloakBrowser 或 Chromium）
from site_adapters.services.engine.browser_provider import launch_browser, get_browser_config

# 转换为 Playwright context 使用的 Cookie
from site_adapters.services.auth.cookies import cookie_string_to_playwright_list

# 存储凭据查询（用户优先，共享兜底）
from site_adapters.services.auth.credentials import get_best_cookie, get_best_header, get_best_token, get_best_basic_auth

# OAuth2 token 缓存/刷新
from site_adapters.services.auth.oauth2 import get_valid_token

# 解析另一个 URL/section 的合并配置
from site_adapters.services.config.resolver import get_metadata_config, get_snapshot_config, get_reader_config

# 在 replace 钩子中委托回内置引擎
from site_adapters.services.engine import create_snapshot, parse_metadata
```

## 脚本类型选择

| 场景 | 推荐方案 |
|------|----------|
| 基于 CSS 选择器提取 | 声明式 `select_*`，不要脚本 |
| snapshot 简单 DOM 清理 | 声明式 `keep_elements` / `remove_elements` / `remove_classes` |
| 快照前需要点击/展开内容 | JS `before` 钩子（singlefile 模式） |
| 需要真实浏览器预渲染 JS 内容 | Python `before` 钩子，配合 `launch_browser()` |
| 需要在 SingleFile 保存后修改快照 HTML | JS `after` 钩子（singlefile 模式）或 Python `after` 钩子 |
| 完全自定义快照、不使用 SingleFile | JS `replace` 钩子（external 模式）或 Python `replace` 钩子 |
| 需要 Node API（puppeteer、fs 等） | JS 钩子（external 模式，`builtin_engine = ""`） |
| 请求前重写 URL | `request_url` 字段，不需要脚本 |
| 重写展示 URL | `rewrite_url` 字段，不需要脚本 |
| 使用用户凭据中的自定义 HTTP Header | `auth.headers` 配置，不需要脚本 |

## 超时行为

- JavaScript 脚本：硬超时，子进程会被杀死。
- Python 脚本：软超时，主线程停止等待，但 daemon 线程会继续运行。长时间运行的任务优先使用 JavaScript，或保持 Python 脚本足够短。
