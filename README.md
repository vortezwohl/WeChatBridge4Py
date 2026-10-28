# WeChatBridge4Py

**微信「合并转发」→ Python 回调** 的桥接 SDK。

以你自己的应用身份（显示名 + logo）注册分享入口后，在微信里多选消息 →
合并转发 → 转发到其他应用 → 选你的入口，聊天归档就会以**结构化消息流**
送进你的 Python 进程。本包**不预设任何目标应用**——发到哪里、怎么处理，
完全由你的回调决定。纯 SDK，无 CLI，一切操作从 `wechatbridge4py` 走。

```
微信多选消息 ──合并转发──► 系统分享面板选你的入口（你的名字 + 你的 logo）
        └─► wcb4py-share.exe（Share Target 激活，无 UI，秒退）
                └─► 原子落盘收件箱（%LOCALAPPDATA%\WeChatBridge4Py\Inbox）
                        └─► 命名事件唤醒 ──► Bridge ──► 你的 @bridge.on_share 回调
```

## 快速上手（Windows，Python ≥ 3.11）

```bash
uv add wechatbridge4py          # 或 pip install wechatbridge4py
```

```python
from wechatbridge4py import AppIdentity, Bridge

bridge = Bridge(
    app=AppIdentity(
        name="我的笔记助手",      # 微信分享面板里显示的名字（你的品牌）
        logo="assets/logo.png",  # 你的 logo（PNG，短边 ≥150，自动生成分享图标）
    ),
)

bridge.register()               # 注册分享入口（首选签名路线；可能弹 UAC）
bridge.doctor().print()         # 环境体检（结构化报告的人类可读输出）

@bridge.on_share
def on_share(batch):
    print(f"收到 {len(batch.items)} 个文件，{len(batch.all_messages)} 条消息")
    for message in batch.all_messages:
        print(f"[{message.time}] {message.sender}: {message.text}")
    # 需要 zip 本体/媒体文件时：batch.zips / batch.items[].path
    # 想转发给你自己的应用？回调里随便你——本 SDK 到此为止。

bridge.run_forever()            # 阻塞消费；Ctrl+C 退出
```

先不开微信也能调试：`bridge.inject("聊天记录.zip")` 把 zip 模拟成一次分享
（一次调用 = 一个批次，与真实分享走完全相同的分发路径）。

## SDK 一览（`wechatbridge4py` 的全部公开面）

| 名称 | 说明 |
| --- | --- |
| `Bridge(app, inbox=None, poll_interval=0.5)` | 唯一编排对象；`app=None` 为纯消费模式 |
| `bridge.on_share(handler)` | 注册回调（装饰器用法，可多个、按序调用） |
| `bridge.run_forever()` / `bridge.drain()` | 阻塞消费 / 一次性处理当前批次 |
| `bridge.register(*, allow_dev_mode_elevation=True)` | 以 `app` 身份注册分享入口 → `RegisterResult` |
| `bridge.unregister()` | 注销分享入口 |
| `bridge.doctor()` | 环境体检 → `DoctorReport`（`.ok` / `.print()`） |
| `bridge.inject(*zip_paths)` | 免微信调试注入 → 批次 id |
| `AppIdentity(name, logo)` | 应用身份；构造即校验（名字红线 + logo 形态） |
| `ShareBatch` / `Message` / `Transcript` / `SharedItem` | 回调拿到的数据模型 |
| `RegisterResult` / `DoctorReport` / `InboxPaths` | 结果与高级用法 |

`AppIdentity` 校验规则：名字非空、≤40 字符、**不得含连续 微信/WeChat/Weixin
子串**（微信宿主会隐藏这类目标，上游实测结论）；logo 为 8 位深非隔行
RGB/RGBA PNG、短边 ≥150（等比缩放 + 透明补边生成 44/150 两档图标，不变形）。

## 数据契约（收件箱协议）

```
%LOCALAPPDATA%\WeChatBridge4Py\Inbox\
├── Ready/<batch-id>/manifest.json + files/*.zip   ← 待处理批次（原子发布）
├── Done/<batch-id>/...                            ← 回调全部成功
├── Failed/<batch-id>/... + error.json             ← 回调抛异常或结构损坏
├── Staging/                                       ← 写入侧暂存（消费者忽略）
└── Logs/relay.log                                 ← 两侧共用日志
```

- **磁盘是唯一事实源**：入口进程只做「校验 → 暂存 → 一次目录改名原子发布 →
  发信号 → 退出」。你的接收进程没在运行也不丢数据，下次启动补扫。
- **至少一次交付**：回调抛异常的批次进 `Failed`（附 `error.json`）；进程在
  回调后、归档前崩溃会在下次启动重投——回调需容忍偶发重复。
- **回调返回后仍可读文件**：批次移入 Done/Failed 后 `items[].path` 自动重定位。
- manifest 为 camelCase JSON、ISO-8601 Z 时间戳（`schemaVersion: 1`），与
  [WeChatBridge](https://github.com/freestylefly/WeChatBridge) 项目同族协议思路。

## 隐私边界

- 只处理**用户主动在微信里分享出来的文件**，不读微信数据库、不注入、不碰微信进程；
- 入口进程无网络、无 UI，落地即走；数据只在本机收件箱与你的进程之间流转。

## 已知限制（务必阅读）

1. **微信的「转发到其他应用」按系统版本门控（真机实测，2026-10-28）**：
   微信 4.x 在 Windows 11 上正常显示分享目标；在 **Windows 10（19044）上
   不渲染该功能/列表**——注册再正确也看不见（系统分享面板可见、微信不可见）。
   上游微信流为此内置了闭源"兼容层"（见其 issue #62），本包未提供等价物；
   Windows 10 用户请升级系统或关注上游方案。
2. 注册有两条路线（自动选择）：**签名稀疏包**（首选，需 Windows SDK 的
   signtool/makeappx/makepri；自建自签证书并提权导入本机信任，首次会弹 UAC）；
   **开发者模式直注**（兜底，需开启开发者模式——未开启时 register 会弹 UAC
   自动开启，`allow_dev_mode_elevation=False` 可禁用；入口 exe 复制进
   `<venv>/wcb4py-share/`，升级本包后需重新 register）。
3. **改名字/换 logo 后需重新 `bridge.register()` 并彻底重启微信**（结束全部
   Weixin/WeChatAppEx 进程）——微信转发列表有缓存。排查可见性用
   「资源管理器选中 zip → 右键共享」对照。
4. 依赖微信 4.x 的「合并转发 → 转发到其他应用」能力；ZIP 内
   `聊天记录.txt` 的行式格式无官方文档，解析基于上游项目与真机样本逆向。

## 开发本仓库

```bash
uv sync          # 安装依赖（严格使用 uv 管理环境）
uv run pytest    # 全部测试
uv build         # 构建 wheel/sdist
```

模块结构：`core/`（模型 + 收件箱协议 + 归档/转录解析 + PNG 成像，零依赖、
跨平台，macOS 复用）＋ `identity.py`（应用身份契约）＋ `windows/`
（Share Target 激活、命名事件、稀疏包注册、结构化诊断）＋ `bridge.py`
（编排）。`wcb4py-share.exe`（gui_scripts 生成）是面向系统的激活二进制，
不是用户 CLI。

## 路线图

- [x] Windows：自定义品牌分享入口 → 收件箱 → 回调（本版本，SDK-only）
- [ ] macOS：Swift 极薄 Share Extension 落盘同一 Inbox 契约，Python 侧零改动
- [ ] 签名 MSIX 分发路线（摆脱自签证书依赖）

## 许可证

[MIT](LICENSE)
