

## linkding-cn

linkding-cn 是基于 [linkding](https://github.com/sissbruecker/linkding) 二次开发增强的自托管开源书签管理器。

- 🌍 多语言：内置 **简体中文🇨🇳**、English
- 🦄 新增功能：新增收藏数据看板（**热力图🔥**/日历图📅）、**回收站♻️**、自定义元数据&快照**爬取脚本🐞**等特有功能
- ⚙️ 深度优化：对界面、搜索、筛选、排序、过滤器等进行了大量优化。

完整增强列表，及与原项目对比见[_这里_](#增强功能)

## 截图

![](https://github.com/user-attachments/assets/ee1e8ac1-cfff-405e-889a-db8fbde09385)

<details style="align:center;">
    <summary>点击查看完整截图</summary>
    <img width="1000" height="920" alt="full" src="https://github.com/user-attachments/assets/3d9b644f-4c74-4bb3-8bb3-93881de8bb92" />
</details>


## 完整功能

<details>
<summary>点击查看功能列表</summary>

- 🌍 多语言：内置**简体中文/English**，支持[增加更多其他语言](./docs/i18n-maintenance.md)
- 📦 永久存档：自动获取网站元数据、保存本地快照，支持[**自定义元数据/快照获取脚本🐞**](https://github.com/WooHooDai/linkding-cn/wiki/%E8%87%AA%E5%AE%9A%E4%B9%89%E8%84%9A%E6%9C%AC)
- 🧠 超强管理：
    - 收藏：[Firefox 扩展](https://addons.mozilla.org/firefox/addon/linkding-extension/)、[Chrome 扩展](https://chrome.google.com/webstore/detail/linkding-extension/beakmhbijpdhipnjhnclmhgjlddhidpe)、书签小程序、REST API
    - 搜索：支持逻辑语法，可限定搜索范围
    - 筛选：按搜索词/标签/域名/日期/书签状态等筛选，可保存为过滤器
    - 排序：按日期/标题，或**随机排序🎲**保持新鲜感
    - 回顾：**日历图/热力图🔥**，展示指定周期收藏情况
    - 整理：可批量操作；可归档、**移至回收站♻️**或永久删除
    - 数据库：提供原始数据管理面板
- ⚙️ 高度可定制：
    - 自定义 CSS
    - 自定义 侧边栏功能、排序
    - 自定义书签列表界面展示细节
    - 自定义页面功能模块是否跟随
- 🌊 开放：
    - 多用户：支持账户密码/单点登录（SSO）
    - 分享：支持与其他用户、陌生访客分享指定书签
    - 导入&导出：Netscape HTML 格式的书签
    - REST API
- 🔧 维护简单：
    - 部署：单个 Docker 容器 + SQLite 即可部署
    - 迁移：自动化迁移，零破坏性变更
</details>

## 增强功能

| 功能 | linkding-cn  | linkding  |
|:---:|:---:|:---:|
|**语言**|_简体中文🇨🇳_ / English / [其他](./docs/i18n-maintenance.md)|English|
|**数据看板**|_日历图📅 / 热力图🔥_|无|
|**删除**|永久删除 / _回收站♻️_|永久删除|
|**标签聚合**|英文 + _CJK（中日韩）_|英文|
|**过滤器**|搜索词 + 标签 + _排序 + 书签状态_|搜索词 + 标签|
|**域名筛选**|_侧边栏筛选 + 搜索限定 + 自定义归一化_|无|
|**排序**|日期 / 标题 / _随机🎲_|日期 / 标题|
|**搜索**|关键词 + 逻辑语法 + _限定范围↔️_|关键词 + 逻辑语法|
|**元数据&快照**|内置 / [_自定义获取脚本🐞_](https://github.com/WooHooDai/linkding-cn/wiki/%E8%87%AA%E5%AE%9A%E4%B9%89%E8%84%9A%E6%9C%AC)|内置|

### 🧙 界面增强

- 设置页：卡片式分区 + 导航栏跟随 + 一键生效
- 书签列表：tooltip 展示完整描述；预览图、favicon 加载更快
- 搜索栏：页面滚动跟随；随机排序按钮；更多筛选项
- 侧边栏：页面滚动跟随；功能模块独立启停、排序、展开折叠
- 过滤器：显示书签数量；支持二级层级
- 其他样式增强：可查看[更新日志](./CHANGELOG.md)

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

## 致谢

❤️ 感谢 [sissbruecker](https://github.com/sissbruecker) 创建了 [linkding](https://github.com/sissbruecker/linkding)，真的是超级简洁优雅，一眼爱上。