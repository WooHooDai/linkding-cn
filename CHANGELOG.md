# 更新日志
## v1.0.4（2026/05/04）
### 当前项目

- 新增：i18n 国际化改造，目前支持简体中文、English，可[新增支持语言](https://github.com/WooHooDai/linkding-cn/blob/main/docs/i18n-maintenance.md)
- 新增：侧边栏 用户数据 功能模块，支持热力图/日历图双模式，支持日期筛选联动，支持总结、星期显示/隐藏
    - <details>
      <summary>热力图模式</summary>
      <img width="300" height="372" alt="image" src="https://github.com/user-attachments/assets/dcfa1f3d-090e-4341-9c78-5bb9b574daf0" />
    </details>

    - <details>
      <summary>日历图模式</summary>
      <img width="300" height="393" alt="image" src="https://github.com/user-attachments/assets/76b6372f-a6d1-4c2b-a01e-33d9577aacda" />
    </details>

    - <details>
      <summary>显示星期 & 隐藏总结</summary>
      <img width="300" height="306" alt="image" src="https://github.com/user-attachments/assets/1242e325-eeb4-4aa2-9b20-1ae1f4ce45ea" />
    </details>
- 新增：侧边栏 域名 功能模块，支持图标/完整双模式支持自定义域名归一化
    - <details>
      <summary>图标模式</summary>
      <img width="300" height="314" alt="image" src="https://github.com/user-attachments/assets/e285fe81-7116-48df-b609-3723d3d0d6a3" />
    </details>

    - <details>
      <summary>完整模式</summary>
      <img width="300" height="530" alt="image" src="https://github.com/user-attachments/assets/ec03a8aa-10f2-4a14-b378-cf8ea5dc8a08" />
    </details>
- 新增：侧边栏功能模块支持独立启用/关闭，支持排序，可在通用设置中配置。
- 新增：过滤器（Bundles）支持如下新的筛选项：HTML 快照、预览图、Favicon
- 新增：批量操作支持如下新的动作：创建 HTML 快照、删除所有快照、移至回收站
- 新增：元数据异步补全与重试机制
- 新增：快照、元数据抓取支持为指定 URL 加载自定义配置文件

- 优化：重构通用设置页面、集成设置页面布局与交互：卡片式分区，分区导航栏，一键生效
    - <details>
      <summary>通用设置</summary>
      <img width="1000" height="2525" alt="image" src="https://github.com/user-attachments/assets/fb44ecaf-6721-4426-9075-678bc1cd9a9d" />
    </details>

    - <details>
      <summary>集成设置</summary>
      <img width="1000" height="1027" alt="image" src="https://github.com/user-attachments/assets/16e94917-ec46-45ed-a8a8-d660379cc633" />
    </details>
- 优化：重构批量操作动作分组与排序
- 优化：二次确认修改为弹窗模式
- 优化：URL 字段中第一个 http/https 链接会被字段抽取作为书签链接
- 优化：favicon 加载策略，优先复用全局缓存，后台异步刷新
- 优化：快照调整为即时调度，新增书签的快照更快开始获取
- 优化：新增/编辑书签页面 URL 字段增加防抖、竞态保护
- 优化：重构、调整、补充单元测试 & E2E测试

- 修复：并发导致的重复 URL 书签
- 修复：书签下的标签点击后会被重复添加到搜索条件

### 合并上游原项目提交

- 新增：搜索引擎支持逻辑表达式（and, or, not）
- 新增：集成设置页面 API token 管理
- 新增：bookmarklet 元数据获取方案，支持服务器端优先、浏览器端优先
- 新增：支持禁用登录；支持 OIDC 登录；

- 优化：对视频与 PDF 资产的处理，收紧资源查看页 CSP
- 优化：重构前端为 component-only
- 优化：标签管理页面功能交互形式由独立页面改为弹窗
- 优化：标签聚合关闭时首字母不大写、不加粗
- 优化：切换到 uv 管理
- 优化：切换 Ruff lint/format

- 修复：Postgres 导入时缺少标签会导致错误
- 修复：导入书签时未进行 URL 规范化
- 修复：规范化 URL 缺失时的精确 URL 查重

---
## v1.0.3（2025/10/03）
### 合并上游原项目提交

- 新增：标签管理功能
- 新增：从当前搜索&筛选项新建过滤器
- 新增：批量创建 HTML 快照
- 新增：书签默认为分享的选项
- 新增：允许使用过滤器筛选 feeds
- 优化：支持 Ctrl/Cmd + Enter 提交书签
- 优化：URL 查重前归一化
- 优化：导入书签时，忽略超出长度限制的标签

### 当前项目

- 优化：预览图增强，解决 CORP 限制，支持分块传输图片
- 优化：favicon 增强，新增书签时预读取/预下载，新增后第一时间展现
- 优化：自定义脚本更改 URL 时，会二次查重
- 优化：批量编辑工具条粘性吸顶
- 优化：未读、分享的取消样式微调，更便于快速操作
- 优化：部分界面样式

---
## v1.0.2 (2025/09/18)

- 新增：自定义配置文件支持域名别名（或称配置共享）
- 修复：阅读模式无法滚动
- 修复：部分中文网页快照乱码
- 优化：滚动条紧贴右侧，且撑满整个窗口高度
- 优化：singlefile参数采取合并，优先级为：自定义配置文件 > 环境配置文件 > 内置参数

---
## v1.0.1 (2025/09/13)

- 增强&修复元数据、快照自定义获取能力（Wiki已更新说明）
    - 新增：自定义配置文件支持相对路径
    - 优化：singlefile服务参数调整
    - 优化：自定义配置singlefile参数强制使用词典形式
    - 修复：自定义脚本cookie解析错误
- 修复：移动端左右无边距

---
## v1.0.0 (2025/09/08)

第一版发布，相对linkding进行了如下修改和增强：

- 🇨🇳 中文本地化：界面翻译；标签聚合支持中文；时区与日期处理；favicon服务默认使用query.domains，无需🪜
- 🔍 搜索增强：新增支持限定搜索范围（标题、描述、笔记、url）
- 📅 筛选增强：新增日期筛选（支持绝对日期和相对日期）；新增标签状态筛选（有标签/无标签）
- 🎲 排序增强：新增随机排序；支持按删除时间排序（回收站中）
- 🤖 过滤器（Bundle）增强：新增支持二级过滤器；新增支持所有筛选项
- 🐞 元数据获取增强：默认解析支持更多主流网站；新增支持自定义脚本
- ⚡️ 快照增强：新增支持自定义脚本（引入drissionpage依赖）；新增支持重命名
- 📖 阅读模式增强：支持无快照书签开启阅读模式
- ♻️ 回收站：新增功能，书签删除时默认移动到回收站，可操作还原、永久删除，支持批量操作
- 👀 界面增强
    - 书签列表和过滤器显示书签数量
    - 粘性工具：搜索栏、分页导航栏、侧边栏可固定在屏幕上，方便操作
    - 快速筛选同域名标签：点击favicon快速筛选（支持自定义域名归一化规则）
    - 独立滚动条：书签列表、侧边栏滚动条互不影响
    - 位置记忆：编辑书签后书签列表浏览位置不变
    - 折叠状态记忆：同一会话中、侧边栏的过滤器、标签、二级过滤器折叠状态将被保持
    - 快速加载：预览图和favicon加载更快、更及时
- 🔧 样式增强：各类影响操作或美观的样式问题

---