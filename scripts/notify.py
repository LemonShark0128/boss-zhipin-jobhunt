"""定时结果推给用户本人（notify-01）。

系统 cron 跑 wrapper 时没有 Agent 在场：wrapper 的 stdout 只有 Hermes 这类会转发输出的调度器能推到
用户手里，Kimi Code、MiniMax Code、Claude Code、Codex 等都收不到。所以 wrapper 渲染完播报后调
`boss_hunt.py notify --stdin`，按 config.notify.channel 推到用户选的渠道。

凭据（webhook、key、token、SMTP 授权码）不写进 config.json，只从环境变量 / notify.env_file /
$BOSS_HUNT_ENV_FILE / ~/.boss-hunt/.env 读（与模型 key 同一套查找顺序）；任何报错都不回显凭据。
接口格式的核实情况见 docs/configuration.md「推送」。
"""
import base64
import hashlib
import hmac
import json
import os
import re
import smtplib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText

import greet as G

# env：必填凭据；optional：选填（签名密钥）。label 给人看。
CHANNELS = {
    "none": {"label": "不由脚本推送", "env": []},
    "serverchan": {"label": "Server酱（微信）", "env": ["BOSS_NOTIFY_SERVERCHAN_KEY"]},
    "pushplus": {"label": "PushPlus（微信）", "env": ["BOSS_NOTIFY_PUSHPLUS_TOKEN"]},
    "wecom": {"label": "企业微信群机器人", "env": ["BOSS_NOTIFY_WECOM_WEBHOOK"]},
    "feishu": {"label": "飞书群机器人", "env": ["BOSS_NOTIFY_FEISHU_WEBHOOK"],
               "optional": ["BOSS_NOTIFY_FEISHU_SECRET"]},
    "dingtalk": {"label": "钉钉群机器人", "env": ["BOSS_NOTIFY_DINGTALK_WEBHOOK"],
                 "optional": ["BOSS_NOTIFY_DINGTALK_SECRET"]},
    "bark": {"label": "Bark（iPhone 通知）", "env": ["BOSS_NOTIFY_BARK_KEY"]},
    "ntfy": {"label": "ntfy（手机通知）", "env": ["BOSS_NOTIFY_NTFY_TOPIC"]},
    "telegram": {"label": "Telegram 机器人", "env": ["BOSS_NOTIFY_TELEGRAM_TOKEN", "BOSS_NOTIFY_TELEGRAM_CHAT_ID"]},
    "email": {"label": "邮件（SMTP）", "env": ["BOSS_NOTIFY_SMTP_PASSWORD"]},
    "command": {"label": "自定义命令", "env": []},
}
WEBHOOK_ENV = {"wecom": "BOSS_NOTIFY_WECOM_WEBHOOK", "feishu": "BOSS_NOTIFY_FEISHU_WEBHOOK",
               "dingtalk": "BOSS_NOTIFY_DINGTALK_WEBHOOK"}
SOURCES = {"tick": "投递跳", "chat": "对话跳", "inbox": "回消息跳", "test": "推送测试", "manual": "通知"}
CUT = "\n…（已截断，完整内容见 runs.log）"
TEST_TEXT = "这是一条推送测试：以后定时任务（投递、对话、回消息）的结果会这样发给你。收到了请回到 Agent 里说一声。"


class NotifyError(Exception):
    """推送失败；消息里不含凭据。"""


def options(cfg):
    value = cfg.get("notify")
    return value if isinstance(value, dict) else {}


def channel_name(cfg):
    return str(options(cfg).get("channel") or "none").strip().lower()


def label(cfg):
    spec = CHANNELS.get(channel_name(cfg))
    return spec["label"] if spec else channel_name(cfg)


def title_for(source):
    return "BOSS求职助手｜%s" % SOURCES.get(source, source or "通知")


def _llm(cfg):
    llm = (((cfg.get("user") or {}).get("greeting") or {}).get("llm")) if isinstance(cfg.get("user"), dict) else None
    return llm if isinstance(llm, dict) else {}


def secret(cfg, name):
    env_file = options(cfg).get("env_file") or _llm(cfg).get("env_file")
    return G._env_key(name, {"env_file": env_file})


def problems(cfg):
    """配置问题（人话）。validate 当警告报，--check 当失败报；不联网。"""
    raw = cfg.get("notify")
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ["notify 必须是对象（例：{\"channel\": \"none\"}）"]
    name = channel_name(cfg)
    if name not in CHANNELS:
        return ["notify.channel=%r 不认识：只能是 %s" % (raw.get("channel"), " / ".join(CHANNELS))]
    out = []
    for env in CHANNELS[name]["env"]:
        if not secret(cfg, env):
            out.append("没找到推送凭据 %s（环境变量、notify.env_file / $BOSS_HUNT_ENV_FILE、~/.boss-hunt/.env、"
                       "~/.hermes/.env 都没有）" % env)
    hook = WEBHOOK_ENV.get(name)
    if hook and secret(cfg, hook) and not secret(cfg, hook).startswith("https://"):
        out.append("%s 必须是完整的 https:// webhook 地址" % hook)
    for key in ("server",):
        if raw.get(key) is not None and not re.match(r"https?://", str(raw.get(key))):
            out.append("notify.%s 必须以 http:// 或 https:// 开头" % key)
    if name == "email":
        email = raw.get("email") if isinstance(raw.get("email"), dict) else {}
        missing = [k for k in ("smtp_host", "user", "to") if not email.get(k)]
        if missing:
            out.append("notify.email 缺 %s" % " / ".join(missing))
        if email.get("to") and not recipients(email.get("to")):  # iter-03
            out.append("notify.email.to 必须是邮箱字符串（多个用逗号分隔）或字符串列表")
        if email.get("smtp_port") is not None and smtp_port(email) is None:
            out.append("notify.email.smtp_port 必须是 1–65535 的整数（常用 465 SSL / 587 STARTTLS）")
    if name == "command":
        cmd = raw.get("command")
        if not (isinstance(cmd, list) and cmd and all(isinstance(x, str) and x for x in cmd)):
            out.append("notify.command 必须是非空的字符串列表（argv，不经过 shell），例：[\"/usr/bin/mail\", \"-s\", \"BOSS\", \"me@x.com\"]")
    return out


def check(cfg):
    """(ok, label, problems)。channel=none 视为 ok（只是不推）。"""
    found = problems(cfg)
    return not found, label(cfg), found


def check_message(cfg):
    ok, name, found = check(cfg)
    if found:
        return "推送：%s 配置不全，定时结果推不出去（%s）" % (name, "；".join(found))
    if channel_name(cfg) == "none":
        return ("推送：未配置脚本推送（notify.channel=none），定时结果只输出到 stdout 和 runs.log。"
                "用 Hermes 等会转发 stdout 的调度器时不用配；否则你收不到，请配置推送渠道")
    return "推送：%s，凭据就绪；真跳结束后会把播报推给你" % name


# ---------------------------------------------------------------------------
# 发送
# ---------------------------------------------------------------------------
def _http(url, body=None, headers=None, timeout=10):
    if not url.startswith(("https://", "http://")):  # 只发 http(s)，挡 file:// 之类
        raise NotifyError("推送地址必须是 http(s)")
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - 上一行已限定 http(s)
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def cap_bytes(text, limit):
    if len(text.encode("utf-8")) <= limit:
        return text
    room = limit - len(CUT.encode("utf-8"))
    return text.encode("utf-8")[:room].decode("utf-8", "ignore") + CUT


def cap_chars(text, limit):
    return text if len(text) <= limit else text[:limit - len(CUT)] + CUT


def _short(value):
    """服务商报错只留第一小句，去掉 IP、hint 之类。"""
    return re.split(r"[,，;；]", str(value or ""), maxsplit=1)[0][:60]


def _post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _call(url, data, {"Content-Type": "application/json"})


def _call(url, data, headers):
    try:
        return _http(url, data, headers)
    except NotifyError:
        raise
    except Exception as exc:  # 网络错误：只报类型，不带 URL（URL 里有凭据）
        raise NotifyError("网络错误（%s）" % type(exc).__name__) from None


def _judge(status, text, field, ok_values, msg_fields=("errmsg", "msg", "message", "description")):
    if not 200 <= int(status) < 300:
        raise NotifyError("HTTP %s" % status)
    if field is None:
        return
    try:
        data = json.loads(text)
    except ValueError:
        raise NotifyError("服务商返回的不是 JSON（HTTP %s）" % status) from None
    value = data.get(field) if isinstance(data, dict) else None
    if value not in ok_values:
        msg = next((data.get(k) for k in msg_fields if isinstance(data, dict) and data.get(k)), "")
        raise NotifyError("%s=%s %s" % (field, value, _short(msg)))


def _with_title(title, text):
    return title + "\n" + text


def _serverchan(cfg, title, text):
    key = secret(cfg, "BOSS_NOTIFY_SERVERCHAN_KEY")
    m = re.match(r"^sctp(\d+)t", key)
    url = ("https://%s.push.ft07.com/send/%s.send" % (m.group(1), key) if m
           else "https://sctapi.ftqq.com/%s.send" % key)
    _judge(*_post_json(url, {"title": title, "desp": text}), "code", (0,))


def _pushplus(cfg, title, text):
    token = secret(cfg, "BOSS_NOTIFY_PUSHPLUS_TOKEN")
    payload = {"token": token, "title": title, "content": text, "template": "txt"}
    _judge(*_post_json("https://www.pushplus.plus/send", payload), "code", (200,))


def _wecom(cfg, title, text):
    url = secret(cfg, "BOSS_NOTIFY_WECOM_WEBHOOK")
    content = cap_bytes(_with_title(title, text), 2048)  # 官方：content 最长 2048 字节
    _judge(*_post_json(url, {"msgtype": "text", "text": {"content": content}}), "errcode", (0,))


def _feishu(cfg, title, text):
    url = secret(cfg, "BOSS_NOTIFY_FEISHU_WEBHOOK")
    payload = {"msg_type": "text", "content": {"text": cap_bytes(_with_title(title, text), 18000)}}
    sec = secret(cfg, "BOSS_NOTIFY_FEISHU_SECRET")
    if sec:  # 官方：sign = Base64(HmacSHA256(key=timestamp+"\n"+secret, msg=""))
        ts = str(int(time.time()))
        digest = hmac.new(("%s\n%s" % (ts, sec)).encode("utf-8"), b"", hashlib.sha256).digest()
        payload.update(timestamp=ts, sign=base64.b64encode(digest).decode("ascii"))
    _judge(*_post_json(url, payload), "code", (0,))


def _dingtalk(cfg, title, text):
    url = secret(cfg, "BOSS_NOTIFY_DINGTALK_WEBHOOK")
    sec = secret(cfg, "BOSS_NOTIFY_DINGTALK_SECRET")
    if sec:  # 官方加签：Base64(HmacSHA256(key=secret, msg=毫秒时间戳+"\n"+secret))，再 urlEncode 拼进 URL
        ts = str(int(time.time() * 1000))
        digest = hmac.new(sec.encode("utf-8"), ("%s\n%s" % (ts, sec)).encode("utf-8"), hashlib.sha256).digest()
        url += "&timestamp=%s&sign=%s" % (ts, urllib.parse.quote_plus(base64.b64encode(digest).decode("ascii")))
    content = cap_bytes(_with_title(title, text), 18000)  # 官方未写 text 长度上限，自截留余量
    _judge(*_post_json(url, {"msgtype": "text", "text": {"content": content}}), "errcode", (0,))


def _bark(cfg, title, text):
    key = secret(cfg, "BOSS_NOTIFY_BARK_KEY")
    server = str(options(cfg).get("server") or "https://api.day.app").rstrip("/")
    data = urllib.parse.urlencode({"title": title, "body": text}).encode("ascii")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    _judge(*_call("%s/%s" % (server, urllib.parse.quote(key, safe="")), data, headers), "code", (200,))


def _ntfy(cfg, title, text):
    topic = secret(cfg, "BOSS_NOTIFY_NTFY_TOPIC")
    server = str(options(cfg).get("server") or "https://ntfy.sh").rstrip("/")
    encoded = "=?UTF-8?B?%s?=" % base64.b64encode(title.encode("utf-8")).decode("ascii")  # 官方：非 ASCII 用 RFC 2047
    body = cap_bytes(text, 4096).encode("utf-8")  # 官方：超过 4096 字节会变成附件
    headers = {"Title": encoded, "Content-Type": "text/plain; charset=utf-8"}
    _judge(*_call("%s/%s" % (server, urllib.parse.quote(topic, safe="")), body, headers), None, ())


def _telegram(cfg, title, text):
    token = secret(cfg, "BOSS_NOTIFY_TELEGRAM_TOKEN")
    chat = secret(cfg, "BOSS_NOTIFY_TELEGRAM_CHAT_ID")
    payload = {"chat_id": chat, "text": cap_chars(_with_title(title, text), 4096)}
    _judge(*_post_json("https://api.telegram.org/bot%s/sendMessage" % token, payload), "ok", (True,))


def recipients(value):
    """iter-03：收件人列表。字符串按逗号 / 分号（含全角）拆开；形状不对返回 []（problems 据此报错）。"""
    if isinstance(value, str):
        parts = re.split(r"[,;，；]", value)
    elif isinstance(value, list) and all(isinstance(x, str) for x in value):
        parts = value
    else:
        return []
    return [x.strip() for x in parts if x.strip()]


def smtp_port(conf):
    """iter-03：端口没写 = 465；写了必须是 1–65535 的整数（"587" 这种数字字符串也认），否则 None。"""
    raw = conf.get("smtp_port")
    if raw is None or raw == "":
        return 465
    if isinstance(raw, bool) or isinstance(raw, float):
        return None
    try:
        port = int(str(raw).strip())
    except ValueError:
        return None
    return port if 0 < port < 65536 else None


def _email(cfg, title, text):
    conf = options(cfg).get("email") or {}
    to = recipients(conf["to"])
    sender = conf.get("from") or conf["user"]
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    port = smtp_port(conf)
    server = None
    try:
        if conf.get("starttls") or port == 587:
            server = smtplib.SMTP(conf["smtp_host"], port, timeout=20)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(conf["smtp_host"], port, timeout=20)
        server.login(conf["user"], secret(cfg, "BOSS_NOTIFY_SMTP_PASSWORD"))
        server.sendmail(sender, to, msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError("邮件发送失败（%s）" % type(exc).__name__) from None
    finally:
        if server is not None:  # iter-03：失败时也关连接；关的时候再出错不影响结果
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


def _command(cfg, title, text):
    env = dict(os.environ, BOSS_NOTIFY_TITLE=title)
    try:
        p = subprocess.run(options(cfg)["command"], check=False, input=text, env=env, text=True,
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotifyError("命令没跑起来（%s）" % type(exc).__name__) from None
    if p.returncode != 0:
        raise NotifyError("命令退出码 %d" % p.returncode)


SENDERS = {"serverchan": _serverchan, "pushplus": _pushplus, "wecom": _wecom, "feishu": _feishu,
           "dingtalk": _dingtalk, "bark": _bark, "ntfy": _ntfy, "telegram": _telegram,
           "email": _email, "command": _command}


def send(cfg, text, source):
    """推一段播报。返回 {ok, sent, channel, label, error}；channel=none 或正文为空时不推、ok=True。"""
    name = channel_name(cfg)
    result = {"ok": True, "sent": False, "channel": name, "label": label(cfg), "error": None}
    text = (text or "").strip()
    if name == "none" or not text:
        return result
    found = problems(cfg)
    if found:
        result.update(ok=False, error="；".join(found))
        return result
    try:
        SENDERS[name](cfg, title_for(source), text)
    except NotifyError as exc:
        result.update(ok=False, error=str(exc))
        return result
    result["sent"] = True
    return result
