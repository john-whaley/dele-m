# dele-m Telegram Captcha Listener

这是一个基于 Telethon 的 Telegram 用户账号监听器。它可以监听群消息或私聊消息，打印机器人发来的文本、按钮、媒体信息，并尝试识别简单数学验证码后点击正确按钮。

项目已整理成模块化结构：

```text
telegram_bot/
  config.py          # 环境变量配置
  captcha_solver.py  # 验证码识别、按钮匹配和点击
  main.py            # 程序入口

generate_session.py        # 本地生成 TG_SESSION
docker-compose.example.yml # Docker Compose 示例
Dockerfile
```

## 1. 安装依赖

```powershell
pip install -r requirements.txt
```

## 2. 获取 TG_SESSION

建议先在本地电脑生成 StringSession，再把它作为环境变量手动填到 VPS/Docker。这样 VPS 上不需要交互式输入手机号和验证码。

先复制配置文件并填入 Telegram API 信息：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```env
TELEGRAM_API_ID=你的_api_id
TELEGRAM_API_HASH=你的_api_hash
```

然后运行：

```powershell
python .\generate_session.py
```

按提示登录 Telegram，终端会输出一长串 session 字符串。把它填到 VPS 或 Docker 的环境变量里：

```env
TG_SESSION=这里粘贴生成出来的长字符串
```

## 3. 本地运行

```powershell
python -m telegram_bot.main
```

兼容入口也可以用：

```powershell
python .\telegram_debug_listener.py
```

## 4. Docker 部署

复制 compose 示例：

```bash
cp docker-compose.example.yml docker-compose.yml
```

编辑 `docker-compose.yml`，手动填入：

```yaml
TELEGRAM_API_ID: "你的_api_id"
TELEGRAM_API_HASH: "你的_api_hash"
TG_SESSION: "本地生成的_string_session"
CAPTCHA_BOT_IDS: "123456789"
CAPTCHA_CHATS: ""
CAPTCHA_CLICK_DELAY: "15"
```

启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

## 5. 关键环境变量

- `TELEGRAM_API_ID`: Telegram API ID，来自 <https://my.telegram.org>
- `TELEGRAM_API_HASH`: Telegram API Hash
- `TG_SESSION`: 推荐用于 Docker/VPS 的 Telethon StringSession
- `TELEGRAM_SESSION_NAME`: 本地文件 session 兜底，只有 `TG_SESSION` 为空时使用
- `CAPTCHA_ENABLED`: 是否自动处理验证码，默认 `true`
- `CAPTCHA_DEBUG`: 是否打印消息详情，默认 `true`
- `CAPTCHA_CHATS`: 只监听指定群或私聊，逗号分隔；留空表示监听账号可见的所有新消息
- `CAPTCHA_BOT_IDS`: 只处理指定机器人数字 ID，逗号分隔；留空表示不过滤发送者
- `CAPTCHA_KEYWORDS`: 触发关键词，逗号分隔
- `CAPTCHA_CLICK_DELAY`: 找到答案按钮后等待多久再点击，单位秒，默认 `15`
- `CAPTCHA_OCR`: 是否启用图片 OCR，默认 `false`
- `DOWNLOAD_DIR`: 媒体下载目录
- `STATS_INTERVAL`: 统计日志输出间隔，单位秒

## 6. 调试建议

第一次上线建议先这样配置，确认机器人实际发的消息、按钮和群 ID：

```env
CAPTCHA_ENABLED=false
CAPTCHA_DEBUG=true
CAPTCHA_CHATS=
CAPTCHA_BOT_IDS=
```

看到日志里的 `chat_id` 和 `sender_id` 后，再收紧配置：

```env
CAPTCHA_ENABLED=true
CAPTCHA_DEBUG=true
CAPTCHA_CHATS=-1001234567890
CAPTCHA_BOT_IDS=123456789
CAPTCHA_CLICK_DELAY=15
```

如果验证码题目在图片或动态视频里，再把 `CAPTCHA_OCR=true` 打开。图片验证码会按简单加减乘除算式处理，`.mp4` 等动态验证码会按 4 位字母数字码识别并匹配按钮文本。Dockerfile 已安装 `tesseract-ocr`，Python 依赖里也包含 `Pillow`、`pytesseract` 和 `opencv-python-headless`。修改 OCR 相关依赖后需要重新 `docker compose up -d --build`。
