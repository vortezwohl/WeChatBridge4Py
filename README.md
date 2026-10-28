# WeChatBridge4Py

**微信「合并转发」→ Python 回调** 的桥接基础设施。

在微信里多选消息 → 合并转发 → 转发到其他应用 → 选「聊天转发桥」，聊天归档就会以
**结构化消息流**送进你的 Python 进程。本包**不预设任何目标应用**——不发 Codex/Claude，
不写 Obsidian；发到哪里、怎么处理，完全由你的回调决定。

```
微信多选消息 ──合并转发──► 系统分享面板「聊天转发桥」
        └─► wcb4py-share.exe（Share Target 激活，秒退）
                └─► 原子落盘收件箱（%LOCALAPPDATA%\WeChatBridge4Py\Inbox）
                        └─► 命名事件唤醒 ──► Bridge ──► 你的 @bridge.on_share 回调
```

## 快速上手（Windows，Python ≥ 3.11）

```bash
# 1. 安装（本项目仓库内即 `uv sync`）
uv add wechatbridge4py          # 或 pip install wechatbridge4py

# 2. 注册分享入口（需开启 Windows 开发者模式，见下）
wcb4py register

# 3. 写你的接收程序
```

```python
from wechatbridge4py import Bridge

bridge = Bridge()

@bridge.on_share
def on_share(batch):
    print(f"收到 {len(batch.items)} 个文件，{len(batch.all_messages)} 条消息")
    for message in batch.all_messages:
        print(f"[{message.time}] {message.sender}: {message.text}")
    # 需要 zip 本体/媒体文件时：batch.zips / batch.items[].path
    # 想转发给你自己的应用？回调里随便你——本库到此为止。

bridge.run_forever()   # 阻塞消费；Ctrl+C 退出
```

先不开微信也能调试：

```bash
wcb4py demo-share 某个聊天记录.zip   # 模拟一次分享注入收件箱
```

## 开发者模式（register 的前提）

未签名的分享入口注册需要 Windows 开发者模式。以管理员身份运行一次：

```bat
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f
```

或在「设置 → 隐私和安全性 → 开发者选项」打开。`wcb4py doctor` 可随时体检。

## CLI

| 命令 | 作用 |
| --- | --- |
| `wcb4py register` | 注册分享入口（幂等，重复执行安全） |
| `wcb4py unregister` | 注销分享入口 |
| `wcb4py doctor` | 环境体检 + 微信可见性排查指引 |
| `wcb4py demo-share <zip>...` | 把 zip 模拟成一次分享（无需微信） |

## 数据契约（收件箱协议）

```
%LOCALAPPDATA%\WeChatBridge4Py\Inbox\
├── Ready/<batch-id>/manifest.json + files/*.zip   ← 待处理批次（原子发布）
├── Done/<batch-id>/...                            ← 回调全部成功
├── Failed/<batch-id>/... + error.json             ← 回调抛异常或结构损坏
├── Staging/                                       ← 写入侧暂存（消费者忽略）
└── Logs/relay.log                                 ─— 两侧共用日志
```

- **磁盘是唯一事实源**：入口进程只做「校验 → 暂存 → 一次目录改名原子发布 → 发信号 → 退出」。
  你的接收进程没在运行也不丢数据，下次启动补扫。
- **至少一次交付**：回调抛异常的批次进 `Failed`（附 `error.json`）；进程在回调后、
  归档前崩溃会在下次启动重投——回调需容忍偶发重复。
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
   **开发者模式直注**（兜底，需开启开发者模式，入口 exe 复制进
   `<venv>/wcb4py-share/`，升级本包后需重跑 `wcb4py register`）。
3. 分享入口显示名刻意为「聊天转发桥」：微信会隐藏显示名含连续
   微信/WeChat/Weixin 子串的目标（上游项目实测结论，勿改）。
4. 微信转发列表有缓存：注册/注销后若菜单未变化，结束微信进程后重开；
   排查可见性用「资源管理器选中 zip → 右键共享」对照。
5. 依赖微信 4.x 的「合并转发 → 转发到其他应用」能力；ZIP 内
   `聊天记录.txt` 的行式格式无官方文档，解析基于上游项目与真机样本逆向。

## 开发本仓库

```bash
uv sync          # 安装依赖（严格使用 uv 管理环境）
uv run pytest    # 全部测试
uv build         # 构建 wheel/sdist
```

模块结构：`core/`（收件箱协议 + 归档/转录解析，零依赖、跨平台，macOS 复用）
＋ `win/`（Share Target 激活、命名事件、稀疏包注册）＋ `bridge.py`（开发者 API）。

## 路线图

- [x] Windows：分享入口注册 → 收件箱 → 回调（本版本）
- [ ] macOS：Swift 极薄 Share Extension 落盘同一 Inbox 契约，Python 侧零改动
- [ ] 签名 MSIX 分发路线（摆脱开发者模式依赖）

## 许可证

[MIT](LICENSE)
