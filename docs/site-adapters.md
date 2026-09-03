# Site Adapters 指南

linkding-cn 的 Site Adapters框架（站点适配器），可通过声明式配置，定制指定域名的元数据、快照、阅读页面的提取规则。支持使用 Python / JavaScript 脚本。

这份指南的目标读者：

- **第一次接触的用户**：从「5 分钟上手」开始，10 分钟内能创建第一个适配器。
- **想要深入的用户**：按「配置编写」「认证」「脚本钩子」逐步学习完整能力。
- **AI Agent**：先阅读「给 AI Agent 的使用入口」，再按「推荐工作流」操作，可以少走弯路、避免写出与当前引擎不一致的规则。

## 目录

1. [快速开始](#快速开始)
2. [核心概念与文件结构](#核心概念与文件结构)
3. [配置编写](#配置编写)
4. [认证体系](#认证体系)
5. [自定义脚本钩子](#自定义脚本钩子)
6. [测试与调试](#测试与调试)
7. [发布订阅源](#发布订阅源)
8. [给 AI Agent 的使用入口](#给-ai-agent-的使用入口)
9. [常见问题](#常见问题)
10. [相关资料与文档链接](#相关资料与文档链接)

---

## 快速开始

一个可用的适配器只需要一个目录和一个文件：

```text
data/site_adapters/adapters/
└── local.my-adapter/             # 适配器目录，目录名规则见下文
    └── adapters.jsonc            # 唯一的规则文件
```

在 `data/site_adapters/adapters/local.my-adapter/adapters.jsonc` 写入：

```jsonc
{
  "_meta": {
    "id": "local",
    "name": "my-adapter",
    "version": 1
  },
  "domains": {
    "example.com": {
      "metadata": {
        "select_title": ["h1", "meta[property='og:title']"],
        "select_description": ["meta[name='description']"],
        "select_image": ["meta[property='og:image']"]
      },
      "snapshot": {
        "remove_elements": ["header", "footer", "nav", ".ad"]
      },
      "reader": {
        "defuddle_args": {
          "contentSelector": [".article-content"]
        }
      }
    },
    "www.example.com": { "type": "alias", "target": "example.com" }
  }
}
```

把它注册给 linkding-cn：

1. 打开管理后台的 Site Adapters 页面（路径：设置 → 管理后台 → DOMAIN → Site Adapters，即 `/admin/site-adapters`）。
2. 在“新增订阅源”弹窗的“来源”中填入 `./local.my-adapter`。
3. 保存后即可在页面顶部的“URL Test”工具中测试该 URL。

使用命令行也可以完成同样的验证：

```bash
# 本地开发（linkding 源码树）
python manage.py site_adapter validate
python manage.py site_adapter show-config "https://example.com/article/1"
python manage.py site_adapter pipeline "https://example.com/article/1" --skip-snapshot

# Docker 部署：先找到容器，再在容器内执行
docker exec <container> python manage.py site_adapter show-config "https://example.com/article/1"
```

> 说明：`./local.my-adapter` 相对于 `adapters/` 目录解析；`source` 也支持本地绝对路径或 HTTPS URL（远程订阅源）。

接下来，按需要阅读下面的章节。

---

## 核心概念与文件结构

### 数据目录

所有数据位于 `data/site_adapters/`（由 `LD_SITE_ADAPTERS_DIR` 指定）。目录结构：

```text
data/site_adapters/
├── adapters/
│   ├── config.jsonc                  # 适配器注册表（优先级、订阅源列表）
│   ├── defaults/                     # 系统内置适配器（自动管理，无需手动创建）
│   │   ├── adapters.jsonc            # _meta + _builtin + _builtin_overrides + defaults + domains
│   │   └── scripts/
│   ├── {id}.{name}/                  # 每个订阅源一个目录
│   │   ├── adapters.jsonc
│   │   └── scripts/                  # 可选的自定义脚本
│   └── ...
├── credentials/                      # 加密保存的用户/共享凭据
├── preferences/                      # 用户快照偏好（toggles）
└── logs/                             # 执行日志 execution-YYYY-MM-DD.jsonl
```

### 两层配置文件

| 文件 | 位置 | 作用 |
|------|------|------|
| `config.jsonc` | `adapters/config.jsonc` | 声明有哪些适配器、加载顺序（优先级）、订阅源地址 |
| `adapters.jsonc` | 每个适配器目录内 | 该适配器的域名规则集 |

### `config.jsonc` 与订阅源注册

`config.jsonc` 顶层是一个 `_adapters` 数组：

```jsonc
{
  "_disabled_domains": [],
  "_adapters": [
    {
      "name": "official-standard",
      "update_interval": 86400,
      "id": "woohoodai",
      "source": "https://example.com/standard/adapters.jsonc",
      "enabled": true
    },
    {
      "name": "my-adapter",
      "id": "local",
      "source": "./local.my-adapter",
      "enabled": true
    }
  ]
}
```

条目字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 发布者命名空间，用于目录名 |
| `name` | string | 是 | 适配器名称（UI 显示 + 目录名一部分） |
| `source` | string | 是 | `https://` URL（远程订阅）、`./` 相对路径或本地绝对路径 |
| `update_interval` | int | 否 | 远程订阅源更新间隔（秒），默认 86400 |
| `enabled` | bool | 否 | 是否启用，默认 `true` |
| `exclude` | list | 否 | `fnmatch` 模式数组，匹配到的域名不从此适配器加载 |

规则：

- `_adapters` 数组中**靠前的适配器优先级更高**，同名配置覆盖靠后的。
- 同一个 `id + name` 只保留最先出现的一条。
- `defaults` 系统适配器会在缺失时自动重建，不需要手工维护。
- 编辑后无需重启：配置加载器基于文件 mtime，约 5 秒内生效。

### `adapters.jsonc` 顶层结构

```jsonc
{
  "_meta": { "id": "...", "name": "...", "version": 1, "description": "..." },
  "defaults": {
    // 适配器级默认值，会被本适配器内所有域名继承，再被域名配置覆盖
  },
  "domains": {
    "example.com": {
      // 域名级配置，包含 defaults / metadata / snapshot / reader / auth / routes
    }
  }
}
```

只属于 `defaults` 适配器的两个顶层键：

- `_builtin`：系统级基线，所有域名的**最低优先级**层，禁止直接修改。
- `_builtin_overrides`：用于覆盖 `_builtin` 的官方入口。

### 域名匹配与别名

域名 key 支持精确匹配和通配符：

- `example.com` 只匹配该裸域名。
- `*.example.com` 匹配任意层级子域名。
- 精确匹配优先；通配符按层级数降序尝试。

多个域名共享配置时使用别名：

```jsonc
"domains": {
  "example.com": {
    "metadata": { "select_title": ["h1"] }
  },
  "www.example.com": { "type": "alias", "target": "example.com" },
  "m.example.com": { "type": "alias", "target": "example.com" }
}
```

别名链最大深度 10 层，循环引用会被检测并中断。

### 路径路由（routes）

同一域名、不同 URL 路径可以使用不同规则：

```jsonc
"domains": {
  "example.com": {
    "metadata": { "select_title": ["h1"] },
    "routes": {
      "^/api/": {
        "metadata": { "content_type": "json", "select_title": ["$.title"] }
      },
      "^/rss/": {
        "metadata": { "content_type": "xml" }
      }
    }
  }
}
```

`routes` 的 key 是对 URL path 的正则，按文档顺序首个匹配生效，route 配置会深合并到域名级配置之上。

### 配置合并规则（重要）

当 linkding 处理一个 URL 时，最终配置按以下顺序得到：

1. 按 `_adapters` 顺序加载所有适配器，**靠前的覆盖靠后的**。
2. URL 域名匹配（精确 > 通配符）。
3. 别名链解析。
4. 同一适配器内：域名配置 `deep_merge` 适配器级 `defaults`，域名覆盖默认值。
5. 全局基线：`defaults` 适配器的 `_builtin` 作为最低优先级合并，`_builtin_overrides` 覆盖 `_builtin`。
6. Section 内：`defaults` + `metadata` / `snapshot` / `reader` 深合并；`auth` 按 `auth` → `defaults.auth` → `section.auth` 合并。

合并细节：

| 规则 | 说明 |
|------|------|
| 跨适配器 | 靠前覆盖靠后 |
| 适配器内 | 域名覆盖适配器级 `defaults` |
| 全局基线 | `_builtin` 最低优先级，`_builtin_overrides` 覆盖它 |
| 对象 | 递归深合并 |
| 标量 | 后写入覆盖前写入 |
| 数组 | **整体替换**，不合并元素 |
| `null` | 删除该键 |
| `http` | 深合并（defaults.http + section.http） |
| `auth.cookie` | 深合并 |
| `auth.headers` | 浅合并，后覆盖前 |
| `auth.oauth2` / `auth.basic_auth` | 整体替换 |

这意味着你只需要声明与基线不同的字段，不要重复声明 `_builtin` 已提供的默认值。

---

## 配置编写

### 一个域名的完整配置骨架

```jsonc
"example.com": {
  "auth": {
    "cookie": {
      "type": "auto",
      "verify": { "content_check": { "invalid_patterns": ["安全验证"] } }
    }
  },
  "defaults": {
    "timeout": 30,
    "request_url": ["^https://m\\.example\\.com/(.*)", "https://www.example.com/\\1"]
  },
  "metadata": {
    "select_title": ["h1.article-title", "meta[property='og:title']"],
    "select_description": ["meta[property='og:description']"],
    "select_image": ["meta[property='og:image']"],
    "rewrite_title": [["\\s* - Example$", ""]]
  },
  "snapshot": {
    "remove_elements": ["header", "footer", "nav", ".sidebar", ".ad"],
    "keep_elements": [".article-content"],
    "process_lazy_images": true,
    "singlefile_args": { "--browser-wait-delay": 1000 }
  },
  "reader": {
    "defuddle_args": { "contentSelector": [".article-body"] }
  }
}
```

### 通用字段

以下字段可以出现在 `defaults`、`metadata`、`snapshot`、`reader` 中：

| 字段 | 类型 | 说明 |
|------|------|------|
| `timeout` | int | 请求超时（秒），也是未声明自身 timeout 的脚本钩子的回退超时 |
| `proxy` | string/null | HTTP 代理地址 |
| `http` | object | 自定义请求头，键值对全部作为 header 透传 |
| `request_url` | rewrite | 正则重写真正发起请求的 URL |
| `rewrite_url` | rewrite | 正则重写最终保存/展示的 URL |
| `auth` | auth | 认证配置（可放在顶级、defaults、各 section 内） |

正则重写规则格式：单条 `["pattern", "replacement"]`，多条 `[["p1","r1"],["p2","r2"]]`，按顺序应用。

### metadata：元数据提取

| 字段 | 说明 |
|------|------|
| `select_title` / `select_description` / `select_image` | 选择器列表，按顺序尝试，取第一个非空结果 |
| `content_type` | `"html"` / `"xml"` / `"json"`，控制选择器语法 |
| `xmlns` | XML XPath 的自定义 namespace 前缀映射 |
| `rewrite_title` / `rewrite_description` / `rewrite_image` | 对提取结果的规则重写 |
| `load_full_page` | 是否加载完整页面内容，默认 `true` |
| `max_content_limit` | 最大加载字节数，默认 5 MB |
| `use_browser` | 用全新的临时无头浏览器加载页面并注入已解析 Cookie；支持 `enabled`、`wait_until`（`domcontentloaded` / `load` / `networkidle`）、`wait_elements`、`timeout`；不共享快照使用的持久化 profile；失败回退 requests |
| `scripts` | 脚本钩子，见「自定义脚本钩子」 |

#### 选择器语法

未显式声明 `content_type` 时，系统根据配置选择器语法推断，再回退到响应的 `Content-Type`。

- `html`：标准 CSS。图片字段按 `src` → `href` → `content` → `url` 顺序取值。额外支持 `::json(path)` 伪元素从 JSON-LD 提取，例如 `script[type="application/ld+json"]::json(description)`；也支持 `::attr(name)` 读取指定 HTML 属性，例如 `img::attr(data-src)`。`select_image` 数组按顺序尝试，图片字段的 `blob:`、空值、`about:`、`#` 或 base64 data URI 不会阻断后续选择器。
- `xml`：标准 XPath。需要属性时直接写 `@attr`。Atom 自动提供 `atom` 前缀，RSS 1.0 自动提供 `rss` 前缀；文档任意层级声明的其他前缀也会自动注册。无前缀元素名自动绑定文档默认 namespace，例如 Atom 可写 `//feed/entry/title`。
- `json`：标准 JSONPath，例如 `$.data.items[0].title`。

### snapshot：快照生成

| 字段 | 说明 |
|------|------|
| `enabled` | `false` 时不为该域名/路由创建快照（纯减法控制） |
| `content_type` | `"html"` 用 SingleFile；`"xml"` / `"json"` 保存原始响应 |
| `keep_elements` | 快照中保留的元素，其余全部移除 |
| `remove_elements` | 快照中移除的元素 |
| `process_lazy_images` | `true` 使用内置属性列表；数组与内置列表合并；`false` 禁用 |
| `process_carousels` | 需要转换为横向媒体列表的轮播容器 CSS 选择器（默认关闭） |
| `remove_classes` | `{"selector": ["class1", "class2"]}` |
| `set_styles` | `{"selector": {"prop": "value"}}`，支持 `!important` 与 CSS 自定义属性 |
| `wait_elements` | 捕获前等待元素出现；`"|"` 表示 OR，条目间是 AND；超时降级执行 |
| `wait_elements_timeout` | 等待超时（秒），默认 `min(timeout, 30)` |
| `singlefile_args` | SingleFile CLI 参数，如 `{"--browser-wait-delay": 2000}` |
| `toggles` | 用户可切换的保留/移除元素 |
| `scripts` | 脚本钩子 |

示例：

```jsonc
"snapshot": {
  "keep_elements": [".article-content"],
  "remove_elements": [".sidebar", ".ad"],
  "remove_classes": { ".RichContent": ["is-collapsed"] },
  "set_styles": { ".RichContent-inner": { "maxHeight": "none", "overflow": "visible" } },
  "wait_elements": [".article-content"],
  "wait_elements_timeout": 10,
  "process_lazy_images": ["data-src", "data-original"]
}
```

### reader：阅读模式

reader 使用 defuddle 引擎，**不支持自定义脚本**。常用参数：

| 参数 | 说明 |
|------|------|
| `contentSelector` | 正文选择器；HTML/XML 支持 CSS 和 XPath，JSON 支持 CSS 和 JSONPath；数组按顺序尝试，命中即绕过自动识别 |
| `markdown` / `separateMarkdown` | Markdown 输出开关 |
| `includeReplies` | `"extractors"` / `true` / `false` |
| `language` | 首选语言（BCP 47） |
| `removeExactSelectors` / `removePartialSelectors` | 移除广告/社交等元素 |
| `removeImages` | 从正文中移除图片 |

```jsonc
"reader": {
  "defuddle_args": {
    "contentSelector": [".article-body"],
    "removeExactSelectors": [".ad"]
  }
}
```

> 完整字段请以自动生成的 [reference/adapters-zh.jsonc](reference/adapters-zh.jsonc) 或 [reference/adapters.jsonc](reference/adapters.jsonc) 为准，它们是 `fields.py` 的单一信源，始终与引擎一致。

---

## 认证体系

认证配置可以放在域名顶级 `auth`、域名级 `defaults.auth` 或各 section 的 `auth` 中，按 `auth` → `defaults.auth` → `section.auth` 合并，后层覆盖前层。常见需求：

### Cookie

```jsonc
"auth": {
  "cookie": {
    "enabled": true,
    "type": "auto",
    "verify": {
      "http_head_probe": {
        "enabled": true,
        "invalid_status": [401, 403]
      },
      "content_check": {
        "valid_selectors": [".gn_name"],
        "invalid_patterns": ["log in", "captcha"]
      }
    },
    "refresh": {
      "url": "",
      "wait_cookie": "z_c0",
      "timeout": 30,
      "interval": 14400,
      "user_data_dir": "default"
    }
  }
}
```

- `type: "auto"`：系统自动维护 Cookie；`type: "login"`：用户手动提供 Cookie。
- `verify.http_head_probe`：L1 HTTP HEAD 探针；`verify.content_check`：L2 页面内容校验。
- `refresh.wait_cookie`：刷新时等待出现的 Cookie 名称（字符串或数组）。
- `refresh.user_data_dir` 仅显式声明时才携带持久化 profile；Chromium 与 CloakBrowser 都会复用该 profile。`"default"` 使用项目内置 Chromium profile，可帮助绕过 Reddit 等站点的 bot 检测；省略则使用临时无状态上下文。
- 当 `auth.cookie` 存在时，`http` 中的 `Cookie` 头会被忽略（两者互斥）。

### 自定义 Header

```jsonc
"auth": {
  "headers": {
    "x-jike-access-token": ""
  }
}
```

扁平形式的值是默认值；需要启用开关、帮助文本或声明名为 `enabled`/`help` 的 header 时，使用结构化形式：

```jsonc
"auth": {
  "headers": {
    "enabled": true,
    "help": "粘贴你的 API Key",
    "values": { "x-jike-access-token": "" }
  }
}
```

### OAuth2 Token

```jsonc
"auth": {
  "oauth2": {
    "endpoint": "https://example.com/oauth/token",
    "client_id": "",
    "client_secret": "",
    "grant_type": "refresh_token",
    "format": "form",
    "access_token_path": "$.access_token",
    "refresh_token_path": "$.refresh_token",
    "expires_in_path": "$.expires_in",
    "header": "Authorization",
    "header_format": "Bearer {token}"
  }
}
```

### HTTP Basic Auth

```jsonc
"auth": {
  "basic_auth": {
    "username": "",
    "password": ""
  }
}
```

### 凭据管理

- 凭据使用 Fernet 加密保存在本地；首次加密时自动生成密钥。
- 凭据分为**用户凭据**和**共享凭据**，用户凭据优先。
- `auto` 类型的 Cookie 在用户/共享凭据都不存在时会自动获取并保存为共享凭据。
- 凭据可在 Site Adapters 管理页面查看与维护。

---

## 自定义脚本钩子

优先使用声明式字段；只有声明式无法满足时才写脚本。脚本放在适配器目录的 `scripts/` 下，然后在 section 的 `scripts` 数组中引用：

```jsonc
"metadata": {
  "scripts": [
    { "path": "extract.py", "hook": "before" }
  ]
},
"snapshot": {
  "scripts": [
    { "path": "cleanup.js", "hook": "before" },
    { "path": "cleanup.js", "hook": "after", "timeout": 120 }
  ]
}
```

规则：

- `path` 为纯文件名时自动补全 `scripts/` 前缀；也支持相对路径。
- `hook` 支持 `before` / `replace` / `after`：
  - `before`：在主引擎执行前运行，可以修改配置、预取页面、返回 HTML。
  - `replace`：完全接管主引擎（每个 section 只允许一个）。
  - `after`：在主引擎（或 replace）之后运行，修改结果或快照文件。
- 每个脚本条目可配置独立的 `timeout`（秒），否则回退到 section 级 timeout，再回退到 30 秒。

执行生命周期：

```text
before hooks -> [replace hook 或 内置引擎] -> after hooks
```

### 脚本类型选择

| 场景 | 推荐方案 |
|------|----------|
| CSS 选择器提取 | 声明式 `select_*`，不需要脚本 |
| 快照简单清理 | 声明式 `keep_elements` / `remove_elements` / `remove_classes` |
| 捕获前点击/展开内容 | JS `before`（SingleFile 浏览器模式） |
| 需要真实浏览器预渲染（JS 渲染） | Python `before` + `launch_browser()` |
| 修改快照保存后的 HTML | JS `after`（SingleFile 模式）或 Python `after` |
| 完全自定义快照 | JS `replace`（external 模式）或 Python `replace` |
| 需要 Node API（fs、puppeteer 等） | JS 脚本，`builtin_engine = ""` |

### Python 脚本

- 在进程内线程执行，可使用 linkding 内部导入。
- `before` / `replace` 签名：`def before(url, config, html_content=None)` 或 `def replace(url, config, html_content=None)`。
- metadata `after` 签名：`def after(result, url, config)`，原地修改 `result`。
- snapshot `after` 签名：`def after(output_path, config)`。
- 常用内置 helper：`launch_browser()`、`cookie_string_to_playwright_list()`、`get_best_cookie()` / `get_best_header()` / `get_best_token()` / `get_best_basic_auth()`、`get_valid_token()`、`get_metadata_config()` / `get_snapshot_config()` / `get_reader_config()`。

参考模板：[metadata.py](../site_adapters/services/engine/scaffolds/metadata.py)、[snapshot.py](../site_adapters/services/engine/scaffolds/snapshot.py)。

### JavaScript 脚本

- metadata JS 在独立 Node 进程执行，通过 stdin 接收 JSON、stdout 返回 JSON。
- snapshot JS 分两种模式：
  - `const builtin_engine = "singlefile";`：`before` 在 SingleFile 浏览器内运行（有 DOM API、无 Node API）；`after` 在 Linkedom DOM 上运行，请用 `setAttribute()` / `getAttribute()` 持久化属性。
  - `const builtin_engine = "";`：external Node 模式，可使用完整 Node API。

参考模板：[metadata.js](../site_adapters/services/engine/scaffolds/metadata.js)、[snapshot.js](../site_adapters/services/engine/scaffolds/snapshot.js)、[snapshot_node.js](../site_adapters/services/engine/scaffolds/snapshot_node.js)。

### 超时行为

- JavaScript 是硬超时：子进程被终止。
- Python 是软超时：主线程不再等待，但 daemon 线程仍会继续运行。长时间任务优先用 JS，或保持 Python 脚本短小。

> 更完整的钩子 API（所有配置键、返回约定、脚手架模板）见本指南的「相关资料与文档链接」一章。

---

## 测试与调试

### 推荐工作流

给一个网站写适配器时，按这个顺序操作：

1. **先跑内置 pipeline**，看默认引擎已经能做到什么、哪里有问题：

```bash
python manage.py site_adapter pipeline "<url>" --skip-snapshot
```

2. **查看当前命中的配置**：

```bash
python manage.py site_adapter show-config "<url>"
```

3. 用浏览器 DevTools 分析页面结构，只修第 1 步发现的问题。
4. 写配置，跑 `validate` 校验。
5. 重新跑 pipeline 验证；需要验证快照时：

```bash
python manage.py site_adapter pipeline "<url>" --output /tmp/test-snapshot.html
```

6. 反复用 `show-config` 和 `pipeline` 迭代。

### 管理后台测试面板

`/admin/site-adapters` 页面顶部提供 URL Test，支持：

- **Show Config**：查看该 URL 命中的配置。
- **Metadata**：测试元数据提取。
- **HTML Snapshot**：测试快照。
- **Reader**：先取快照再提取阅读页面。
- **Credential**：查看该 URL 的认证凭据。
- **Full Pipeline**：端到端测试（除 Credential 外全部项目）。

### CLI 命令

```bash
# 校验整个适配器集
python manage.py site_adapter validate

# 校验某个域名配置（domain key，例如 example.com）
python manage.py site_adapter validate --file example.com

# 查看某 URL 的合并后配置
python manage.py site_adapter show-config "<url>"

# 测试元数据
python manage.py site_adapter metadata "<url>"

# 端到端流水线
python manage.py site_adapter pipeline "<url>"
python manage.py site_adapter pipeline "<url>" --skip-snapshot
python manage.py site_adapter pipeline "<url>" --output /tmp/snapshot.html

# 测试 Cookie
python manage.py site_adapter cookie "<url>" --section metadata

# 校验订阅源（本地目录/文件或 HTTPS URL）
python manage.py site_adapter validate-subscription ./local.my-adapter
python manage.py site_adapter validate-subscription https://example.com/adapters.jsonc

# 从 Tampermonkey UserScript 生成配置骨架
python manage.py site_adapter from-userscript ./myscript.user.js
```

Docker 环境在容器内执行同样的命令（`docker exec <container> python manage.py site_adapter ...`）。

### 执行日志

- 日志文件：`data/site_adapters/logs/execution-YYYY-MM-DD.jsonl`。
- 按天轮转，默认保留 30 天（`LD_SITE_ADAPTERS_LOG_RETENTION_DAYS` 可调）。
- `authorization`、`cookie`、`set-cookie` 等敏感字段自动脱敏。
- 脚本执行、Cookie 验证/刷新都会记录，是排错第一入口。

---

## 发布订阅源

适配器既可以只给自己用，也可以发布成远程订阅源。远程订阅源仍然是一个包含 `_meta`、`defaults`、`domains` 的 `adapters.jsonc`：

```jsonc
{
  "_meta": {
    "id": "com.example",
    "name": "my-site-pack",
    "version": 1,
    "description": "为某些网站优化的适配规则",
    "checkUpdateUrl": "https://example.com/adapters/check"
  },
  "defaults": {
    "timeout": 30
  },
  "domains": {
    "example.com": {
      "metadata": { "select_title": ["h1"] },
      "snapshot": { "remove_elements": [".ad"] }
    }
  }
}
```

发布要求：

- 远程 `source` 必须是 HTTPS。
- 脚本放在订阅源目录的 `scripts/` 下，并在 `scripts` 数组中用相对路径引用。发布端脚本 URL 使用相对路径；当前引擎不支持订阅源脚本使用外部 HTTPS URL。
- `_meta.id` 是发布者命名空间，`_meta.name` 是订阅源名称，目录名由两者组成。
- 每次修改配置或脚本都需要递增 `_meta.version`。

更新机制：

1. `update_interval`：默认 86400 秒，时间闸门。
2. `_meta.updateUrl`：可选的完整下载地址，用于发布方迁移订阅文件位置。
3. `_meta.checkUpdateUrl`：轻量版本接口，返回 `{"id": "...", "version": N}`；版本未变时跳过正文下载。
4. ETag / Last-Modified：条件请求，服务端返回 304 时跳过下载。
5. 内容哈希：无验证器时使用，内容未变则跳过脚本刷新。

可选模块化：`_includes` 支持拆分大型订阅源，include URL 必须是 HTTPS，最大递归深度 10 层，循环引用会被检测。

发布前检查：

```bash
# 1. 校验本地订阅源
python manage.py site_adapter validate-subscription ./my-adapter/

# 2. 测试典型 URL
python manage.py site_adapter show-config "https://target-site.com/page"
python manage.py site_adapter metadata "https://target-site.com/page"
python manage.py site_adapter pipeline "https://target-site.com/page" --skip-snapshot
```

---

## 给 AI Agent 的使用入口

AI Agent 编写或修改 site-adapters 规则时，按下面的顺序获取上下文，可以避免依赖过时知识。

1. **先看这篇指南**。它提供了完整的工作流、字段语义和合并规则。
2. **再看权威字段参考**：`docs/reference/adapters-zh.jsonc`（中文）或 `docs/reference/adapters.jsonc`（英文）。这是从 `fields.py` 自动生成的，字段名、类型、默认值和语义以此为准。
3. **写脚本前看钩子 API**：本指南「自定义脚本钩子」章节，以及仓库内中文参考 [adapters-scripts-hook.md](reference/adapters-scripts-hook.md)。
4. **动手前先观察现状**：

```bash
# 看当前 URL 命中了什么配置
python manage.py site_adapter show-config "<url>"

# 看默认引擎已经能输出什么
python manage.py site_adapter pipeline "<url>" --skip-snapshot
```

5. **优先声明式，再考虑脚本**。绝大多数网站只需 `select_*`、`keep_elements`、`remove_elements`、`process_lazy_images` 等字段；只有 JS 渲染、动态交互等场景才需要钩子脚本。
6. **验证与提交**：

```bash
python manage.py site_adapter validate
python manage.py site_adapter pipeline "<url>" --skip-snapshot
```

### AI Agent 写规则时的检查清单

- 域名 key 是否与真实主机名匹配？`www.example.com` 和 `example.com` 是不同的 key。
- 是否声明了 `_meta.id`、`_meta.name`、`_meta.version`？修改后 version 是否递增？
- 选择的字段是否在 `fields.py` 中真实存在？用 `validate` 检查 unknown field。
- `scripts` 是数组 `[{ "path": ..., "hook": ... }]`，不是旧的单个 `script` 字符串；`path` 自动补全 `scripts/`。
- `auth` 当前支持 `cookie` / `headers` / `oauth2` / `basic_auth`，旧的 `token` 字段已废弃。
- reader 不支持脚本；不要在 reader 里写 `scripts`。
- 数组字段（`remove_elements` 等）是整体替换，不是合并；小心 `_builtin` 已有默认值被清空。
- 需要覆盖时用 `null` 删除继承的键，而不是留空数组（空数组会替换掉基线值）。
- 修改后跑 `validate` + `pipeline` 双重验证。

---

## 常见问题

**Q: 我的配置为什么没生效？**

先运行 `python manage.py site_adapter show-config "<url>"` 查看合并后的最终配置。常见原因：

1. 域名 key 不匹配（`www` 与裸域名不同）。
2. 被更高优先级的适配器覆盖（检查 `_adapters` 顺序）。
3. `_builtin` 基线被覆盖或数组整体替换。
4. 字段拼写错误或使用了已废弃字段（`validate` 会提示）。
5. 适配器 `enabled: false` 或域名在 `_disabled_domains` 中。

**Q: 选择器提取不到内容？**

- 确认选择器在当前页面真实存在，且页面不是纯 JS 渲染。
- `meta` 标签需要选到 `content` 属性；HTML 图片字段会自动取 `src`/`href`/`content`/`url`。
- 使用 `::json(path)` 从 JSON-LD 提取，或用 `::attr(name)` 读取 `data-src` 等属性，或改用 `use_browser`。

**Q: 快照太慢或太乱？**

- 优先声明式清理，避免脚本。
- 用 `singlefile_args` 调整等待与拦截，例如 `{"--browser-wait-delay": 2000, "--block-videos": "true"}`。
- 用 `wait_elements` 等待关键元素，而不是固定延时。

**Q: Cookie 总是失效？**

- 检查 `verify` 配置是否过严或过松。
- 查看 `data/site_adapters/logs/execution-*.jsonl` 中的验证结果。
- 必要时显式配置 `refresh.user_data_dir: "default"` 复用持久化浏览器 profile；只有显式声明才会携带 profile，方便识别并发快照/Cookie 刷新时的 profile 锁冲突。

**Q: 远程订阅源更新不生效？**

- 等待 `update_interval` 到期，或在管理界面点击刷新按钮立即更新。
- 确认 `_meta.version` 已递增；若配置了 `checkUpdateUrl`，确认版本接口返回的 version 更大。

---

## 相关资料与文档链接

### 配置文件字段权威参考

- [fields.py](../site_adapters/services/config/fields.py) — 所有字段定义的单一信源
- [reference/adapters-zh.jsonc](reference/adapters-zh.jsonc) — 中文完整字段参考
- [reference/adapters.jsonc](reference/adapters.jsonc) — 英文完整字段参考
- 重新生成方式：`python scripts/generate-adapters-reference.py`。

> 提示：`docs/reference/*.jsonc` 是带注释的 JSONC，可以直接复制某个 section 作为配置起点；任何字段语义分歧都以 `fields.py` 生成的最新参考为准。

### 自定义脚本指南&模板

- [adapters-scripts-hook.md](reference/adapters-scripts-hook.md) — API 参考：配置键、返回约定、脚本模板。
- [metadata.py](../site_adapters/services/engine/scaffolds/metadata.py) — metadata Python 脚本模板。
- [metadata.js](../site_adapters/services/engine/scaffolds/metadata.js) — metadata JavaScript 脚本模板。
- [snapshot.py](../site_adapters/services/engine/scaffolds/snapshot.py) — snapshot Python 脚本模板。
- [snapshot.js](../site_adapters/services/engine/scaffolds/snapshot.js) — snapshot SingleFile 模式脚本模板。
- [snapshot_node.js](../site_adapters/services/engine/scaffolds/snapshot_node.js) — snapshot external Node 脚本模板。

### 配置文件示例

- [defaults/adapters.jsonc](../site_adapters/services/config/adapters/defaults/adapters.jsonc) — 内置适配器，即系统 `_builtin` 基线，展示默认选择器与默认行为。
- `data/site_adapters/adapters/defaults/adapters.jsonc` - 内置适配器（运行时副本）
- [WooHooDai/linkding-cn-adapters](https://github.com/WooHooDai/linkding-cn-adapters) - 官方适配器远程订阅源

### 源代码参考

- [resolver.py](../site_adapters/services/config/resolver.py) — 配置合并与解析实现。
- [loader.py](../site_adapters/services/config/loader.py) — 订阅源加载与优先级实现。
