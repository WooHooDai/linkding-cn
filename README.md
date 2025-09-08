<div align="center">
    <br>
    <a href="https://github.com/WooHooDai/linkding-cn">
        <img src="assets/header.svg" height="50">
    </a>
    <br>
</div>

## 项目说明

基于[linkding](https://github.com/sissbruecker/linkding)（forked from[linkding](https://github.com/sissbruecker/linkding)）修改和增强，感谢 sissbruecker 大佬做出这么棒的书签管理器！

**具体修改和增强如下：**
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

**截屏：**
![截屏](https://github.com/user-attachments/assets/a1e4bfc3-fdfd-4059-9ebc-b77dde109f93)

## 开始使用

本项目通过 Docker-compose 部署

1. 环境配置文件请参考 [.env.sample](https://github.com/WooHooDai/linkding-cn/blob/main/.env.sample) (使用时注意文件名需修改为`.env`,其中`LD_SUPERUSER_NAME`和`LD_SUPERUSER_PASSWORD`务必填写用于登录)
2. 容器配置文件请参考 [docker-compose.yml](https://github.com/WooHooDai/linkding-cn/blob/main/docker-compose.yml)
3. 通过命令行启动容器： `docker compose up -d`

## 文档

本项目新增/增强的功能，请参阅[这里](https://github.com/WooHooDai/linkding-cn/wiki)，其他功能请参阅[原项目文档](https://linkding.link/installation/)

---
> 以下为原项目的README信息，在此意译为中文，供参考

##  简介

linkding 是一个可自部署的书签管理器。
它的设计目标是极简、快速、便于通过Docker部署。

名字寓意：
- *link* 是链接(url)和书签(bookmark)的近义词
- *Ding* 在德语中是事物的意思
- ...连在一起意思是管理你链接的工具

**特色概览：**
- 界面简洁，专为可读性优化
- 标签管理系统
- 批量编辑，Markdown 笔记，稍后读
- 与其他用户或访客分享书签
- 自动获取书签元数据（标题、描述、icons）
- 自动保存网页快照，包括本地 HTML 文件和在线存档（Internet Archive）
- 导入/导出书签，支持 Netscape HTML 格式
- 可作为渐进式网页应用（PWA）安装
- 浏览器插件：[Firefox](https://addons.mozilla.org/firefox/addon/linkding-extension/) / [Chrome](https://chrome.google.com/webstore/detail/linkding-extension/beakmhbijpdhipnjhnclmhgjlddhidpe) / bookmarklet
- SSO: OIDC；身份验证代理
- REST API：可供开发第三方应用
- 管理员面板：可供管理用户服务、原始数据


**在线体验:** https://demo.linkding.link/

**截图:**

![截图](/docs/public/linkding-screenshot.png?raw=true "Screenshot")

## 开始使用

请阅读如下帮助链接：
- [在自己的服务器上部署linkding](https://linkding.link/installation) 或 [检查托管选项](https://linkding.link/managed-hosting)
- [安装浏览器插件](https://linkding.link/browser-extension)
- [检阅社区项目](https://linkding.link/community)，可从中获取手机app、浏览器插件、库等

## 文档

完整文档请查阅 [linkding.link](https://linkding.link/)。

如果你想对文档作出贡献，你可以在 `docs` 文件夹中找到源文件。

如果你想贡献社区项目，请放心[提交PR](https://github.com/sissbruecker/linkding/edit/master/docs/src/content/docs/community.md).

## 贡献

诚挚期待您贡献小的程序优化、错误修复和文档优化。

如果您想贡献较大的功能，可以考虑先开启一个issue供大家讨论。

对于不符合项目目标、或我不想维护的功能PR，我可能选择忽略。

## 开发

应用使用 Django 框架构建。你可以从优秀的 [Django 文档](https://docs.djangoproject.com/en/4.1/)开始。`bookmarks`文件夹包含了实际的书签应用。其他部分的代码应该可以自解释，或者它们与 Django 相关。

### 前置条件
- Python 3.12
- Node.js

### 启动

为应用创建一个虚拟环境 (https://docs.python.org/3/tutorial/venv.html):
```
python3 -m venv ~/environments/linkding
```
在 shell 中激活环境:
```
source ~/environments/linkding/bin/activate[.csh|.fish]
```
在环境中安装应用所需依赖:
```
pip3 install -r requirements.txt -r requirements.dev.txt
```
安装前端依赖:
```
npm install
```
初始化数据库:
```
mkdir -p data
python3 manage.py migrate
```
为前端创建一个用户:
```
python3 manage.py createsuperuser --username=joe --email=joe@example.com
```
启动 Node.js 开发服务器 (用于运行编译了的 Javascript 组件，例如标签自动补全):
```
npm run dev
```
启动 Django 开发服务器:
```
python3 manage.py runserver
```
现在可以通过 http://localhost:8000 打开前端

### 测试

使用 pytest 运行所有测试:
```
make test
```

### 格式化

使用 black 格式化 Python，使用 prettier 格式化 Javascrip:
```
make format
```

### DevContainers

本仓库也支持 DevContainers: [![Open in Remote - Containers](https://img.shields.io/static/v1?label=Remote%20-%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/sissbruecker/linkding.git)

一旦检出，只有如下命令是必要的:

为前端创建一个用户:
```
python3 manage.py createsuperuser --username=joe --email=joe@example.com
```
启动 Node.js 开发服务器 (用于运行编译了的 Javascript 组件，例如标签自动补全):
```
npm run dev
```
启动 Django 开发服务器:
```
python3 manage.py runserver
```
现在可以通过 http://localhost:8000 打开前端
