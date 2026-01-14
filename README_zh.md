# Grandstream 家庭集成

[English](README.md) | [简体中文](README_zh.md)

一个功能丰富的 Home Assistant 自定义集成，提供对 Grandstream 设备的支持，包括 GDS 系列门禁设备和
GNS 系列 NAS 设备。此集成使您能够直接通过 Home Assistant 本地控制和监控您的 Grandstream 设备。

## 主要功能

### 设备支持

- **GDS372X**: 实时状态监控、通话状态检测、设备控制
- **GNS5004E/GNS5004R**: 存储监控、系统性能监控、温度检测、设备控制

### 监控指标

- **GDS 设备**: 电话状态、可用账号、通话状态、振铃状态
- **GNS 设备**: CPU使用率、内存使用率、存储池状态、磁盘健康状态、网络流量、温度监控

### 设备控制

- **重启设备**: 支持GDS和GNS设备重启
- **电源管理**: GNS设备睡眠、唤醒、关机功能
- **摄像头支持**: GDS设备RTSP流媒体和快照功能

### 集成特性

- **实时更新**: 本地推送通知，即时设备状态变化
- **摄像头集成**: 支持兼容设备的 RTSP 流
- **设备操作**: 控制设备、自定义设备自动化和服务
- **自动发现**: 本地网络自动设备检测 (Zeroconf)

## 安装方法

### 方法一：HACS 安装（推荐）

1. 如果尚未安装 [HACS](https://hacs.xyz/)，请先安装
2. 在 Home Assistant 中，转到 HACS → 集成
3. 点击 "+" 按钮并搜索 "Grandstream Home"
4. 点击 "下载" 并按照提示操作
5. 重启 Home Assistant
6. 在 Home Assistant 中，转到 设置 → 设备与服务 → 添加集成
7. 搜索 "Grandstream Home" 并按照设置向导操作

### 方法二：脚本安装（推荐）

项目提供了两种安装脚本，支持自动检测和安装：

#### 使用完整安装脚本（install.sh）

```bash
# 自动检测 Home Assistant 配置目录
./install.sh

# 或指定配置目录
HA_CONFIG_DIR=/config ./install.sh

# 卸载集成
./install.sh --uninstall

# 查看帮助
./install.sh --help
```

#### 使用简化安装脚本（install-simple.sh）

```bash
# 需要手动指定 Home Assistant 配置目录
./install-simple.sh /config

# 或使用其他配置路径
./install-simple.sh ~/.homeassistant
```

**安装脚本特性：**

- 自动检测 Home Assistant 配置目录
- 备份现有安装
- 权限设置
- 安装验证
- 支持卸载功能

### 方法三：手动安装

1. 从 [发布页面](https://github.com/GrandstreamEngineering/grandstream_home/releases) 下载最新版本
2. 解压缩 zip 文件
3. 将 `grandstream_home` 文件夹复制到您的 `config/custom_components` 目录
4. 重启 Home Assistant
5. 在 Home Assistant 中，转到 设置 → 设备与服务 → 添加集成
6. 搜索 "Grandstream 家庭集成" 并按照设置向导操作

## 配置方法

### GDS 设备配置

1. 确保您的 GDS 设备与 Home Assistant 连接到同一网络
2. 在 GDS 设备上创建具有管理员权限的本地用户帐户
3. 默认用户名：`gdsha`
4. 设置过程中，提供：
   - 设备 IP 地址
   - 密码
   - 可选: 自定义端口
   - 可选: RTSP 流媒体配置 (摄像头功能)

### GNS NAS 配置

1. 确保您的 GNS 与 Home Assistant 连接到同一网络
2. 在 NAS Web 界面中启用本地 API 访问
3. 设置过程中，提供：
   - 设备 IP 地址
   - 用户名
   - 密码
   - 可选: 自定义端口

### 自动发现 (Zeroconf)

集成支持自动发现功能：

- 自动识别 GDS372X 和 GNS 设备
- 自动配置设备名称和端口

### 手动配置方式

如果自动发现无法正常工作，可以手动配置设备：

1. 在 Home Assistant 中，转到 设置 → 设备与服务 → 添加集成
2. 搜索 "Grandstream 家庭集成"
3. 在配置界面选择 "手动配置"
4. 输入以下信息：
   - **设备类型**: 选择 GDS 或 GNS
   - **设备 IP 地址**: 设备在局域网中的 IP 地址
5. 点击 "提交" 进行下一步配置

## 实体和传感器

### GDS 设备传感器

- **电话状态**: 显示设备当前状态
  - `unknown` - 未知
  - `available` - 有可用账号
  - `unavailable` - 无可用账号
  - `busy` - 通话中
  - `preview` - 通话预览
  - `ringing` - 振铃

### GNS 传感器

- **CPU 使用率**: 实时 CPU 使用率百分比
- **内存使用率**: 内存使用率百分比和总容量
- **存储池状态**: 存储池健康状态和使用率
- **磁盘健康**: 磁盘温度、健康状态和容量
- **网络流量**: 实时网络接收/发送速率
- **温度监控**: CPU 温度和系统温度
- **风扇状态**: 风扇运行状态和模式

## 按钮控制

### GDS 设备按钮

- **重启设备**: 重启 GDS 设备

### GNS 设备按钮

- **重启设备**: 重启 GNS
- **睡眠设备**: 使 GNS 进入睡眠状态
- **唤醒设备**: 从睡眠状态唤醒 GNS
- **关闭设备**: 安全关闭 GNS

## 摄像头支持

### GDS 摄像头功能

- **RTSP 流媒体**
- **快照功能**

## 服务

该集成提供以下服务：

### Grandstream 家庭服务

- `grandstream_home.reboot_device`: 重启 Grandstream 设备
- `grandstream_home.sleep_device`: 使 GNS 设备进入睡眠状态
- `grandstream_home.wake_device`: 唤醒睡眠中的 GNS 设备
- `grandstream_home.shutdown_device`: 关闭 GNS 设备

### 摄像头功能（适用于GDS设备）

集成提供基于FFmpeg的RTSP流媒体支持，主要用于图像抓取和流媒体显示

## 故障排除

### 设备未找到

- 确保设备与 Home Assistant 在同一网络上
- 检查设备的本地 API 是否已启用
- 验证防火墙设置允许 Home Assistant 与设备之间的通信
- 尝试使用手动 IP 配置而不是发现

### 连接错误

- 验证正确的用户名和密码
- 检查设备上配置的自定义端口
- 开启调试模式查看设备日志以获取更具体的错误信息

## 更新日志

请参阅 [CHANGELOG.md](CHANGELOG.md) 了解详细的变更历史。

## 许可证

请参阅 [LICENSE](LICENSE) 文件了解详情。
