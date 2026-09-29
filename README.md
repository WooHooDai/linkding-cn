## linkding-cn

linkding-cn 是一款开源、自托管的网页管理&阅读工具（书签管理器 + 稍后读工具），基于 [linkding](https://github.com/sissbruecker/linkding) 二次开发。

![](https://github.com/user-attachments/assets/725f6d6f-c286-4119-a280-c4cde450e171)

<details style="align:center;">
    <summary>点击查看截图：书签列表</summary>
    <img width="1000" height="933" alt="full" src="https://github.com/user-attachments/assets/767526f7-ebc0-4758-a222-b631d1769e67" />
</details>

<details style="align:center;">
    <summary>点击查看截图：阅读页面</summary>
    <img width="1912" height="939" alt="full" src="https://github.com/user-attachments/assets/f10bc340-e786-4e16-b977-912f69d67837" />
</details>


## 核心特性

- 🌍 多语言：内置 **简体中文🇨🇳**、English，支持[增加更多其他语言](./docs/i18n-guide.md)
- 📦 快照存档：自动获取网页的 Favicon、元数据、HTML 快照。
    - 支持通过声明或自定义脚本[自定义适配规则](./docs/site-adapters.md)，对元数据、快照、阅读页面进行灵活的获取、清理、定制
    - 内置**官方适配订阅源**，欢迎前往[WooHooDai/linkding-cn-adapters](https://github.com/WooHooDai/linkding-cn-adapters) 提适配需求或贡献适配规则
- 📚 阅读模式：提供统一、简洁、可定制、可高亮批注的阅读页面
    - 自动提取网页正文（使用 [Defuddle](https://github.com/kepano/defuddle)），支持[自定义正文提取规则](./docs/site-adapters.md)。
    - 支持自定义阅读页面的主题、字体、字号、行高、页面宽度、阅读速度。
    - 支持高亮&批注，可自定义高亮、批注复制模式
- 🏷️ 单级标签：简单、易用、强大兼具的书签
    - 输入自动补全：支持英文、中文拼音全拼任意子字符串匹配，支持中文拼音首字母前缀匹配
    - 快捷标签：添加自定义按钮到书签工具栏，一键为书签增加/删除预设的标签（组）
    - 动态筛选：与书签列表、高亮列表动态联动筛选；支持首字母聚合、自动树状嵌
    - 独立管理页面：编辑、删除、合并、筛选、排序
- 🎯 过滤器：保存常用的筛选条件、排序依据为过滤器，轻松复用，免除手动分类文件夹
- 🔍 搜索引擎：支持逻辑语法，可[限定搜索范围](https://github.com/WooHooDai/linkding-cn/wiki/%E4%B9%A6%E7%AD%BE%E5%88%97%E8%A1%A8#-%E6%90%9C%E7%B4%A2)
- 🎲 随机按钮：支持列表随机排序；支持随机打开书签的 URL/HTML 快照/阅读页面/详情
- 📊 数据统计：侧边栏收藏数据看板（热力图🔥/日历图📅） + 各场景动态书签数量计数
- ⚙️ 高度可定制：自定义 CSS；大量页面个性化设置项
- 🚥 书签健康检查：批量探测书签链接有效性，支持自定义域名状态检测
- 🌊 开放：
    - 多用户：支持账户密码/单点登录（SSO）
    - 分享：支持与其他用户、陌生访客分享指定书签
    - 导入&导出：Netscape HTML 格式的书签
    - REST API
- 🔧 维护简单：
    - 部署：单个 Docker 容器 + SQLite 即可部署
    - 迁移：自动化迁移，零破坏性变更

与原项目的区别见[_这里_](#与linkding的区别)

## 快速开始

使用 Docker Compose 部署：

**1. 准备配置文件**

- 新建容器目录`linkding-cn`
- 下载 [.env.sample](./.env.sample) 到容器目录，并重命名为 `.env`，
- 填写 `LD_SUPERUSER_NAME` 和 `LD_SUPERUSER_PASSWORD`（用于首次登录）。
- 下载 [docker-compose.yml](./docker-compose.yml) 到容器目录

**2. 启动服务**

- 在容器目录下运行

```bash
docker compose up -d
```
- 启动后访问 `http://localhost:9090` 即可使用。

**3. 更新**

如需更新，在容器目录下运行

```bash
docker compose pull && docker compose up -d
```

## 相关链接

- [linkding-cn 文档](https://github.com/WooHooDai/linkding-cn/wiki) — 本项目新增功能说明文档
- [linkding-cn 更新日志](./CHANGELOG.md)
- [linkding 文档](https://linkding.link/) — 原项目官方文档


## 与linkding的区别

| 功能 | linkding-cn  | linkding  |
|:---:|:---:|:---:|
|**语言**|_简体中文🇨🇳_ / English / [其他](./docs/i18n-guide.md)|English|
|**阅读模式**|阅读页面 + _高亮批注_ |阅读页面|
|**元数据&快照**|内置 + [_声明式适配/自定义脚本_](./docs/site-adapters.md)|内置|
|**书签工具栏自定义**|_支持启用/禁用、排序、显示模式_|❌|
|**快捷标签**|_支持一键添加/删除一个/多个标签_|❌|
|**标签聚合**|支持英文、_CJK（中日韩）_；支持 _树状模式_|仅支持英文聚合|
|**标签自动补全**|英文、_中文拼音全拼/首字母_|英文|
|**过滤器**|搜索词 + 标签 + _其他筛选项 + 排序_|搜索词 + 标签|
|**搜索引擎**|关键词 + 逻辑语法 + _限定范围↔️_|关键词 + 逻辑语法|
|**域名筛选**|_侧边栏筛选 + 搜索限定 + 自定义归一化_|❌|
|**数据看板**|_日历图📅 / 热力图🔥_|❌|
|**随机能力**|_列表随机_ / _单条目随机_|❌|
|**删除**|永久删除 / _回收站♻️_|永久删除|
|**书签健康检查**|_支持批量检查 + 自定义域名状态检测_|❌|


## 致谢

❤️ 感谢 [sissbruecker](https://github.com/sissbruecker) 创建了 [linkding](https://github.com/sissbruecker/linkding)，超级简洁优雅，令人爱不释手。