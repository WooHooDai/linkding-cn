Docker 镜像构建、验证与发布

默认镜像继续提供完整功能，基础版保留 Node 与 Defuddle 阅读提取。各变体同时支持 linux/amd64 和 linux/arm64。

| 变体 | 构建参数 | 正式标签 | 浏览器功能 |
| --- | --- | --- | --- |
| Debian Plus | 默认 | latest、v版本；兼容别名 latest-plus、v版本-plus | Chromium、Python/JS Playwright、快照、Cookie 刷新、uBlock |
| Debian Base | --base | latest-base、v版本-base | 不包含 Chromium；支持书签管理和阅读提取 |
| Alpine Base | --base --alpine | latest-base-alpine、v版本-base-alpine；兼容别名 latest-alpine、v版本-alpine | 不包含 Chromium；支持书签管理和阅读提取 |

Alpine Plus 不再作为可发布目标：Python Playwright 1.62.0 没有 musl wheel。旧 Dockerfile 的对应路径不能可靠构建。基础版设置 LD_ENABLE_SNAPSHOTS=False；需要浏览器元数据、快照或自动 Cookie 获取时使用 Debian Plus。

端口由 `LD_SERVER_PORT` 控制，未设置时使用 `9090`；`bootstrap.sh` 和健康检查使用同一默认值。
Dockerfile 的 `EXPOSE 9090` 只是默认端口声明，不会限制应用监听端口，也不会自动发布端口。
Compose 默认映射为 `${LD_HOST_PORT:-9090}:9090`：只修改宿主机端口时设置 `LD_HOST_PORT` 即可；
如果设置了不同的 `LD_SERVER_PORT`，还需同步修改映射右侧的容器端口。
`docker run -P` 使用的是 `EXPOSE` 声明；自定义监听端口时应显式使用 `-p 宿主端口:容器端口`。

**本地构建与验证**

在仓库根目录执行：

```sh
# 默认只加载到本机；使用独立标签，避免覆盖正在使用的 latest。
scripts/build-docker.sh --load --platform linux/arm64 \
  --tag linkding-cn:verify --oci /tmp/linkding-verify.oci.tar

# 生产容器回归：临时内部网络、临时数据库、不挂载用户数据。
python3 scripts/verify-docker-image.py test linkding-cn:verify --platform linux/arm64 --strict \
  --output-dir tmp/image-test

# 与已发布镜像比较单架构压缩层总量。
python3 scripts/verify-docker-image.py size /tmp/linkding-verify.oci.tar \
  --baseline woohoodai/linkding-cn:latest --platform linux/arm64 \
  --output tmp/image-test/size.json

# PostgreSQL 路径；测试脚本只使用已下载的数据库镜像。
docker pull postgres:16-alpine
python3 scripts/verify-docker-image.py test linkding-cn:verify --platform linux/arm64 --strict \
  --postgres postgres:16-alpine --output-dir tmp/image-test-postgres
```

amd64 使用相应的 --platform。基础版在构建命令增加 --base，在测试命令增加 --base；Alpine 再增加 --alpine。本地脚本默认使用清华 APT/APK 镜像源，--no-mirror 使用官方源。--oci 需要支持多输出的 Docker Buildx；当前验证使用 Buildx 的 docker-container builder。

脚本支持任意顺序的参数；-- 后的参数传给 buildx。--load 只接受一个架构；--push 默认构建两个架构。--print-tags 仅打印标签，可用于检查变体映射。--push 会直接发布，本地使用者应先运行验证；推荐正式发布走 GitHub 工作流。

`scripts/verify-docker-image.py test` 会验证：服务启动、实际 HEALTHCHECK、自定义端口/路径、后台 worker、数据库/ICU、中文翻译、静态文件、最小 Node 依赖、Defuddle。Plus 还验证生产浏览器调用、中文字体、图片/Canvas/SVG 场景、登录后的管理页面与适配器弹窗、JS 快照与 after hook、Cookie 刷新、SingleFile、www-data 下的 uBlock。生成 result.json、系统包清单、容器日志及截图，最后移除自己的临时容器和网络。

**输入锁定与升级**

Python/Node/uv 镜像按 digest 固定。生产 Playwright 使用 pyproject.toml 的 browser 组，PostgreSQL 使用 postgres 组；Docker 构建通过 uv sync --locked 安装。npm ci 使用 package-lock.json，其中 SingleFile 与 simple-cdp 等传递依赖一并锁定。

Node 固定 24.20.0；SingleFile 2.1.3 要求 Node >=24。两个 Python/JS Playwright 包分别保留自己的驱动代码，共用 /usr/local/bin/node。不要在最终镜像安装工具后再用额外一层删除；应在 builder 中生成最终需要的文件后复制。

Chromium 当前固定为 152.0.7977.82-1~deb12u1。APT 仍使用发行版实时仓库，并将实际安装版本保存为 /usr/share/linkding/os-packages.tsv；这不是完整的 Debian 仓库快照。旧 Chromium 版本从仓库下架时，显式更新 Dockerfile 或使用 --build-arg CHROMIUM_VERSION=可用版本，再运行浏览器回归。保持受控安全更新，不应为了固定体积冻结安全补丁。

uBlock 沿用构建时获取最新稳定版 Chromium 扩展的方式。构建脚本只跳过 `ublock-build` 下载阶段的缓存，下载工具和其他依赖继续使用缓存；下载及 ZIP 完整性检查成功后才替换目录。实际版本记录在扩展的 manifest.json 和镜像回归结果中，COPY --chown 保留正确权限。直接运行 buildx 时也应带上 `--no-cache-filter ublock-build`，才能重新检查最新版本。

升级依赖时使用 uv lock 和 npm install --package-lock-only，检查是否出现非预期版本变化，然后执行单元测试和镜像验证。前端打包依赖放 devDependencies；运行脚本真正 require 的库放 dependencies。不要把原生二进制从 BUILDPLATFORM 的依赖目录复制到其他架构。

**CI 与发布**

main.yaml 在 main 的 push 与 pull request 上执行单元、现有 E2E 和镜像验证；Node 使用 24.20.0，单元测试覆盖 bookmarks 与 site_adapters。

image-validation.yaml 使用两个架构的原生 GitHub runner，验证三个变体共六个镜像。正式发布由 build.yaml 的 workflow_dispatch 触发，publish 默认关闭。全部验证成功且 publish 为 true 后，工作流上传已验证的 OCI 产物，再用 skopeo --preserve-digests 复制并组装多架构标签；发布阶段不重新构建，避免依赖漂移。

Plus 默认与注册表 latest 比较，读取时解析到具体平台 digest；也可传入固定 digest。新建的 Base 变体第一次发布没有基线，只记录体积；之后需把 baseline_base / baseline_alpine 指向各自上一个已发布变体。比较不同架构或带有不同变体标签的镜像会报错。

体积门禁以 OCI 压缩层字节数为准，不包含 attestation，也不使用本机共享磁盘占用。增长超过 max(5 MB, 1%) 提示；超过 max(10 MB, 3%) 失败，并输出新增/变化的最大层。检查新增功能成本或依赖变更后再决定是否更新基线。

源码发布脚本使用 main，检查工作区和标签冲突，并原子推送分支与标签。仓库包含上游历史标签，若 v版本已指向其他提交，脚本会拒绝覆盖；需选择未使用的版本。镜像测试和构建本身不推送 Git，也不修改 version.txt。

构建工具自检：

```sh
python3 -m unittest discover -s scripts/tests -v
uv lock --check
bash -n scripts/build-docker.sh scripts/release.sh
```

长期维护的入口是 `scripts/build-docker.sh` 和 `scripts/verify-docker-image.py`。后者通过
`test`、`size` 子命令分别执行镜像回归和体积检查，容器内检查也由同一脚本执行。
`scripts/tests/test_build_tools.py` 验证构建工具本身，不能替代实际镜像回归。

一次性分析记录放在 `docs/plans/`，日志和截图放在 `tmp/`，两个目录均不提交。
