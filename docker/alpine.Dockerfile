ARG PYTHON_IMAGE=python:3.13.7-alpine3.21@sha256:0c3d4f28025c9adc2c03326aa160dde8f53faaa8684134a0e146e4edca28a946
ARG NODE_IMAGE=node:24.20.0-alpine3.23@sha256:0388af2af070cd4736a1567cfed02469ba117848845b4165d87a333edb53d2ca

# Only generated JS/CSS is copied from the native build platform.
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS node-build
WORKDIR /etc/linkding
COPY rollup.config.mjs postcss.config.js esbuild.config.mjs package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY bookmarks/frontend ./bookmarks/frontend
COPY bookmarks/styles ./bookmarks/styles
COPY bookmarks/services/vendor/defuddle_entry.js ./bookmarks/services/vendor/defuddle_entry.js
COPY site_adapters/frontend ./site_adapters/frontend
COPY site_adapters/styles ./site_adapters/styles
RUN NODE_ENV=production POSTCSS_DISABLE_CACHE=true npm run build

# Native binaries and runtime packages always use the target architecture.
FROM ${NODE_IMAGE} AS node-runtime
WORKDIR /opt/node-runtime
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev --no-audit --no-fund

FROM ${PYTHON_IMAGE} AS build-deps
ARG APK_MIRROR=""
ARG TARGETARCH
RUN if [ -n "$APK_MIRROR" ]; then \
        sed -i "s|dl-cdn.alpinelinux.org|$APK_MIRROR|g" /etc/apk/repositories; \
    fi
RUN apk add --no-cache alpine-sdk linux-headers libpq-dev pkgconf icu-dev sqlite-dev \
    libffi-dev openssl-dev rust cargo git gettext wget unzip
COPY --from=ghcr.io/astral-sh/uv:0.8.13@sha256:4de5495181a281bc744845b9579acf7b221d6791f99bcc211b9ec13f417c2853 /uv /usr/local/bin/uv
WORKDIR /etc/linkding
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,id=uv-alpine-${TARGETARCH},target=/root/.cache/uv \
    uv sync --locked --no-dev --group postgres

FROM build-deps AS compile-icu
ARG SQLITE_RELEASE_YEAR=2023
ARG SQLITE_RELEASE=3430000
RUN wget -q https://www.sqlite.org/${SQLITE_RELEASE_YEAR}/sqlite-amalgamation-${SQLITE_RELEASE}.zip && \
    unzip -q sqlite-amalgamation-${SQLITE_RELEASE}.zip && \
    cp sqlite-amalgamation-${SQLITE_RELEASE}/sqlite3.h ./sqlite3.h && \
    cp sqlite-amalgamation-${SQLITE_RELEASE}/sqlite3ext.h ./sqlite3ext.h && \
    wget -q 'https://www.sqlite.org/src/raw/ext/icu/icu.c?name=91c021c7e3e8bbba286960810fa303295c622e323567b2e6def4ce58e4466e60' -O icu.c && \
    gcc -fPIC -shared icu.c $(pkg-config --libs --cflags icu-uc icu-io) -o libicu.so

FROM build-deps AS app-build
ENV PATH="/etc/linkding/.venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
COPY . .
COPY --from=node-build /etc/linkding/bookmarks/static bookmarks/static/
COPY --from=node-build /etc/linkding/site_adapters/static site_adapters/static/
COPY --from=node-build /etc/linkding/bookmarks/services/vendor/defuddle.js bookmarks/services/vendor/defuddle.js
# Third-party translations are already compiled in their wheels.
RUN mkdir -p data && \
    python manage.py compilemessages --ignore=.venv && \
    python manage.py collectstatic --noinput && \
    rm -rf bookmarks/frontend bookmarks/styles bookmarks/static \
        site_adapters/frontend site_adapters/styles site_adapters/static

# Base includes Node/Defuddle for reading, but no browser or snapshot support.
FROM ${PYTHON_IMAGE} AS linkding
ARG PYTHON_IMAGE
ARG APK_MIRROR=""
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/WooHooDai/linkding-cn" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.base.name="$PYTHON_IMAGE" \
      io.github.woohoodai.linkding.variant="base-alpine"
RUN if [ -n "$APK_MIRROR" ]; then \
        sed -i "s|dl-cdn.alpinelinux.org|$APK_MIRROR|g" /etc/apk/repositories; \
    fi
RUN apk add --no-cache bash curl icu libpq mailcap libssl3 libstdc++ ca-certificates
RUN adduser -u 82 -D -S -G www-data www-data
WORKDIR /etc/linkding
COPY --from=build-deps /etc/linkding/.venv .venv/
COPY --from=compile-icu /etc/linkding/libicu.so ./
COPY --from=app-build /etc/linkding/bookmarks bookmarks/
COPY --from=app-build /etc/linkding/site_adapters site_adapters/
COPY --from=app-build /etc/linkding/locale locale/
COPY --from=app-build /etc/linkding/static static/
COPY bootstrap.sh LICENSE.txt manage.py supervisord.conf uwsgi.ini version.txt ./
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
# The Alpine Node image omits the upstream license file; use the same pinned release.
COPY --from=node:24.20.0-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e /usr/local/LICENSE /usr/share/doc/node/LICENSE
COPY --from=node-runtime /opt/node-runtime/node_modules /opt/node-runtime/node_modules
ENV VIRTUAL_ENV=/etc/linkding/.venv
ENV PATH="/etc/linkding/.venv/bin:/opt/node-runtime/node_modules/.bin:$PATH"
ENV NODE_PATH=/opt/node-runtime/node_modules
ENV PLAYWRIGHT_NODEJS_PATH=/usr/local/bin/node
ENV LD_ENABLE_SNAPSHOTS=False
ENV UWSGI_MAX_FD=4096
RUN mkdir -p data /usr/share/linkding && chmod g+w . && chmod +x bootstrap.sh && \
    apk info -v > /usr/share/linkding/os-packages.tsv
EXPOSE 9090
HEALTHCHECK --interval=30s --retries=3 --timeout=3s \
    CMD curl -fsS http://localhost:${LD_SERVER_PORT:-9090}/${LD_CONTEXT_PATH}health || exit 1
CMD ["./bootstrap.sh"]

# Alpine Plus is not published: Python Playwright has no supported musl wheels.
