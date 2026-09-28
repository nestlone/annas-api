# annas-api

[![CI](https://github.com/nestlone/annas-api/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nestlone/annas-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Docker](https://img.shields.io/badge/deploy-docker-blue.svg)](compose.yaml)

面向受控部署的异步 HTTP API 与 CLI：检索书目、校验下载文件，可选将 DjVu 转为 PDF。

> 本项目不是任何第三方网站的官方客户端。请仅用于你拥有访问、下载和再分发权限的
> 资料；部署者需自行遵守适用法律、服务条款与网络政策。

## 功能

- **异步任务** —— 提交检索或下载后立即返回任务 ID，轮询获取结果。
- **持久状态** —— 任务状态与结果存入 SQLite，重启后保留。
- **校验下载** —— 发布文件前校验长度、MD5（若已知）与 PDF 可读性。
- **签名交付** —— 通过短时效 HMAC 链接提供文件，不暴露源站下载地址。
- **断点续传** —— 中断的下载按字节范围续传。
- **代理分流** —— 可选让 CDN 下载经由轮换代理池出网。
- **受控并发** —— 本地 Worker 池（1–10）配合 FIFO 排队。
- **一键部署** —— Docker Compose；密钥存放于未纳入 Git 的 `.env`。

## 快速开始

需要 Docker 与 Compose 插件。

```bash
cp .env.example .env   # 设置 FERRY_API_TOKEN 与 FERRY_API_SIGNING_KEY
docker compose up --build -d
curl http://127.0.0.1:8000/healthz
```

服务仅绑定 `127.0.0.1:8000`。交互式 OpenAPI 文档：<http://127.0.0.1:8000/docs>。

## 用法

```bash
# 提交检索。
curl -X POST http://127.0.0.1:8000/v1/search \
  -H "X-API-Key: $FERRY_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"example title","limit":5}'

# 查询任务、查看队列、取消排队中的任务。
curl -H "X-API-Key: $FERRY_API_TOKEN" http://127.0.0.1:8000/v1/jobs/<job-id>
curl -H "X-API-Key: $FERRY_API_TOKEN" 'http://127.0.0.1:8000/v1/jobs?status=queued'
curl -X POST -H "X-API-Key: $FERRY_API_TOKEN" \
  http://127.0.0.1:8000/v1/jobs/<job-id>/cancel
```

完整契约见 [HTTP API 参考](docs/api.md)，本地使用见 [CLI 参考](docs/cli.md)。

## 配置

服务读取少量环境变量（见 [`.env.example`](.env.example)）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FERRY_API_TOKEN` | — | 管理接口的 `X-API-Key`（必填）。 |
| `FERRY_API_SIGNING_KEY` | — | 下载链接的 HMAC 密钥（必填，须不同于 Token）。 |
| `FERRY_API_WORKERS` | `2` | 本地 Worker 数，限制为 1–10。 |
| `FERRY_API_FILE_URL_TTL` | `900` | 下载链接有效秒数（60–86400）。 |
| `FERRY_API_FILE_RETENTION_HOURS` | `24` | 任务完成后文件与记录的保留小时数（1–8760）。 |
| `FERRY_PROXY_POOL_URL` | — | 可选的轮换代理池地址，用于 CDN 下载。 |

详见[配置](docs/configuration.md)。

## 文档

- [架构](docs/architecture.md)
- [HTTP API](docs/api.md)
- [CLI](docs/cli.md)
- [配置](docs/configuration.md)
- [开发指南](docs/development.md)
- [Agent Skill](skill/README.md) —— 供 agent 直接接入的打包客户端
- [贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md) · [行为准则](CODE_OF_CONDUCT.md)

## 许可证

[MIT](LICENSE)

---

[English](README.md)
