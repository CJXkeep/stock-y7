# -*- coding: utf-8 -*-
"""钉钉 Stream 模式接入（dingtalk-stream，模仿 openclaw 类插件的直连方式）。

服务启动后若已配置 AppKey/AppSecret，则建立 Stream 长连接（WebSocket，无需公网 IP）：
- 机器人被拉进群后在群里 **@它** 时，消息报文里的 ``conversationId`` 即为目标群的
  ``openConversationId``——配置为空时**自动回填** ``data/notify.json``，其余情况仅记日志；
- 群聊/单聊均可识别（``conversationType``：``1``=单聊、``2``=群聊），自动回填只取群聊；
- 断线由 SDK 内部自动重连；任何失败只告警，绝不影响推送与主服务流程。
"""
from __future__ import annotations

import logging
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_LIBS_DIR = os.path.join(ROOT, "libs")
if os.path.isdir(_LIBS_DIR):
    sys.path.insert(0, _LIBS_DIR)

from server.notify_service import load_notify_config, save_notify_config

log = logging.getLogger("trend_app")

#: 机器人接收消息的 Stream 回调 topic（钉钉官方固定值）
CHATBOT_TOPIC = "/v1.0/im/bot/messages/get"
#: conversationType：群聊（自动回填只认群聊）
_CONV_TYPE_GROUP = "2"

_stream_started = [False]
_stream_lock = threading.Lock()


def _sdk():
    """延迟导入 dingtalk-stream SDK（便携依赖位于 libs/）。"""
    import dingtalk_stream
    return dingtalk_stream


def _extract_conversation(data: dict) -> dict:
    """从消息报文提取会话信息（纯函数）。"""
    data = data if isinstance(data, dict) else {}
    return {
        "conversation_id": str(data.get("conversationId") or "").strip(),
        "conversation_type": str(data.get("conversationType") or "").strip(),
        "sender_nick": str(data.get("senderNick") or "").strip(),
        "text": str((data.get("text") or {}).get("content", "")).strip(),
    }


def _maybe_fill_conversation_id(conversation_id: str, conversation_type: str) -> dict:
    """openConversationId 为空且收到群聊消息时自动回填配置。"""
    if not conversation_id:
        return {"filled": False, "reason": "报文缺少 conversationId"}
    cfg = load_notify_config()
    if cfg.get("open_conversation_id"):
        return {"filled": False, "reason": "已配置 openConversationId"}
    if conversation_type != _CONV_TYPE_GROUP:
        return {"filled": False, "reason": "非群聊消息（不回填）"}
    save_notify_config({**cfg, "open_conversation_id": conversation_id})
    return {"filled": True, "conversation_id": conversation_id}


def _handle_message_data(data: dict) -> dict:
    """处理一条机器人消息：记日志 + 按需回填；返回处理结果（供测试）。"""
    info = _extract_conversation(data)
    result = _maybe_fill_conversation_id(info["conversation_id"], info["conversation_type"])
    log.info("钉钉 Stream 收到消息：类型 %s · 来自 %s · conversationId=%s%s",
             info["conversation_type"] or "?", info["sender_nick"] or "?",
             info["conversation_id"] or "(空)",
             f"（已自动回填配置）" if result.get("filled") else
             (f"（{result.get('reason')}）" if info["conversation_id"] else ""))
    return {"info": info, **result}


def _run_stream(app_key: str, app_secret: str) -> None:
    """建立 Stream 长连接（阻塞，供守护线程调用）；永不向上抛异常。"""
    try:
        dingtalk_stream = _sdk()
        handler = _build_handler(dingtalk_stream)
        client = dingtalk_stream.DingTalkStreamClient(
            dingtalk_stream.Credential(app_key, app_secret))
        client.register_callback_handler(CHATBOT_TOPIC, handler)
        log.info("钉钉 Stream 模式已启动：把机器人拉进群后 @它 一下，"
                 "openConversationId 会自动获取并回填（无需手动复制）")
        client.start_forever()
    except Exception as exc:
        log.warning("钉钉 Stream 连接异常退出（不影响推送主流程，重启服务可重连）: %s", exc)
    finally:
        with _stream_lock:
            _stream_started[0] = False


def _build_handler(dingtalk_stream):
    """构造机器人消息 handler（SDK 要求 async process）。"""
    class _ChatbotHandler(dingtalk_stream.ChatbotHandler):
        async def process(self, callback: dingtalk_stream.CallbackMessage):
            try:
                _handle_message_data(callback.data if isinstance(callback.data, dict) else {})
            except Exception as exc:
                log.warning("钉钉 Stream 消息处理失败（不影响连接）: %s", exc)
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    return _ChatbotHandler()


def start_stream() -> bool:
    """启动 Stream 长连接线程（幂等）；已配置 AppKey/AppSecret 才启动。

    返回是否实际启动；配置变化后如需重连请重启服务。
    """
    with _stream_lock:
        if _stream_started[0]:
            return False
        cfg = load_notify_config()
        app_key = str(cfg.get("app_key") or "").strip()
        app_secret = str(cfg.get("app_secret") or "").strip()
        if not (app_key and app_secret):
            return False
        _stream_started[0] = True
    threading.Thread(target=_run_stream, args=(app_key, app_secret),
                     name="dingtalk-stream", daemon=True).start()
    return True
