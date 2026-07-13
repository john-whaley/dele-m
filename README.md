# dele-m Telegram Captcha Listener

这是一个基于 Telethon 的 Telegram 用户账号监听器。它会监听你账号可见的群消息或私聊消息，筛选指定机器人发出的验证消息，识别图片中的简单数学验证码，然后按配置等待一段时间并点击正确按钮。

## 项目结构

```text
telegram_bot/
  config.py          # 环境变量配置
  captcha_solver.py  # 验证码识别、按钮匹配和点击
  main.py            # 程序入口

generate_session.py        # 生成 TG_SESSION
docker-compose.example.yml # Docker Compose 示例
Dockerfile
tools/verify_samples.py    # 样本验证脚本
```

## 获取 TG_SESSION

先在本地或 VPS 上准备 `.env`：

```bash
cp .env.example .env
```

填入 Telegram API 信息：

```env
TELEGRAM_API_ID=你的_api_id
TELEGRAM_API_HASH=你的_api_hash
```

然后运行：

```bash
python generate_session.py
```

按提示登录 Telegram 后，终端会输出一长串 `TG_SESSION`。把它手动填入 VPS/Docker 的环境变量里。

## Docker 部署

复制示例：

```bash
cp docker-compose.example.yml docker-compose.yml
```

编辑 `docker-compose.yml`：

```yaml
TELEGRAM_API_ID: "你的_api_id"
TELEGRAM_API_HASH: "你的_api_hash"
TG_SESSION: "本地生成的_string_session"
CAPTCHA_BOT_IDS: "123456789"
CAPTCHA_CHATS: ""
CAPTCHA_CLICK_DELAY: "15"
CAPTCHA_OCR: "true"
```

启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

## 关键配置

- `CAPTCHA_ENABLED`: 是否自动处理验证码，默认 `true`
- `CAPTCHA_DEBUG`: 是否打印消息详情，默认 `false`
- `CAPTCHA_CHATS`: 只监听指定群或私聊，逗号分隔；留空表示监听账号可见的所有新消息
- `CAPTCHA_BOT_IDS`: 只处理指定机器人数字 ID，逗号分隔；留空表示不过滤发送者
- `CAPTCHA_KEYWORDS`: 触发关键词，逗号分隔
- `CAPTCHA_CLICK_DELAY`: 找到答案按钮后等待多久再点击，单位秒，默认 `15`
- `CAPTCHA_OCR`: 是否启用本地图片 OCR，默认 `false`
- `CAPTCHA_FALLBACK_GUESS`: 当 OCR/AI 算出的答案没有匹配到按钮时，是否按按钮数字规则兜底猜测，默认 `false`
- `CAPTCHA_FALLBACK_MIN_CONFIDENCE`: 兜底猜测最低可信度，默认 `0.7`
- `DOWNLOAD_DIR`: 媒体下载目录
- `STATS_INTERVAL`: 统计日志输出间隔，单位秒

图片验证码按固定格式 `a x b = ?` 处理，其中 `a` 和 `b` 是 0-9 个位数，`x` 是加减乘除。`.mp4` 动态验证码目前会跳过，不会自动点击。

## AI 识别兜底

可以把下载到本地的验证码图片发给视觉模型识别。接口按 OpenAI 兼容的 `chat/completions` 格式调用。

```yaml
CAPTCHA_OCR: "true"
CAPTCHA_AI_OCR: "true"
CAPTCHA_AI_API_KEY: "你的_api_token"
CAPTCHA_AI_BASE_URL: "https://api.openai.com/v1/chat/completions"
CAPTCHA_AI_MODEL: "gpt-4o-mini"
CAPTCHA_AI_PROMPT: "图片中的公式及结果是多少？"
CAPTCHA_AI_MODE: "fallback"
CAPTCHA_AI_TIMEOUT: "30"
```

`CAPTCHA_AI_MODE` 有两个值：

- `fallback`: 先用本地模板/OCR，失败后再调用 AI，默认推荐
- `always`: 图片下载后优先调用 AI

AI 返回 `3+6=?` 或 `18` 都可以。程序会继续用解析结果去匹配消息按钮，不会直接乱点。

如果 AI/OCR 的答案没有在按钮中找到，可以开启规则兜底：

```yaml
CAPTCHA_FALLBACK_GUESS: "true"
CAPTCHA_FALLBACK_MIN_CONFIDENCE: "0.7"
```

兜底会根据已识别出的 `a x b` 和按钮数字排除不可能答案，例如负数、过大的位数、减法/除法大于左操作数、加法/乘法小于操作数等；可信度低于阈值时会跳过。

`CAPTCHA_AI_PROMPT` 是发送图片时一并发送给 AI 的文字，默认是 `图片中的公式及结果是多少？`，你可以按自己的接口习惯改成更严格的提示词。

`CAPTCHA_AI_BASE_URL` 可以填完整地址，例如 `https://.../v1/chat/completions`；也可以只填到 `https://.../v1`，程序会自动补成 `/chat/completions`。

## 样本验证

把图片样本放到 `viwers/img`，推荐用 `axb=c` 命名，例如：

```text
9+6=15.jpg
4×5=20.jpg
0÷3=0.jpg
```

运行本地 OCR/模板验证：

```bash
python tools/verify_samples.py --root viwers
```

需要测试 AI 兜底时，先设置 `CAPTCHA_AI_API_KEY` 等环境变量，然后运行：

```bash
python tools/verify_samples.py --root viwers --ai
```

测试 `viwers/sure` 里的按钮兜底规则：

```bash
python tools/verify_samples.py --root viwers --fallback-guess
```

视频样本默认跳过；如需强制测试视频，添加 `--include-videos`。当前线上处理仍会跳过 `.mp4` 验证码。
