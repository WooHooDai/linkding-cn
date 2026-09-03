"""
Single source of truth for all site adapter configuration fields.

Each field entry may contain:
  - type: human-readable JSON type
  - en / zh: localized description
  - example: preferred placeholder for generated reference files
  - required / common / reserved: rendering and validation metadata

Runtime modules and scripts/generate-adapters-reference.py import these
definitions so the reference files and validator do not drift.
"""

_REWRITE_RULE_EN = 'Regex rewrite. Accepts ["pattern", "replacement"] for one rule, or [["pattern", "replacement"], ...] for multiple rules applied in order.'
_REWRITE_RULE_ZH = '正则重写。单个规则使用 ["pattern", "replacement"]；多个规则使用 [["pattern", "replacement"], ...]，按顺序应用。'

# ── defaults section ─────────────────────────────────────────────────────

DEFAULT_FIELDS = {
    "timeout": {
        "type": "int",
        "en": "Request timeout in seconds.",
        "zh": "请求超时时间（秒）。",
        "example": 30,
    },
    "proxy": {
        "type": "str|null",
        "en": 'HTTP proxy URL, e.g. "http://127.0.0.1:8080".',
        "zh": "HTTP 代理地址。",
        "example": None,
    },
    "rewrite_url": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Changes the URL saved/displayed for the bookmark.",
        "zh": _REWRITE_RULE_ZH + "用于改写最终展示/保存的 URL。",
        "example": ["^https://m\\.example\\.com/(.*)", "https://www.example.com/\\1"],
    },
    "request_url": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Changes the URL used for the actual request.",
        "zh": _REWRITE_RULE_ZH + "用于改写真正发起请求的 URL。",
        "example": ["^https://www\\.example\\.com/article/", "https://api.example.com/item/"],
    },
    "http": {
        "type": "object<string, string>",
        "en": "Custom HTTP request headers. Every key-value pair is passed as a header.",
        "zh": "自定义 HTTP 请求头。所有键值对都会作为 header 透传。",
        "example": {},
    },
    "auth": {
        "type": "auth",
        "en": "Authentication config.",
        "zh": "认证配置。",
        "example": None,
    },
}

# ── metadata section ─────────────────────────────────────────────────────

METADATA_FIELDS = {
    "auth": {
        "type": "auth",
        "en": "Authentication config.",
        "zh": "认证配置。",
        "example": None,
    },
    "http": {
        "type": "object<string, string>",
        "en": "Custom HTTP request headers. Every key-value pair is passed as a header.",
        "zh": "自定义 HTTP 请求头。所有键值对都会作为 header 透传。",
        "example": {},
    },
    "request_url": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Changes the URL used for the actual request.",
        "zh": _REWRITE_RULE_ZH + "用于改写真正发起请求的 URL。",
        "example": ["^https://www\\.example\\.com/article/", "https://api.example.com/item/"],
    },
    "rewrite_url": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Changes the URL saved/displayed for the bookmark.",
        "zh": _REWRITE_RULE_ZH + "用于改写最终展示/保存的 URL。",
        "example": ["^https://m\\.example\\.com/(.*)", "https://www.example.com/\\1"],
    },
    "content_type": {
        "type": '"html"|"xml"|"json"',
        "en": 'Response format for declarative extraction. "html" uses CSS selectors and JSON-LD fallback; "xml" uses standard XPath; "json" uses standard JSONPath such as $.data.items[0].title.',
        "zh": '声明式提取的响应格式。"html" 使用 CSS 选择器和 JSON-LD 兜底；"xml" 使用标准 XPath；"json" 使用标准 JSONPath，例如 $.data.items[0].title。',
        "example": "html",
    },
    "xmlns": {
        "type": "object<string, string>",
        "en": 'Optional namespace prefixes for XML XPath expressions. Atom/RSS use automatic aliases atom/rss; prefixes declared anywhere in the document are auto-registered; unprefixed element names bind to the document default namespace; this overrides or adds custom prefixes.',
        "zh": '可选的 XML XPath namespace 前缀映射。Atom/RSS 自动提供 atom/rss 前缀；文档任意层级声明的前缀会自动注册；无前缀元素名自动绑定文档默认 namespace；此字段用于覆盖或补充自定义前缀。',
        "example": {"atom": "http://www.w3.org/2005/Atom"},
    },
    "select_title": {
        "type": "str|array<str>",
        "en": "Selectors for the title, tried in order. HTML uses standard CSS, including the ::json(path) pseudo-element extension to extract fields from JSON-LD script tags (e.g. 'script[type=\"application/ld+json\"]::json(description)') and the ::attr(name) pseudo-element extension to read an explicit HTML attribute (e.g. 'img::attr(data-src)'); XML uses standard XPath; JSON uses standard JSONPath.",
        "zh": "标题选择器，按顺序尝试，取第一个非空结果。HTML 使用标准 CSS，支持 ::json(path) 伪元素扩展以从 JSON-LD script 标签提取字段（如 'script[type=\"application/ld+json\"]::json(description)'），也支持 ::attr(name) 伪元素扩展读取指定 HTML 属性（如 'img::attr(data-src)'）；XML 使用标准 XPath；JSON 使用标准 JSONPath。",
    },
    "select_description": {
        "type": "str|array<str>",
        "en": "Selectors for the description, tried in order. HTML uses standard CSS, including the ::json(path) pseudo-element extension to extract fields from JSON-LD script tags (e.g. 'script[type=\"application/ld+json\"]::json(description)') and the ::attr(name) pseudo-element extension to read an explicit HTML attribute (e.g. 'img::attr(data-src)'); XML uses standard XPath; JSON uses standard JSONPath.",
        "zh": "描述选择器，按顺序尝试。HTML 使用标准 CSS，支持 ::json(path) 伪元素扩展以从 JSON-LD script 标签提取字段（如 'script[type=\"application/ld+json\"]::json(description)'），也支持 ::attr(name) 伪元素扩展读取指定 HTML 属性（如 'img::attr(data-src)'）；XML 使用标准 XPath；JSON 使用标准 JSONPath。",
    },
    "select_image": {
        "type": "str|array<str>",
        "en": "Selectors for the preview image, tried in order. HTML uses standard CSS, including the ::json(path) pseudo-element extension to extract fields from JSON-LD script tags (e.g. 'script[type=\"application/ld+json\"]::json(image.url)') and the ::attr(name) pseudo-element extension to read an explicit HTML attribute (e.g. 'img::attr(data-src)'); values that are blob:/empty/about:/# or base64 data URIs are skipped so later selectors are tried. XML uses standard XPath; JSON uses standard JSONPath.",
        "zh": "预览图选择器，按顺序尝试。HTML 使用标准 CSS，支持 ::json(path) 伪元素扩展以从 JSON-LD script 标签提取字段（如 'script[type=\"application/ld+json\"]::json(image.url)'），也支持 ::attr(name) 伪元素扩展读取指定 HTML 属性（如 'img::attr(data-src)'）；blob:/空值/about:/#/base64 data URI 会被跳过并继续尝试后续选择器。XML 使用标准 XPath；JSON 使用标准 JSONPath。",
    },
    "rewrite_title": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Applied to the extracted title.",
        "zh": _REWRITE_RULE_ZH + "应用于提取到的标题。",
        "example": ["\\s*- Example$", ""],
    },
    "rewrite_description": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Applied to the extracted description.",
        "zh": _REWRITE_RULE_ZH + "应用于提取到的描述。",
        "example": ["\\[read more\\]", ""],
    },
    "rewrite_image": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Applied to the extracted image URL.",
        "zh": _REWRITE_RULE_ZH + "应用于提取到的预览图 URL。",
        "example": ["^http://", "https://"],
    },
    "load_full_page": {
        "type": "bool",
        "en": "Whether to load the full page content. Defaults to true.",
        "zh": "是否加载完整页面内容。默认为 true。",
        "example": True,
    },
    "max_content_limit": {
        "type": "int",
        "en": "Maximum content size in bytes to load. Defaults to 5120000 (5 MB).",
        "zh": "最大加载字节数。默认为 5120000（5 MB）。",
        "example": 5120000,
    },
    "use_browser": {
        "type": "object|null",
        "en": "Use a browser engine instead of requests for page loading. When enabled, launches a fresh ephemeral headless browser context (Chromium or CloakBrowser) and injects the resolved cookies, which handles JavaScript-rendered content. It does not share the persistent chromium-profile used by snapshots/cookie refresh, so concurrent metadata requests cannot race on a profile lock. Supported options: enabled (bool), wait_until (domcontentloaded | load | networkidle), wait_elements (string or array of strings), timeout (positive int or null). On browser failure, falls back to requests with a warning log. Set to null to explicitly disable/remove browser config inherited from defaults.",
        "zh": "使用浏览器引擎而非 requests 加载页面。启用后启动全新的临时无头浏览器上下文（Chromium 或 CloakBrowser），并注入已解析的 Cookie，可处理 JavaScript 渲染的内容。该模式不共享快照/Cookie 刷新使用的持久化 chromium-profile，避免并发元数据请求争用 profile 锁。支持选项：enabled（bool）、wait_until（domcontentloaded | load | networkidle）、wait_elements（字符串或字符串数组）、timeout（正整数或 null）。浏览器加载失败时回退到 requests 并记录警告日志。设为 null 可显式禁用/移除从 defaults 继承的浏览器配置。",
        "example": {"wait_until": "domcontentloaded"},
    },
    "scripts": {
        "type": "array<{path, hook, timeout?}>",
        "en": 'Script hooks. hook: "before" | "replace" | "after". before runs inside the built-in engine; after modifies the saved snapshot HTML; replace bypasses the engine. Optional per-script timeout (seconds) overrides the section-level timeout for this script only; when omitted, the section-level timeout is used.',
        "zh": '自定义脚本钩子。hook: before | replace | after。before 在内置引擎中运行，after 修改保存后的快照 HTML；replace 完全接管内置引擎。可选的 per-script timeout（秒）仅为此脚本覆盖 section 级 timeout；省略时继承 section 级 timeout。',
        "example": [],
        "example_items": [
            {"path": "example_before.py", "hook": "before"},
            {"path": "example_replace.py", "hook": "replace"},
            {"path": "example_after.py", "hook": "after", "timeout": 120},
        ],
    },
    "timeout": {
        "type": "int",
        "en": "Request timeout in seconds. Also serves as the fallback timeout for script hooks that do not declare their own.",
        "zh": "请求超时时间（秒）。同时作为未声明自身 timeout 的脚本钩子的回退超时。",
        "example": 30,
    },
    "proxy": {
        "type": "str|null",
        "en": "HTTP proxy URL.",
        "zh": "HTTP 代理地址。",
        "example": None,
    },
}

# ── snapshot section ─────────────────────────────────────────────────────

SNAPSHOT_FIELDS = {
    "enabled": {
        "type": "bool",
        "en": "Whether snapshot creation is allowed for this domain/route. Defaults to true. When false, no snapshot asset is created. This is a subtractive control: it can only disable snapshots, never force-enable them against the global or user-level toggle.",
        "zh": "是否允许为该域名/路由创建快照。默认 true。设为 false 时不创建快照资产。这是纯减法控制：只能禁用快照，无法绕过全局或用户级开关强制开启。",
        "example": True,
    },
    "content_type": {
        "type": '"html"|"xml"|"json"',
        "en": 'Snapshot format. "html" uses SingleFile; "xml" and "json" save the raw response.',
        "zh": "快照格式。\"html\" 使用 SingleFile；\"xml\" 和 \"json\" 保存原始响应。",
        "example": "html",
    },
    "auth": {
        "type": "auth",
        "en": "Authentication config.",
        "zh": "认证配置。",
        "example": None,
    },
    "http": {
        "type": "object<string, string>",
        "en": "Custom HTTP request headers. Every key-value pair is passed as a header.",
        "zh": "自定义 HTTP 请求头。所有键值对都会作为 header 透传。",
        "example": {},
    },
    "request_url": {
        "type": "rewrite",
        "en": _REWRITE_RULE_EN + " Changes the URL used for the actual request.",
        "zh": _REWRITE_RULE_ZH + "用于改写真正发起请求的 URL。",
        "example": ["^https://www\\.example\\.com/article/", "https://api.example.com/item/"],
    },
    "keep_elements": {
        "type": "str|array<str>",
        "en": "CSS selectors of elements to KEEP in the snapshot. Everything else is removed.",
        "zh": "快照中保留的元素。其余全部移除。",
    },
    "remove_elements": {
        "type": "str|array<str>",
        "en": "CSS selectors of elements to REMOVE from the snapshot.",
        "zh": "快照中移除的元素。",
    },
    "process_lazy_images": {
        "type": "bool|array<str>",
        "en": 'Fix lazy-loaded images. true = built-in attr list; ["data-actualsrc"] = custom attrs (merged with built-in list). false disables.',
        "zh": "修复懒加载图片。true 使用内置属性列表；数组指定自定义属性名（与内置列表合并）。false 禁用。",
        "example": True,
    },
    "process_carousels": {
        "type": "str|array<str>",
        "en": "CSS selectors of carousel containers to replace with a horizontal media list. Carousel conversion is opt-in.",
        "zh": "需要转换为横向媒体列表的轮播容器 CSS 选择器。轮播转换默认关闭。",
        "example": ["faceplate-carousel"],
    },
    "remove_classes": {
        "type": "object",
        "en": 'Remove CSS classes from elements. Format: {"selector": ["class1", "class2"]}.',
        "zh": "移除指定元素的 CSS class。格式: {\"selector\": [\"class1\", \"class2\"]}。",
    },
    "set_styles": {
        "type": "object",
        "en": 'Set inline styles on elements. Format: {"selector": {"prop": "value"}}. Supports !important and CSS custom properties (--var). ":root" selector targets documentElement.',
        "zh": '设置指定元素的内联样式。格式：{"selector": {"prop": "value"}}。支持 !important 和 CSS 自定义属性（--var）。\":root\" 选择器指向 documentElement。',
    },
    "wait_elements": {
        "type": "array<str>",
        "en": 'CSS selectors to wait for before capture. Each entry is a selector; "|" separates OR alternatives. e.g. [".a | .b", ".c"] waits until (".a" OR ".b") AND ".c" are present. Non-matching selectors time out gracefully (capture proceeds with whatever is present).',
        "zh": '捕获前等待元素出现。每个条目是一个选择器，"|" 分隔 OR 关系。如 [".a | .b", ".c"] 等待 (".a" 或 ".b") 且 ".c" 同时存在。未匹配的选择器超时后降级执行。',
        "example": [".notion-scroller"],
    },
    "wait_elements_timeout": {
        "type": "int",
        "en": "Timeout (seconds) for wait_elements. When omitted, defaults to min(timeout, 30).",
        "zh": "wait_elements 的超时时间（秒）。省略时默认取 min(timeout, 30)。",
        "example": 10,
    },
    "singlefile_args": {
        "type": "object",
        "en": 'SingleFile CLI arguments, e.g. {"--browser-wait-delay": 2000}.',
        "zh": "SingleFile CLI 参数，如 {\"--browser-wait-delay\": 2000}。",
    },
    "toggles": {
        "type": "object",
        "en": 'User-toggleable element removal. {"toggle-id": {"selector": "...", "label": "...", "default": true}}.',
        "zh": "用户可切换的元素去除。{\"id\": {\"selector\": \"...\", \"label\": \"...\", \"default\": true}}。",
    },
    "scripts": {
        "type": "array<{path, hook, timeout?}>",
        "en": 'Script hooks. hook: "before" | "replace" | "after". before runs inside SingleFile; after modifies the saved snapshot file; replace bypasses SingleFile and declarative fields. Optional per-script timeout (seconds) overrides the section-level timeout for this script only; when omitted, the section-level timeout is used.',
        "zh": '自定义脚本钩子。hook: before | replace | after。before 在 SingleFile 内运行，after 修改保存后的快照文件；replace 接管 SingleFile，并绕过声明式快照字段。可选的 per-script timeout（秒）仅为此脚本覆盖 section 级 timeout；省略时继承 section 级 timeout。',
        "example": [],
        "example_items": [
            {"path": "example_before.py", "hook": "before"},
            {"path": "example_replace.py", "hook": "replace"},
            {"path": "example_after.py", "hook": "after", "timeout": 120},
        ],
    },
    "timeout": {
        "type": "int",
        "en": "Snapshot timeout in seconds. Also serves as the fallback timeout for script hooks that do not declare their own.",
        "zh": "快照超时时间（秒）。同时作为未声明自身 timeout 的脚本钩子的回退超时。",
        "example": 30,
    },
    "proxy": {
        "type": "str|null",
        "en": "HTTP proxy URL.",
        "zh": "HTTP 代理地址。",
        "example": None,
    },
}

# ── reader section ───────────────────────────────────────────────────────

READER_FIELDS = {
    "defuddle_args": {
        "type": "object<DefuddleOptions>",
        "en": "Defuddle extraction options. Common keys are shown below; advanced/reserved keys are commented out. Unknown keys are warned and ignored.",
        "zh": "Defuddle 正文提取参数。常用参数在下方展开；不常用或预留参数已注释。未知参数会被警告并忽略。",
        "example": {},
    },
    "timeout": {
        "type": "int",
        "en": "Request timeout in seconds.",
        "zh": "请求超时时间（秒）。",
        "example": 30,
    },
    "proxy": {
        "type": "str|null",
        "en": "HTTP proxy URL.",
        "zh": "HTTP 代理地址。",
        "example": None,
    },
    "http": {
        "type": "object<string, string>",
        "en": "Custom HTTP request headers. Every key-value pair is passed as a header.",
        "zh": "自定义 HTTP 请求头。所有键值对都会作为 header 透传。",
        "example": {},
    },
    "auth": {
        "type": "auth",
        "en": "Authentication config.",
        "zh": "认证配置。",
        "example": None,
    },
}

# ── All sections (used by validator & generator) ─────────────────────────

ALL_SECTIONS = {
    "defaults": DEFAULT_FIELDS,
    "metadata": METADATA_FIELDS,
    "snapshot": SNAPSHOT_FIELDS,
    "reader":   READER_FIELDS,
}

# ── routes (domain-level path routing) ───────────────────────────────────

ROUTES_FIELD = {
    "type": "object<path_pattern, domain_config>",
    "en": "Path-based route overrides. Keys are regex patterns matched against the URL path (document order, first match wins). Values are domain configs with the same structure (auth, defaults, metadata, snapshot, reader). Route config deep-merges over the domain-level config. Domains without 'routes' behave exactly as before.",
    "zh": "基于路径的路由覆盖。键为正则表达式，匹配 URL 路径（按文档顺序，首个匹配生效）。值为域名配置结构（auth、defaults、metadata、snapshot、reader），深合并到域名级配置之上。没有 routes 的域名行为不变。",
    "example": {},
}

# ── auth sub-fields ──────────────────────────────────────────────────────

AUTH_COOKIE_FIELDS = {
    "enabled": {
        "type": "bool",
        "en": "Whether cookie handling is enabled. Defaults to true.",
        "zh": "是否启用 Cookie 处理。默认为 true。",
        "example": True,
    },
    "type": {
        "type": '"auto" | "login"',
        "en": '"auto": system-managed cookies; "login": user provides cookie manually.',
        "zh": '"auto": 系统自动维护 Cookie；"login": 用户手动提供 Cookie。',
        "example": "auto",
    },
    "verify.http_head_probe.enabled": {
        "type": "bool",
        "en": "Enable L1 HTTP HEAD probe. Defaults to true.",
        "zh": "是否启用 L1 HTTP HEAD 探针。默认为 true。",
    },
    "verify.http_head_probe.url": {
        "type": "str",
        "en": "Override probe URL. Defaults to bookmark URL.",
        "zh": "覆盖探测 URL。默认使用书签 URL。",
    },
    "verify.http_head_probe.timeout": {
        "type": "int",
        "en": "Probe timeout in seconds. Defaults to 5.",
        "zh": "探针超时（秒）。默认为 5。",
        "example": 5,
    },
    "verify.http_head_probe.invalid_status": {
        "type": "array<int>",
        "en": "HTTP status codes that mean invalid. Defaults to [401, 403].",
        "zh": "判定失效的 HTTP 状态码。默认为 [401, 403]。",
        "example": [401, 403],
    },
    "verify.http_head_probe.invalid_location_patterns": {
        "type": "array<str>",
        "en": "Flat array of regex strings matched against redirect Location. Any match means invalid.",
        "zh": "字符串正则数组，用于匹配重定向 Location；任一匹配即判定失效。",
        "example": ["login", "signin"],
    },
    "verify.http_head_probe.set_cookie_cleared": {
        "type": "bool",
        "en": "Treat Set-Cookie clearing an existing cookie as invalidation. Defaults to true.",
        "zh": "服务端 Set-Cookie 清空已有 Cookie 时视为失效。默认为 true。",
    },
    "verify.content_check.enabled": {
        "type": "bool",
        "en": "Enable L2 page content check. Defaults to true.",
        "zh": "是否启用 L2 页面内容验证。默认为 true。",
    },
    "verify.content_check.url": {
        "type": "str",
        "en": "Override verification URL. Defaults to bookmark URL.",
        "zh": "覆盖验证 URL。默认使用书签 URL。",
    },
    "verify.content_check.check_selectors": {
        "type": 'array<"title" | "body">',
        "en": 'Which page parts to scan. Defaults to ["title", "body"].',
        "zh": '检查页面的哪些部分。默认为 ["title", "body"]。',
        "example": ["title", "body"],
    },
    "verify.content_check.valid_patterns": {
        "type": "array<str>",
        "en": "Flat array of regex strings. Any match means valid (short-circuit).",
        "zh": "字符串正则数组。任一匹配即判定有效（短路）。",
        "example": ["dashboard", "logged.?in"],
    },
    "verify.content_check.valid_selectors": {
        "type": "array<str>",
        "en": "CSS selectors. Any element existing means valid (short-circuit).",
        "zh": "CSS 选择器。任一元素存在即判定有效（短路）。",
    },
    "verify.content_check.invalid_patterns": {
        "type": "array<str>",
        "en": "Flat array of regex strings. Any match against title/body means invalid.",
        "zh": "字符串正则数组。title/body 中任一匹配即判定失效。",
        "example": ["log in", "captcha"],
    },
    "verify.content_check.invalid_selectors": {
        "type": "array<str>",
        "en": "CSS selectors. Any element existing means invalid.",
        "zh": "CSS 选择器。任一元素存在即判定失效。",
    },
    "refresh.url": {
        "type": "str",
        "en": "Refresh URL. Defaults to bookmark URL.",
        "zh": "刷新时访问的 URL。默认使用书签 URL。",
    },
    "refresh.wait_cookie": {
        "type": "str | array<str>",
        "en": "Cookie name(s) to wait for after page load.",
        "zh": "页面加载后等待出现的 Cookie 名称（字符串或数组）。",
    },
    "refresh.timeout": {
        "type": "int",
        "en": "Browser refresh timeout in seconds. Defaults to 30.",
        "zh": "浏览器刷新超时（秒）。默认为 30。",
        "example": 30,
    },
    "refresh.interval": {
        "type": "int",
        "en": "Minimum seconds between refresh attempts. Defaults to 14400 (4 hours).",
        "zh": "最小刷新间隔（秒）。默认为 14400（4 小时）。",
        "example": 14400,
    },
    "refresh.user_data_dir": {
        "type": '"default" | str',
        "en": 'Persistent browser profile directory used only when explicitly declared. Chromium and CloakBrowser both use a persistent context when this is set. "default" uses the project Chromium profile (shared with SingleFile snapshots), which helps bypass bot detection on sites like Reddit. Absolute or BASE_DIR-relative paths are also accepted. Because the same profile cannot be locked by two browser processes at once, explicit declarations make concurrent snapshot/cookie-refresh conflicts easy to notice. Omit to use a fresh ephemeral context.',
        "zh": '仅当显式声明时才使用的持久化浏览器用户目录，Chromium 与 CloakBrowser 都会以该 profile 启动持久化上下文。"default" 使用项目内置 Chromium profile（与 SingleFile 快照共享），可绕过 Reddit 等站点的 bot 检测。也支持绝对路径或相对于 BASE_DIR 的路径。同一 profile 无法被两个浏览器进程同时锁定，因此显式声明会在并发快照/Cookie 刷新冲突时更容易被察觉。省略则使用临时无状态上下文。',
        "example": "default",
    },
}

AUTH_OAUTH2_FIELDS = {
    "enabled": {
        "type": "bool",
        "en": "Whether OAuth2 token handling is enabled. Defaults to true.",
        "zh": "是否启用 OAuth2 Token 处理。默认为 true。",
        "example": True,
    },
    "endpoint": {
        "type": "str (required)",
        "en": "OAuth2 token endpoint URL.",
        "zh": "OAuth2 Token 端点 URL。",
        "required": True,
        "example": "",
    },
    "client_id": {
        "type": "str",
        "en": "OAuth2 client_id.",
        "zh": "OAuth2 client_id。",
    },
    "client_secret": {
        "type": "str",
        "en": "OAuth2 client_secret.",
        "zh": "OAuth2 client_secret。",
    },
    "grant_type": {
        "type": "str",
        "en": 'Grant type. Defaults to "refresh_token".',
        "zh": '授权类型。默认为 "refresh_token"。',
        "example": "refresh_token",
    },
    "format": {
        "type": '"form" | "json"',
        "en": 'Request body format. Defaults to "form".',
        "zh": '请求体格式。默认为 "form"。',
        "example": "form",
    },
    "access_token_path": {
        "type": "str",
        "en": 'Standard JSONPath to access_token in response. Defaults to "$.access_token".',
        "zh": '响应中 access_token 的标准 JSONPath。默认为 "$.access_token"。',
        "example": "$.access_token",
    },
    "refresh_token_path": {
        "type": "str",
        "en": 'Standard JSONPath to refresh_token in response. Defaults to "$.refresh_token".',
        "zh": '响应中 refresh_token 的标准 JSONPath。默认为 "$.refresh_token"。',
        "example": "$.refresh_token",
    },
    "expires_in_path": {
        "type": "str",
        "en": 'Standard JSONPath to expires_in in response. Defaults to "$.expires_in".',
        "zh": '响应中 expires_in 的标准 JSONPath。默认为 "$.expires_in"。',
        "example": "$.expires_in",
    },
    "header": {
        "type": "str",
        "en": 'HTTP header name to inject token into. Defaults to "Authorization".',
        "zh": '注入 Token 的 HTTP header 名称。默认为 "Authorization"。',
        "example": "Authorization",
    },
    "header_format": {
        "type": "str",
        "en": 'Header value template. {token} is replaced. Defaults to "Bearer {token}".',
        "zh": 'Header 值模板，{token} 被替换。默认为 "Bearer {token}"。',
        "example": "Bearer {token}",
    },
    "extra_params": {
        "type": "object",
        "en": "Extra key-value pairs included in the token request body.",
        "zh": "Token 请求体中的额外键值对。",
        "example": {},
    },
}

AUTH_HEADERS_OBJECT_INFO = {
    "type": "object",
    "en": 'Custom HTTP request headers. Two forms: flat {"X-API-Key": ""} where keys are header names and values are string defaults; or structured {"enabled": true, "help": "...", "values": {"X-API-Key": ""}} when you need special fields. Reserved keys in flat form: enabled, help, values. To declare a header literally named \'enabled\' or \'help\', use the structured form with a \'values\' sub-key.',
    "zh": '自定义 HTTP 请求头。支持两种形式：扁平形式（{"X-API-Key": ""}），键为 header 名称，值为字符串默认值；或结构化形式（{"enabled": true, "help": "...", "values": {"X-API-Key": ""}}），当需要特殊字段时使用。扁平形式中保留键：enabled、help、values。如需声明名为 \'enabled\' 或 \'help\' 的 header，请使用结构化形式并在 values 中声明。',
    "example_key": "X-API-Key",
    "example_value": "",
}

AUTH_HEADERS_FIELDS = {
    "enabled": {
        "type": "bool",
        "en": "Enable or disable header auth for this block. Defaults to true.",
        "zh": "启用或禁用此块的 header 认证。默认为 true。",
        "example": True,
    },
    "help": {
        "type": "str",
        "en": "Help text shown in the credentials UI.",
        "zh": "凭据管理界面显示的帮助文本。",
        "example": "Enter your API key",
    },
    "values": {
        "type": "object<string, string>",
        "en": "Header name to default value mapping. Use this when you need to declare headers named 'enabled' or 'help'.",
        "zh": "Header 名称到默认值的映射。当需要声明名为 'enabled' 或 'help' 的 header 时使用。",
        "example_key": "X-API-Key",
        "example_value": "",
    },
}

AUTH_BASIC_FIELDS = {
    "username": {
        "type": "str",
        "en": "HTTP Basic Auth username.",
        "zh": "HTTP Basic Auth 用户名。",
        "example": "",
    },
    "password": {
        "type": "str",
        "en": "HTTP Basic Auth password.",
        "zh": "HTTP Basic Auth 密码。",
        "example": "",
    },
}

# ── adapter metadata fields ──────────────────────────────────────────────

ADAPTER_META_FIELDS = {
    "id": {
        "type": "str",
        "en": 'Publisher/author namespace, e.g. "com.rsshub". Directory name: {id}.{name}.',
        "zh": '发布者/作者命名空间，如 "com.rsshub"。目录名: {id}.{name}。',
        "required": True,
        "example": "local",
    },
    "name": {
        "type": "str",
        "en": "Adapter display name, also part of directory name.",
        "zh": "适配器显示名称，也是目录名的一部分。",
        "required": True,
        "example": "my-adapter",
    },
    "version": {
        "type": "int",
        "en": "Monotonically increasing version number. Increment whenever this config or its associated scripts change.",
        "zh": "单调递增版本号。配置文件或关联脚本变化时都需要递增。",
        "required": True,
        "example": 1,
    },
    "description": {
        "type": "str",
        "en": "Human-readable description.",
        "zh": "可读的描述信息。",
        "optional": True,
        "example": "",
    },
    "updateUrl": {
        "type": "str",
        "en": "Full download URL (for remote subscriptions).",
        "zh": "完整下载地址（远程订阅源使用）。",
        "optional": True,
        "example": "",
    },
    "checkUpdateUrl": {
        "type": "str",
        "en": "Lightweight version-check URL (returns {id, version}).",
        "zh": "轻量版本检查地址（返回 {id, version}）。",
        "optional": True,
        "example": "",
    },
}

# ── Open/third-party field name sets ──────────────────────────────────────

# SINGLEFILE_ARG_NAMES is manually maintained. Update it when the pinned
# SingleFile dependency adds, removes, or renames CLI arguments.
SINGLEFILE_ARG_NAMES = frozenset({
    "--accept-header-document", "--accept-header-font", "--accept-header-image",
    "--accept-header-script", "--accept-header-stylesheet", "--accept-language",
    "--block-alternative-images", "--block-audios", "--block-fonts", "--block-images",
    "--block-mixed-content", "--block-scripts", "--block-stylesheets",
    "--block-videos", "--blocked-URL-pattern", "--browser-arg", "--browser-args",
    "--browser-capture-max-time", "--browser-cookie", "--browser-cookies-file",
    "--browser-debug", "--browser-device-height", "--browser-device-scale-factor",
    "--browser-device-width", "--browser-executable-path", "--browser-headless",
    "--browser-height", "--browser-ignore-insecure-certs", "--browser-load-max-time",
    "--browser-mobile-emulation", "--browser-remote-debugging-URL", "--browser-script",
   "--browser-server", "--browser-start-minimized", "--browser-stylesheet",
    "--browser-single-process",
   "--browser-wait-delay", "--browser-wait-end-delay", "--browser-wait-until",
    "--browser-wait-until-delay", "--browser-wait-until-fallback", "--browser-width",
    "--compress-CSS", "--compress-HTML", "--compress-content",
    "--console-messages-file", "--crawl-external-links-max-depth",
    "--crawl-inner-links-only", "--crawl-links", "--crawl-load-session",
    "--crawl-max-depth", "--crawl-no-parent", "--crawl-remove-URL-fragment",
    "--crawl-replace-URLs", "--crawl-rewrite-rule", "--crawl-save-session",
    "--crawl-sync-session", "--create-root-directory", "--debug-messages-file",
    "--dump-content", "--embed-pdf", "--embed-pdf-options", "--embed-screenshot",
    "--embed-screenshot-options", "--embedded-image", "--embedded-pdf",
    "--emulate-media-feature", "--errors-file", "--errors-traces-disabled",
    "--extract-data-from-page", "--filename-conflict-action", "--filename-max-length",
    "--filename-max-length-unit", "--filename-replaced-character",
    "--filename-replacement-character", "--filename-template",
    "--group-duplicate-images", "--group-duplicate-stylesheets", "--help",
    "--http-header", "--http-proxy-password", "--http-proxy-server",
    "--http-proxy-username", "--include-BOM", "--include-infobar",
    "--infobar-position-absolute", "--infobar-position-bottom",
    "--infobar-position-left", "--infobar-position-right", "--infobar-position-top",
    "--infobar-template", "--insert-meta-CSP", "--insert-single-file-comment",
    "--insert-text-body", "--load-deferred-images",
    "--load-deferred-images-before-frames",
    "--load-deferred-images-dispatch-scroll-event",
    "--load-deferred-images-keep-zoom-level", "--load-deferred-images-max-idle-time",
    "--max-parallel-workers", "--max-resource-size", "--max-resource-size-enabled",
    "--max-size-duplicate-images", "--move-styles-in-head", "--open-infobar",
    "--output-directory", "--output-json", "--password", "--platform",
    "--prevent-appended-data", "--remove-alternative-fonts",
    "--remove-alternative-images", "--remove-alternative-medias", "--remove-frames",
    "--remove-hidden-elements", "--remove-no-script-tags", "--remove-saved-date",
    "--remove-unused-fonts", "--remove-unused-styles", "--removed-elements-selector",
    "--replace-emojis-in-filename", "--resolve-links", "--save-original-URLs",
    "--save-raw-page", "--self-extracting-archive", "--settings-file",
    "--settings-file-profile", "--urls-file", "--user-agent", "--user-script-enabled",
    "--version",
})

# Defuddle options are manually maintained from the pinned upstream type file:
# node_modules/defuddle/dist/types.d.ts
# Review this mapping when the defuddle dependency is upgraded. The
# common/advanced/reserved classification and localized descriptions are
# project-owned and cannot be derived automatically.
DEFUDDLE_ARG_FIELDS = {
    "contentSelector": {
        "type": "str | array<str>",
        "en": "Main content selector. HTML/XML snapshots support CSS and XPath; JSON snapshots support CSS and JSONPath. An array is tried in order and the first matching selector wins. Bypasses automatic content detection.",
        "zh": "正文内容选择器。HTML/XML 快照支持 CSS 和 XPath；JSON 快照支持 CSS 和 JSONPath。数组会按顺序尝试，使用第一个匹配项，并绕过自动正文识别。",
        "common": True,
        "example": [".article"],
    },
    "markdown": {
        "type": "bool",
        "en": "Convert extracted content to Markdown. Defaults to false.",
        "zh": "将提取内容转换为 Markdown。默认为 false。",
        "common": False,
        "example": True,
    },
    "separateMarkdown": {
        "type": "bool",
        "en": "Include Markdown in the response alongside HTML. Defaults to false.",
        "zh": "在响应中同时包含 HTML 和 Markdown。默认为 false。",
        "common": False,
        "example": False,
    },
    "includeReplies": {
        "type": 'bool | "extractors"',
        "en": 'Include replies. "extractors" includes replies from site-specific extractors; true includes all replies; false excludes all replies.',
        "zh": "是否包含回复。\"extractors\" 包含站点提取器的回复；true 包含全部回复；false 排除回复。",
        "common": False,
        "example": "extractors",
    },
    "language": {
        "type": "str",
        "en": "Preferred language, as a BCP 47 tag such as en, fr, or ja.",
        "zh": "首选语言，BCP 47 标签，如 en、fr、ja。",
        "common": False,
        "example": "en",
    },
    "profile": {
        "type": "bool",
        "en": "Enable per-step profiling. Timings are returned in result.profile.",
        "zh": "启用分步性能分析，耗时记录在 result.profile。",
        "common": False,
        "example": False,
    },
    "debug": {
        "type": "bool",
        "en": "Enable debug logging. Defaults to false.",
        "zh": "启用调试日志。默认为 false。",
        "common": False,
        "example": False,
    },
    "removeExactSelectors": {
        "type": "bool",
        "en": "Remove elements matching built-in exact selectors such as ads and social buttons. Defaults to true.",
        "zh": "移除与内置精确选择器匹配的元素，如广告和社交按钮。默认为 true。",
        "common": False,
        "example": True,
    },
    "removePartialSelectors": {
        "type": "bool",
        "en": "Remove elements matching built-in partial selectors such as ads and social buttons. Defaults to true.",
        "zh": "移除与内置模糊选择器匹配的元素，如广告和社交按钮。默认为 true。",
        "common": False,
        "example": True,
    },
    "removeImages": {
        "type": "bool",
        "en": "Remove images from extracted content. Defaults to false.",
        "zh": "从提取内容中移除图片。默认为 false。",
        "common": False,
        "example": False,
    },
    "useAsync": {
        "type": "bool",
        "en": "Allow async extractors to fetch content from third-party APIs when local HTML has no content. Defaults to true.",
        "zh": "本地 HTML 无内容时，允许异步提取器从第三方 API 获取内容。默认为 true。",
        "common": False,
        "example": True,
    },
    "removeHiddenElements": {
        "type": "bool",
        "en": "Remove hidden elements. Defaults to true.",
        "zh": "移除隐藏元素。默认为 true。",
        "common": False,
        "example": True,
    },
    "removeLowScoring": {
        "type": "bool",
        "en": "Remove low-scoring content blocks. Defaults to true.",
        "zh": "移除低评分内容块。默认为 true。",
        "common": False,
        "example": True,
    },
    "removeSmallImages": {
        "type": "bool",
        "en": "Remove small images. Defaults to true.",
        "zh": "移除小尺寸图片。默认为 true。",
        "common": False,
        "example": True,
    },
    "standardize": {
        "type": "bool",
        "en": "Standardize footnotes, headings, code blocks, and similar HTML. Defaults to true.",
        "zh": "标准化脚注、标题、代码块等 HTML。默认为 true。",
        "common": False,
        "example": True,
    },
    "removeContentPatterns": {
        "type": "bool",
        "en": "Remove content-based boilerplate such as reading-time hints and article cards. Defaults to true.",
        "zh": "移除阅读时长提示、文章卡片等内容型模板。默认为 true。",
        "common": False,
        "example": True,
    },
    "fetch": {
        "type": "function (reserved)",
        "en": "Reserved for future script-like support. Upstream Defuddle accepts a custom fetch function used for all HTTP requests during extraction, including async extractor requests such as YouTube transcripts or Reddit comments. A function cannot be expressed as a JSONC value.",
        "zh": "预留给未来的脚本式扩展。上游 Defuddle 允许传入自定义 fetch 函数，用于提取过程中的所有 HTTP 请求，包括 YouTube 字幕、Reddit 评论等异步请求；函数无法表达为 JSONC 值。",
        "common": False,
        "reserved": True,
        "example": None,
    },
}

REFERENCE_META = {
    "en": {
        "header": "Site Adapters — Complete Field Reference",
        "auto_gen": "Auto-generated from fields.py (single source of truth)",
        "regen": "python scripts/generate-adapters-reference.py",
        "guide": "docs/site-adapters.md",
        "required": "[required]",
        "optional": "[optional]",
        "reserved": "[reserved]",
    },
    "zh": {
        "header": "Site Adapters — 完整字段参考",
        "auto_gen": "由 fields.py 自动生成（单一信源）",
        "regen": "python scripts/generate-adapters-reference.py",
        "guide": "docs/site-adapters.md",
        "required": "[必填]",
        "optional": "[可选]",
        "reserved": "[预留]",
    },
}

PRIORITY_NOTES = {
    "en": [
        "Across subscriptions: adapters listed earlier in config.jsonc._adapters have higher priority.",
        "Within one subscription:",
        "defaults: ( _builtin < _builtin_overrides < ) adapter-level defaults < domain-level defaults < metadata/snapshot/reader",
        "auth: auth < defaults.auth < section.auth; http.Cookie < auth.cookie",
        "Merge strategy: objects deep-merge (higher priority inherits, overrides and merges lower priority); scalar and array values replace; `null` deletes the key.",
        "routes: when a domain has a 'routes' object, the URL path is matched against regex patterns in document order. The first matching route's config deep-merges over the domain-level config. Unmatched URLs fall back to the domain-level config. Routes are optional; domains without 'routes' behave exactly as before.",
    ],
    "zh": [
        "跨订阅源：config.jsonc._adapters 列表中越靠前的订阅源优先级越高。",
        "订阅源内部：",
        "defaults: ( _builtin < _builtin_overrides < ) 适配器级 defaults < 域名级 defaults < metadata/snapshot/reader",
        "auth: auth < defaults.auth < section.auth；http.Cookie < auth.cookie",
        "合并策略：对象类型深合并（高优先级继承覆盖并合并低优先级）；标量、数组整体替换；`null` 表示删除该键。",
        "routes：域名配置中的 'routes' 对象按文档顺序用正则匹配 URL 路径，首个匹配的 route 配置深合并到域名级配置之上。未匹配任何 route 时使用域名级配置。routes 为可选项，没有 routes 的域名行为不变。",
    ],
}

ALIAS_EXAMPLES = [
    {"key": "www.example_mobile.com", "target": "example.com"},
]

DOMAIN_EXAMPLE_KEY = "example.com"

# ── Section titles (used by generate script of reference files: scripts/generate-adapters-reference.py) ───────────────────────

SECTION_TITLES = {
    "priority_title": {
        "en": "Priority & merge rules",
        "zh": "优先级与合并规则",
    },
    "sec_meta": {
        "en": "_meta — adapter metadata (top-level)",
        "zh": "_meta — 适配器元信息（顶层）",
    },
    "meta_required": {
        "en": "Required fields",
        "zh": "必填字段",
    },
    "meta_optional": {
        "en": "Optional fields",
        "zh": "可选字段",
    },
    "sec_defaults": {
        "en": "defaults — adapter-wide defaults, merged into every domain config before domain-specific overrides",
        "zh": "defaults — 适配器级默认值，合并到每个域名配置（域名自身字段覆盖此默认值）",
    },
    "sec_domains": {
        "en": "domains — per-domain configuration",
        "zh": "domains — 按域名组织的配置",
    },
    "alias_intro": {
        "en": "Alias — share config with another domain",
        "zh": "别名 — 与其他域名共享配置",
    },
    "full_intro": {
        "en": "Full domain config — with all available sections",
        "zh": "完整域名配置 — 包含所有可用 section",
    },
    "sec_auth": {
        "en": "auth — authentication config",
        "zh": "auth — 认证配置",
    },
    "cookie_title": {
        "en": "Cookie authentication",
        "zh": "Cookie 认证",
    },
    "l1_title": {
        "en": "L1: HTTP HEAD probe for fast invalidation detection",
        "zh": "L1: HTTP HEAD 探针，快速检测失效",
    },
    "l2_title": {
        "en": "L2: Page content verification (runs when L1 passes or is disabled)",
        "zh": "L2: 页面内容验证（L1 通过或被禁用时执行）",
    },
    "refresh_title": {
        "en": "Browser-based cookie refresh",
        "zh": "浏览器自动刷新 Cookie",
    },
    "oauth2_title": {
        "en": "OAuth2 token auto-refresh",
        "zh": "OAuth2 Token 自动刷新",
    },
    "headers_title": {
        "en": "Custom HTTP headers",
        "zh": "自定义 HTTP 请求头",
    },
    "basic_title": {
        "en": "HTTP Basic Auth",
        "zh": "HTTP Basic Auth 认证",
    },
    "sec_default": {
        "en": "defaults — domain-level baseline, inherited by metadata / snapshot / reader",
        "zh": "defaults — 域名级基线配置，被 metadata / snapshot / reader 继承",
    },
    "sec_metadata": {
        "en": "metadata — title, description, preview image extraction",
        "zh": "metadata — 元数据提取（标题、描述、预览图）",
    },
    "sec_snapshot": {
        "en": "snapshot — HTML snapshot via SingleFile, or raw XML/JSON capture",
        "zh": "snapshot — HTML 快照生成（SingleFile + DOM 清理），或原始 XML/JSON 捕获",
    },
    "snapshot_mutex": {
        "en": "A snapshot script with hook \"replace\" bypasses SingleFile and the declarative fields keep_elements / remove_elements / remove_classes / set_styles / singlefile_args. HTML cleanup fields only apply to html snapshots. before/after hooks can coexist with those fields.",
        "zh": "hook 为 \"replace\" 的快照脚本会绕过 SingleFile 和声明式字段 keep_elements / remove_elements / remove_classes / set_styles / singlefile_args。HTML 清理字段仅对 html 快照生效。before/after hook 可与这些字段并存。",
    },
    "sec_reader": {
        "en": "reader — article extraction via defuddle",
        "zh": "reader — 文章提取（defuddle 引擎）",
    },
    "sec_routes": {
        "en": "routes — path-based config overrides (optional, first-match-wins)",
        "zh": "routes — 基于路径的配置覆盖（可选，首个匹配生效）",
    },
    "defuddle_title": {
        "en": "Defuddle options; only contentSelector is shown, all other options are commented out",
        "zh": "Defuddle 参数；仅展示 contentSelector，其余参数均已注释",
    },
    "sec_builtin": {
        "en": "_builtin — defaults adapter only. System-wide baseline config, merged as the lowest-priority layer for every domain. Do not edit; use _builtin_overrides.",
        "zh": "_builtin — 仅 defaults 适配器。系统级基线配置，作为所有域名的最低优先级层。禁止修改；需要调整时使用 _builtin_overrides。",
    },
    "sec_builtin_overrides": {
        "en": "_builtin_overrides — defaults adapter only. Deep-merged over _builtin; use this to override system defaults.",
        "zh": "_builtin_overrides — 仅 defaults 适配器。深合并到 _builtin 之上，用于覆盖系统默认值。",
    },
    "builtin_note": {
        "en": "metadata / snapshot / reader sections have the same structure as the domain config below",
        "zh": "metadata / snapshot / reader section 与下方域名配置结构相同",
    },
    "builtin_overrides_note": {
        "en": "Same structure as _builtin. Leave empty unless you need to override a system default.",
        "zh": "结构与 _builtin 相同。除非需要覆盖系统默认值，否则保持为空。",
    },
}
