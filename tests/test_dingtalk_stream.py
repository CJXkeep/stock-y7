# -*- coding: utf-8 -*-
"""钉钉 Stream 模式接入守护测试（离线：不建真实连接，注入临时配置目录）。

覆盖：消息报文提取（conversationId/类型/发送者/文本）、自动回填语义
（空配置+群聊=回填、已配置=跳过、单聊=跳过、缺 id=跳过）、start_stream
未配置凭证时不启动。
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import dingtalk_stream as ds
from server import notify_service as ns


class StreamMessageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dingtalk_stream_")
        self.orig_path = ns.notify_config_path
        ns.notify_config_path = lambda path=None: path or os.path.join(self.tmp, "notify.json")

    def tearDown(self):
        ns.notify_config_path = self.orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_conversation(self):
        info = ds._extract_conversation({
            "conversationId": "cidABC==", "conversationType": "2",
            "senderNick": "张三", "text": {"content": "你好"},
        })
        assert info == {"conversation_id": "cidABC==", "conversation_type": "2",
                        "sender_nick": "张三", "text": "你好"}
        assert ds._extract_conversation(None)["conversation_id"] == ""
        assert ds._extract_conversation({})["conversation_type"] == ""

    def test_group_message_autofills_empty_config(self):
        ns.save_notify_config({"app_key": "k", "app_secret": "s"}, self.tmp + "/notify.json")
        result = ds._handle_message_data({
            "conversationId": "cidGROUP==", "conversationType": "2",
            "senderNick": "张三", "text": {"content": "@机器人 你好"},
        })
        assert result["filled"] is True
        assert ns.load_notify_config()["open_conversation_id"] == "cidGROUP=="

    def test_existing_config_not_overwritten(self):
        ns.save_notify_config({"app_key": "k", "app_secret": "s",
                               "open_conversation_id": "cidOLD=="}, self.tmp + "/notify.json")
        result = ds._handle_message_data({"conversationId": "cidNEW==",
                                          "conversationType": "2"})
        assert result["filled"] is False and result["reason"] == "已配置 openConversationId"
        assert ns.load_notify_config()["open_conversation_id"] == "cidOLD=="

    def test_private_message_not_filled(self):
        ns.save_notify_config({"app_key": "k", "app_secret": "s"}, self.tmp + "/notify.json")
        result = ds._handle_message_data({"conversationId": "cidP2P==",
                                          "conversationType": "1"})
        assert result["filled"] is False and "非群聊" in result["reason"]
        assert ns.load_notify_config()["open_conversation_id"] == ""

    def test_missing_id_not_filled(self):
        result = ds._handle_message_data({"conversationType": "2"})
        assert result["filled"] is False and "缺少" in result["reason"]


class StartStreamTest(unittest.TestCase):
    def setUp(self):
        # 注入临时空配置目录：不依赖真实 data/notify.json 的「未配置凭证」巧合前提
        self.tmp = tempfile.mkdtemp(prefix="dingtalk_start_")
        self.orig_path = ns.notify_config_path
        ns.notify_config_path = lambda path=None: path or os.path.join(self.tmp, "notify.json")

    def tearDown(self):
        ns.notify_config_path = self.orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_credentials_does_not_start(self):
        # 空配置 + 未启动标志 → 不启动且不抛异常
        assert ds.start_stream() is False


if __name__ == "__main__":
    unittest.main(verbosity=1)
