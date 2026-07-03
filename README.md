# Remote Monitor

跨平台远程屏幕监控工具。服务端运行图形界面查看多个客户端的实时屏幕，客户端通过 TLS 加密通道推送屏幕画面。

## 功能

- 多客户端同时连接，IP 列表管理
- TLS 加密传输 + 密码认证
- 全屏查看、断开客户端
- 自动重连（客户端侧）
- 跨平台：服务端 Windows（PyQt5），客户端 Windows/Linux

## 环境要求

- Python 3.8+
- 操作系统：Windows / Linux

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 生成 TLS 证书

```bash
python generate_cert.py
```

生成 `server.crt` 和 `server.key`，用于加密传输。

### 3. 设置密码（可选）

默认密码为 `monitor123`，可通过环境变量自定义：

```bash
# Windows PowerShell
$env:MONITOR_PASSWORD="your_password"

# Linux / macOS
export MONITOR_PASSWORD="your_password"
```

### 4. 启动服务端

```bash
python gui/server.py
```

可选参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--port` | 监听端口 | 8888 |
| `--cert` | 证书路径 | server.crt |
| `--key` | 私钥路径 | server.key |
| `--password` | 认证密码 | 环境变量 MONITOR_PASSWORD 或 monitor123 |

### 5. 启动客户端

```bash
python client.py --host <服务端IP>
```

可选参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--host` | 服务端 IP | 127.0.0.1 |
| `--port` | 服务端端口 | 8888 |
| `--password` | 认证密码 | 环境变量或 monitor123 |
| `--fps` | 推送帧率 | 5.0（公网建议 1-3） |
| `--quality` | JPEG 压缩质量 1-100 | 70（公网建议 40-60） |
| `--reconnect-delay` | 断线重连间隔（秒） | 1.0 |

也可以直接修改client.py中的默认参数，例如将默认IP修改为123.45.67.89。

示例（公网场景，降低帧率和画质）：

```bash
python client.py --host 123.45.67.89 --fps 2 --quality 50
```

## 项目结构

```
remote_monitor/
├── gui/
│   └── server.py        # 服务端 GUI（PyQt5）
├── client.py             # 屏幕推送客户端
├── generate_cert.py      # TLS 证书生成工具
├── requirements.txt      # Python 依赖
└── .gitignore
```

## 安全说明

- 所有通信通过 TLS 加密，证书用 `generate_cert.py` 生成
- 连接时需密码认证，建议通过环境变量 `MONITOR_PASSWORD` 传入
- 建议定期更换证书和密码
