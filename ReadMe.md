# 基于KVM virtio-serial机制通信的设计

本文档介绍了如何基于KVM的virtio-serial机制实现虚拟机与宿主机之间的通信设计。virtio-serial是一种高效的虚拟化通信机制，适用于在虚拟机和宿主机之间传输数据。

此机制适用于管理面，相较于其它的通信机制，如IVSHMEM，性能较低，但实现简单，适合传输控制命令和状态信息。

---

## 目录

1. [原理](#原理)
2. [系统架构](#系统架构)
3. [通信协议设计](#通信协议设计)
4. [API设计](#api设计)
5. [数据流程](#数据流程)
6. [错误处理](#错误处理)
7. [安全设计](#安全设计)
8. [配置说明](#配置说明)
9. [实现步骤](#实现步骤)
10. [使用示例](#使用示例)
11. [部署指南](#部署指南)

---

## 原理

### virtio-serial机制概述

virtio-serial是QEMU/KVM提供的一种半虚拟化串口设备，允许虚拟机与宿主机之间进行高效的数据传输。

### 设备表现形式

| 端 | 设备类型 | 设备路径示例 |
|------|----------|-------------|
| Host | UNIX域套接字 | `/var/lib/libvirt/qemu/channel/target/domain-26-UCypher-newtest1/test.vserial.0` |
| Guest | 字符设备 | `/dev/virtio-ports/test.vserial.0` |

### 工作原理

```
+------------------+                      +------------------+
|      Host        |                      |      Guest       |
|                  |                      |                  |
| +-------------+  |    virtio-serial     | +-------------+  |
| | UDS Client  |<-+----------------------+->| Char Device |  |
| +-------------+  |  libvirt channel     | | /dev/virtio |  |
|                  |                      | +-------------+  |
+------------------+                      +------------------+
```

- **Host端**：libvirt/QEMU创建一个UNIX域套接字（UDS），路径通常为 `/var/lib/libvirt/qemu/channel/target/domain-XX-VMName/设备名`，应用程序作为客户端连接该套接字进行通信
- **Guest端**：内核加载virtio-serial驱动后，生成字符设备文件 `/dev/virtio-ports/设备名`，应用程序通过读写该设备进行通信

---

## 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Host (宿主机)                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                         Host RPC Client                          │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │   │
│  │  │ HTTP Request │  │   Command    │  │    File Transfer     │   │   │
│  │  │   Builder    │  │   Executor   │  │       Handler        │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────┘   │   │
│  │                            │                                      │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │              UDS Connection Manager                       │   │   │
│  │  │         (UNIX Domain Socket Client)                       │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│                                    ▼                                     │
│                    ┌──────────────────────────────┐                     │
│                    │  /var/lib/libvirt/.../       │                     │
│                    │     test.vserial.0 (UDS)     │                     │
│                    └──────────────────────────────┘                     │
└────────────────────────────────│────────────────────────────────────────┘
                                 │ virtio-serial
                                 │ (QEMU虚拟化)
┌────────────────────────────────│────────────────────────────────────────┐
│                                ▼                                         │
│                    ┌──────────────────────────────┐                     │
│                    │  /dev/virtio-ports/          │                     │
│                    │    test.vserial.0 (CharDev)  │                     │
│                    └──────────────────────────────┘                     │
│                                    │                                     │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                         Guest RPC Server                         │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │              Device Reader/Writer                         │   │   │
│  │  │         (Character Device Handler)                        │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  │                            │                                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │   │
│  │  │ HTTP Parser  │  │   Request    │  │    Response          │   │   │
│  │  │              │  │   Router     │  │    Builder           │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────┘   │   │
│  │                            │                                      │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │                    Command Handlers                       │   │   │
│  │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────┐  │   │   │
│  │  │  │ System  │  │  Shell  │  │  File   │  │   Custom    │  │   │   │
│  │  │  │  Info   │  │  Exec   │  │ Transfer│  │  Handlers   │  │   │   │
│  │  │  └─────────┘  └─────────┘  └─────────┘  └─────────────┘  │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              Guest (虚拟机)                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 模块说明

| 模块 | 位置 | 功能描述 |
|------|------|----------|
| Host RPC Client | Host | 发起RPC请求，管理与虚拟机的通信 |
| UDS Connection Manager | Host | 管理UNIX域套接字连接 |
| Guest RPC Server | Guest | 接收并处理RPC请求 |
| Device Reader/Writer | Guest | 读写virtio-serial字符设备 |
| HTTP Parser | Guest | 解析HTTP请求报文 |
| Request Router | Guest | 根据URL路由到对应处理器 |
| Command Handlers | Guest | 执行具体的命令处理逻辑 |

---

## 通信协议设计

### 协议选择：HTTP/1.1

选择HTTP作为应用层协议的原因：
- 成熟稳定，生态丰富
- 请求-响应模型清晰
- 支持多种Content-Type
- 易于调试和扩展
- 现有工具支持良好

### 报文格式

#### 请求报文格式

```http
POST /api/v1/{endpoint} HTTP/1.1
Host: virtio-rpc
Content-Type: application/json
Content-Length: {length}
X-Request-ID: {uuid}
X-Timestamp: {unix_timestamp}

{request_body}
```

#### 响应报文格式

```http
HTTP/1.1 {status_code} {status_text}
Content-Type: application/json
Content-Length: {length}
X-Request-ID: {uuid}
X-Response-Time: {milliseconds}

{response_body}
```

### 通用请求体结构

```json
{
    "version": "1.0",
    "action": "string",
    "params": {
        "key": "value"
    },
    "timeout": 30
}
```

### 通用响应体结构

```json
{
    "version": "1.0",
    "code": 0,
    "message": "success",
    "data": {},
    "timestamp": 1703654400
}
```

### 状态码定义

| HTTP状态码 | 业务码 | 含义 |
|-----------|--------|------|
| 200 | 0 | 成功 |
| 200 | 1001 | 命令执行失败 |
| 200 | 1002 | 文件操作失败 |
| 400 | 2001 | 请求参数错误 |
| 400 | 2002 | JSON解析失败 |
| 404 | 3001 | 接口不存在 |
| 500 | 5001 | 服务器内部错误 |
| 503 | 5002 | 服务不可用 |

---

## API设计

### API版本管理

所有API以 `/api/v1/` 为前缀，支持版本演进。

### 基础命令集

#### 1. 心跳检测

**Endpoint:** `GET /api/v1/ping`

**响应示例:**
```json
{
    "code": 0,
    "message": "pong",
    "data": {
        "timestamp": 1703654400,
        "uptime": 3600
    }
}
```

#### 2. 系统信息查询

**Endpoint:** `GET /api/v1/system/info`

**响应示例:**
```json
{
    "code": 0,
    "message": "success",
    "data": {
        "hostname": "guest-vm",
        "os": "Linux",
        "os_version": "Ubuntu 22.04",
        "kernel": "5.15.0-generic",
        "arch": "x86_64",
        "cpu_count": 4,
        "memory_total": 8589934592,
        "memory_available": 4294967296
    }
}
```

#### 3. 系统状态查询

**Endpoint:** `GET /api/v1/system/status`

**响应示例:**
```json
{
    "code": 0,
    "message": "success",
    "data": {
        "cpu_usage": 15.5,
        "memory_usage": 45.2,
        "disk_usage": {
            "/": 60.3,
            "/data": 25.8
        },
        "load_average": [0.5, 0.8, 1.2],
        "process_count": 128
    }
}
```

### 扩展命令集

#### 4. Shell命令执行

**Endpoint:** `POST /api/v1/shell/exec`

**请求体:**
```json
{
    "command": "ls -la /tmp",
    "timeout": 30,
    "working_dir": "/home/user",
    "env": {
        "PATH": "/usr/bin:/bin"
    }
}
```

**响应示例:**
```json
{
    "code": 0,
    "message": "success",
    "data": {
        "exit_code": 0,
        "stdout": "total 48\ndrwxrwxrwt 12 root root 4096 ...",
        "stderr": "",
        "duration_ms": 15
    }
}
```

#### 5. 文件上传（Host → Guest）

**Endpoint:** `POST /api/v1/file/upload`

**请求体:**
```json
{
    "path": "/tmp/config.json",
    "content": "base64_encoded_content",
    "mode": "0644",
    "owner": "root",
    "group": "root",
    "overwrite": true
}
```

**响应示例:**
```json
{
    "code": 0,
    "message": "success",
    "data": {
        "path": "/tmp/config.json",
        "size": 1024,
        "md5": "d41d8cd98f00b204e9800998ecf8427e"
    }
}
```

#### 6. 文件下载（Guest → Host）

**Endpoint:** `POST /api/v1/file/download`

**请求体:**
```json
{
    "path": "/var/log/syslog",
    "offset": 0,
    "length": 65536
}
```

**响应示例:**
```json
{
    "code": 0,
    "message": "success",
    "data": {
        "path": "/var/log/syslog",
        "content": "base64_encoded_content",
        "size": 65536,
        "total_size": 1048576,
        "md5": "abc123..."
    }
}
```

#### 7. 文件信息查询

**Endpoint:** `GET /api/v1/file/info?path=/etc/passwd`

**响应示例:**
```json
{
    "code": 0,
    "message": "success",
    "data": {
        "path": "/etc/passwd",
        "exists": true,
        "type": "file",
        "size": 2048,
        "mode": "0644",
        "owner": "root",
        "group": "root",
        "mtime": 1703654400,
        "md5": "abc123..."
    }
}
```

#### 8. 服务管理

**Endpoint:** `POST /api/v1/service/control`

**请求体:**
```json
{
    "name": "nginx",
    "action": "restart"
}
```

**action可选值:** `start`, `stop`, `restart`, `status`, `enable`, `disable`

**响应示例:**
```json
{
    "code": 0,
    "message": "success",
    "data": {
        "name": "nginx",
        "action": "restart",
        "status": "running",
        "pid": 1234
    }
}
```

### API汇总表

| 方法 | Endpoint | 功能描述 |
|------|----------|----------|
| GET | /api/v1/ping | 心跳检测 |
| GET | /api/v1/system/info | 获取系统信息 |
| GET | /api/v1/system/status | 获取系统状态 |
| POST | /api/v1/shell/exec | 执行Shell命令 |
| POST | /api/v1/file/upload | 上传文件到Guest |
| POST | /api/v1/file/download | 从Guest下载文件 |
| GET | /api/v1/file/info | 查询文件信息 |
| POST | /api/v1/service/control | 服务管理 |

---

## 数据流程

### 请求-响应流程

```
┌──────────┐                                              ┌──────────┐
│  Host    │                                              │  Guest   │
│  Client  │                                              │  Server  │
└────┬─────┘                                              └────┬─────┘
     │                                                          │
     │  1. 构建HTTP请求                                          │
     ├─────────────────────────────────────────────────────────>│
     │     POST /api/v1/shell/exec HTTP/1.1                    │
     │     Content-Type: application/json                      │
     │     {"command": "ls -la"}                               │
     │                                                          │
     │                                                          │  2. 解析HTTP请求
     │                                                          │  3. 路由到Handler
     │                                                          │  4. 执行命令
     │                                                          │  5. 构建响应
     │                                                          │
     │  6. 返回HTTP响应                                          │
     │<─────────────────────────────────────────────────────────┤
     │     HTTP/1.1 200 OK                                     │
     │     Content-Type: application/json                      │
     │     {"code": 0, "data": {...}}                          │
     │                                                          │
     ▼                                                          ▼
```

### 大文件传输流程

对于大文件传输，采用分块传输机制：

```
Host                                                    Guest
  │                                                        │
  │  1. 请求文件信息                                         │
  ├───────────────────────────────────────────────────────>│
  │<───────────────────────────────────────────────────────┤
  │     返回文件大小、MD5等                                   │
  │                                                        │
  │  2. 分块下载 (offset=0, length=64KB)                    │
  ├───────────────────────────────────────────────────────>│
  │<───────────────────────────────────────────────────────┤
  │                                                        │
  │  3. 分块下载 (offset=64KB, length=64KB)                 │
  ├───────────────────────────────────────────────────────>│
  │<───────────────────────────────────────────────────────┤
  │                                                        │
  │  ... 重复直到传输完成                                     │
  │                                                        │
  │  4. 校验MD5                                             │
  │                                                        │
  ▼                                                        ▼
```

---

## 错误处理

### 错误码体系

```
错误码结构: XYYY
  X   - 错误类别 (1-9)
  YYY - 具体错误 (001-999)

类别定义:
  1XXX - 命令执行错误
  2XXX - 请求参数错误
  3XXX - 资源不存在错误
  4XXX - 权限错误
  5XXX - 服务器错误
  6XXX - 网络/通信错误
```

### 详细错误码表

| 错误码 | 错误名称 | 描述 | 建议处理方式 |
|--------|----------|------|-------------|
| 0 | SUCCESS | 成功 | - |
| 1001 | CMD_EXEC_FAILED | 命令执行失败 | 检查命令语法和权限 |
| 1002 | CMD_TIMEOUT | 命令执行超时 | 增加超时时间或优化命令 |
| 1003 | CMD_NOT_FOUND | 命令不存在 | 检查命令是否安装 |
| 2001 | INVALID_PARAMS | 参数无效 | 检查请求参数 |
| 2002 | JSON_PARSE_ERROR | JSON解析失败 | 检查JSON格式 |
| 2003 | MISSING_REQUIRED | 缺少必需参数 | 补充必需参数 |
| 3001 | ENDPOINT_NOT_FOUND | 接口不存在 | 检查URL路径 |
| 3002 | FILE_NOT_FOUND | 文件不存在 | 检查文件路径 |
| 4001 | PERMISSION_DENIED | 权限不足 | 检查文件/操作权限 |
| 5001 | INTERNAL_ERROR | 内部错误 | 查看服务器日志 |
| 5002 | SERVICE_UNAVAILABLE | 服务不可用 | 稍后重试 |
| 6001 | CONNECTION_LOST | 连接断开 | 重新连接 |
| 6002 | READ_TIMEOUT | 读取超时 | 增加超时或重试 |

### 错误响应示例

```json
{
    "code": 1001,
    "message": "Command execution failed",
    "data": {
        "error_type": "CMD_EXEC_FAILED",
        "detail": "bash: command not found: invalid_cmd",
        "exit_code": 127
    }
}
```

### 重试策略

| 错误类型 | 是否重试 | 重试次数 | 重试间隔 |
|---------|---------|---------|---------|
| 网络错误 | 是 | 3 | 指数退避(1s, 2s, 4s) |
| 超时错误 | 是 | 2 | 固定间隔(5s) |
| 参数错误 | 否 | - | - |
| 权限错误 | 否 | - | - |
| 内部错误 | 是 | 1 | 固定间隔(10s) |

---

## 安全设计

### 安全威胁分析

| 威胁 | 风险等级 | 缓解措施 |
|------|---------|---------|
| 命令注入 | 高 | 命令白名单、参数校验 |
| 路径遍历 | 高 | 路径规范化、访问控制 |
| 拒绝服务 | 中 | 请求限流、超时控制 |
| 未授权访问 | 高 | 认证机制、权限检查 |

### 安全措施

#### 1. 命令执行安全

```python
# 命令白名单示例
ALLOWED_COMMANDS = {
    'ls', 'cat', 'head', 'tail', 'grep',
    'df', 'free', 'top', 'ps', 'netstat',
    'systemctl', 'service', 'journalctl'
}

# 危险字符过滤
FORBIDDEN_CHARS = ['|', '&', ';', '`', '$', '>', '<', '\n', '\r']
```

#### 2. 文件访问控制

```python
# 允许访问的路径前缀
ALLOWED_PATHS = [
    '/tmp/',
    '/var/log/',
    '/home/',
    '/etc/'  # 只读
]

# 禁止访问的路径
FORBIDDEN_PATHS = [
    '/etc/shadow',
    '/etc/passwd',
    '/root/',
    '/proc/',
    '/sys/'
]
```

#### 3. 认证机制（可选）

```json
// 请求头添加认证信息
{
    "X-Auth-Token": "sha256_hmac_signature",
    "X-Timestamp": "1703654400",
    "X-Nonce": "random_string"
}
```

#### 4. 请求限流

```python
# 限流配置
RATE_LIMIT = {
    'requests_per_second': 10,
    'requests_per_minute': 100,
    'max_concurrent': 5
}
```

---

## 配置说明

### QEMU虚拟机配置

在启动虚拟机时添加virtio-serial设备：

```bash
# 完整的QEMU启动参数示例
qemu-system-x86_64 \
    -name guest-vm \
    -m 4096 \
    -smp 4 \
    -drive file=/path/to/disk.qcow2,format=qcow2 \
    # virtio-serial设备配置
    -device virtio-serial-pci,id=virtio-serial0 \
    -chardev socket,id=channel0,path=/tmp/virtio-serial.sock,server=on,wait=off \
    -device virtserialport,chardev=channel0,name=channel.0 \
    # 其他配置...
```

### 配置参数说明

| 参数 | 说明 | 示例值 |
|------|------|--------|
| chardev socket path | UDS文件路径 | /var/lib/libvirt/qemu/channel/target/domain-XX-VMName/test.vserial.0 |
| server | QEMU作为服务端 | on |
| wait | 不等待客户端连接 | off |
| virtserialport name | Guest内设备名称 | test.vserial.0 |

### Host端配置文件

```yaml
# host_config.yaml
connection:
  socket_path: /var/lib/libvirt/qemu/channel/target/domain-26-UCypher-newtest1/test.vserial.0
  connect_timeout: 5
  read_timeout: 30
  write_timeout: 30

retry:
  max_retries: 3
  retry_interval: 1
  backoff_factor: 2

logging:
  level: INFO
  file: /var/log/virtio-rpc-host.log
  max_size: 10MB
  backup_count: 5
```

### Guest端配置文件

```yaml
# guest_config.yaml
device:
  path: /dev/virtio-ports/test.vserial.0
  buffer_size: 65536

server:
  max_request_size: 10MB
  request_timeout: 60

security:
  enable_auth: false
  allowed_commands: []  # 空表示使用默认白名单
  allowed_paths:
    - /tmp/
    - /var/log/
    - /home/

logging:
  level: INFO
  file: /var/log/virtio-rpc-guest.log
  max_size: 10MB
  backup_count: 5
```

---

## 实现步骤

### 开发语言

选择Python作为开发语言，利用其丰富的库和简洁的语法，实现快速开发和测试。

### 依赖库

```
# requirements.txt
pyyaml>=6.0        # 配置文件解析
psutil>=5.9        # 系统信息获取
```

### 项目结构

```
virtio-serial-rpc/
├── host/
│   ├── __init__.py
│   ├── client.py          # RPC客户端主类
│   ├── connection.py      # UDS连接管理
│   ├── protocol.py        # HTTP协议处理
│   └── config.yaml        # Host端配置
├── guest/
│   ├── __init__.py
│   ├── server.py          # RPC服务端主类
│   ├── device.py          # 字符设备读写
│   ├── router.py          # 请求路由
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── system.py      # 系统信息处理
│   │   ├── shell.py       # Shell命令处理
│   │   ├── file.py        # 文件操作处理
│   │   └── service.py     # 服务管理处理
│   └── config.yaml        # Guest端配置
├── common/
│   ├── __init__.py
│   ├── protocol.py        # 共用协议定义
│   ├── errors.py          # 错误码定义
│   └── utils.py           # 工具函数
├── tests/
│   ├── test_client.py
│   ├── test_server.py
│   └── test_protocol.py
├── ReadMe.md
└── requirements.txt
```

### 核心类设计

#### Host端 - RPC客户端

```python
class VirtioRPCClient:
    """Host端RPC客户端"""
    
    def __init__(self, socket_path: str, config: dict = None):
        """初始化客户端"""
        pass
    
    def connect(self) -> bool:
        """连接到virtio-serial套接字"""
        pass
    
    def disconnect(self):
        """断开连接"""
        pass
    
    def send_request(self, method: str, endpoint: str, 
                     body: dict = None, timeout: int = 30) -> dict:
        """发送HTTP请求并获取响应"""
        pass
    
    # 便捷方法
    def ping(self) -> dict:
        """心跳检测"""
        pass
    
    def get_system_info(self) -> dict:
        """获取系统信息"""
        pass
    
    def exec_command(self, command: str, timeout: int = 30) -> dict:
        """执行Shell命令"""
        pass
    
    def upload_file(self, local_path: str, remote_path: str) -> dict:
        """上传文件"""
        pass
    
    def download_file(self, remote_path: str, local_path: str) -> dict:
        """下载文件"""
        pass
```

#### Guest端 - RPC服务端

```python
class VirtioRPCServer:
    """Guest端RPC服务端"""
    
    def __init__(self, device_path: str, config: dict = None):
        """初始化服务端"""
        pass
    
    def register_handler(self, method: str, path: str, handler: callable):
        """注册请求处理器"""
        pass
    
    def start(self):
        """启动服务"""
        pass
    
    def stop(self):
        """停止服务"""
        pass
    
    def _handle_request(self, raw_request: bytes) -> bytes:
        """处理请求"""
        pass
```

---

## 使用示例

### Host端使用示例

```python
from host.client import VirtioRPCClient

# 创建客户端
client = VirtioRPCClient('/var/lib/libvirt/qemu/channel/target/domain-26-UCypher-newtest1/test.vserial.0')

# 连接
client.connect()

# 心跳检测
result = client.ping()
print(f"Ping result: {result}")

# 获取系统信息
info = client.get_system_info()
print(f"Guest OS: {info['data']['os']}")
print(f"Hostname: {info['data']['hostname']}")

# 执行命令
result = client.exec_command('df -h')
print(f"Disk usage:\n{result['data']['stdout']}")

# 上传文件
client.upload_file('/local/config.json', '/tmp/config.json')

# 下载文件
client.download_file('/var/log/syslog', '/local/syslog.txt')

# 断开连接
client.disconnect()
```

### Guest端使用示例

```python
from guest.server import VirtioRPCServer
from guest.handlers import system, shell, file

# 创建服务端
server = VirtioRPCServer('/dev/virtio-ports/test.vserial.0')

# 注册处理器（默认已注册标准处理器）
# server.register_handler('GET', '/api/v1/custom', custom_handler)

# 启动服务
server.start()
```

### 命令行工具使用

```bash
# Host端发送命令（注意socket路径较长，可以设置环境变量简化）
$ export VIRTIO_SOCKET=/var/lib/libvirt/qemu/channel/target/domain-26-UCypher-newtest1/test.vserial.0

$ python -m host.cli --socket $VIRTIO_SOCKET ping
{"code": 0, "message": "pong"}

$ python -m host.cli --socket $VIRTIO_SOCKET exec "ls -la /tmp"
total 48
drwxrwxrwt 12 root root 4096 ...

$ python -m host.cli --socket $VIRTIO_SOCKET upload local.txt /tmp/remote.txt
Upload successful: /tmp/remote.txt (1024 bytes)

# Guest端启动服务
$ python -m guest.server --config /etc/virtio-rpc/config.yaml
[INFO] VirtioRPC Server started on /dev/virtio-ports/test.vserial.0
```

---

## 部署指南

### 环境要求

| 组件 | 要求 |
|------|------|
| Host OS | Linux (支持KVM) |
| Guest OS | Linux (内核 >= 2.6.32) |
| Python | >= 3.8 |
| QEMU | >= 4.0 |

### Host端部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制配置文件
cp host/config.yaml /etc/virtio-rpc/host_config.yaml

# 3. 编辑配置
vim /etc/virtio-rpc/host_config.yaml

# 4. 测试连接
python -m host.cli --socket /tmp/virtio-serial.sock ping
```

### Guest端部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制配置文件
mkdir -p /etc/virtio-rpc
cp guest/config.yaml /etc/virtio-rpc/guest_config.yaml

# 3. 编辑配置
vim /etc/virtio-rpc/guest_config.yaml

# 4. 创建systemd服务
cat > /etc/systemd/system/virtio-rpc.service << EOF
[Unit]
Description=Virtio Serial RPC Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m guest.server --config /etc/virtio-rpc/guest_config.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 5. 启动服务
systemctl daemon-reload
systemctl enable virtio-rpc
systemctl start virtio-rpc

# 6. 检查状态
systemctl status virtio-rpc
```

### 验证部署

```bash
# 在Host上执行
export VIRTIO_SOCKET=/var/lib/libvirt/qemu/channel/target/domain-26-UCypher-newtest1/test.vserial.0

python -m host.cli --socket $VIRTIO_SOCKET ping
# 期望输出: {"code": 0, "message": "pong", ...}

python -m host.cli --socket $VIRTIO_SOCKET info
# 期望输出: 系统信息JSON
```

---

## 附录

### A. 完整错误码列表

见 [错误处理](#错误处理) 章节。

### B. 性能参考

| 操作类型 | 预期延迟 | 吞吐量 |
|---------|---------|--------|
| Ping | < 5ms | - |
| 系统信息查询 | < 10ms | - |
| 命令执行(简单) | < 50ms | - |
| 小文件传输(< 1KB) | < 20ms | - |
| 大文件传输 | - | ~10MB/s |

### C. 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| 连接失败 | 套接字不存在 | 检查QEMU启动参数 |
| 连接失败 | 权限不足 | 检查套接字文件权限 |
| 响应超时 | Guest服务未启动 | 启动Guest端服务 |
| 响应超时 | 网络问题 | 检查virtio-serial设备 |
| 命令执行失败 | 命令不在白名单 | 检查安全配置 |

### D. 版本历史

| 版本 | 日期 | 变更说明 |
|------|------|---------|
| 1.0.0 | 2024-01-01 | 初始版本 |

---

## 参考资料

1. [QEMU virtio-serial文档](https://www.qemu.org/docs/master/system/devices/virtio-serial.html)
2. [Linux virtio驱动](https://www.kernel.org/doc/html/latest/driver-api/virtio/index.html)
3. [HTTP/1.1 RFC 2616](https://tools.ietf.org/html/rfc2616)