"""测试飞书通知模块（notify/feishu.py）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSendFeishu:
    """send_feishu 基础功能测试。"""

    def test_no_webhook_url_returns_false(self) -> None:
        """未配置 webhook URL 时应返回 False 并打 warning 日志。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch.dict("os.environ", {}, clear=True):
            result = send_feishu("test content")
        assert result is False

    def test_webhook_url_from_env(self) -> None:
        """使用环境变量 FEISHU_WEBHOOK_URL 作为 webhook 地址。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu.requests.post") as mock_post,
            patch.dict(
                "os.environ",
                {"FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/test"},
            ),
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0, "StatusCode": 0}
            mock_post.return_value = mock_resp

            result = send_feishu("这是一条测试消息")
        assert result is True
        mock_post.assert_called_once()
        called_args = mock_post.call_args
        assert called_args[0][0] == "https://open.feishu.cn/open-apis/bot/v2/hook/test"

    def test_webhook_url_param_overrides_env(self) -> None:
        """显式传入的 webhook_url 应优先于环境变量。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu.requests.post") as mock_post,
            patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": "https://env.url"}),
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            send_feishu("msg", webhook_url="https://param.url")

        called_url = mock_post.call_args[0][0]
        assert called_url == "https://param.url"
        assert "env.url" not in called_url

    def test_api_error_raises_for_status(self) -> None:
        """API 返回非 2xx 时应抛出异常。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = Exception("HTTP 500")
            result = send_feishu("msg", webhook_url="https://open.feishu.cn/test")
        assert result is False

    def test_nonzero_code_returns_false(self) -> None:
        """API 返回非零 code 时应返回 False。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 1, "msg": "forbidden"}
            mock_post.return_value = mock_resp

            result = send_feishu("msg", webhook_url="https://open.feishu.cn/test")
        assert result is False


class TestSendSignedWebhook:
    """签名模式测试。"""

    def test_signed_mode_called_when_app_id_and_secret_provided(self) -> None:
        """同时提供 app_id 和 app_secret 时应使用签名模式。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._send_signed_webhook") as mock_signed,
            patch("trade_krono_cli.notify.feishu._send_webhook") as mock_plain,
        ):
            mock_signed.return_value = {"code": 0}

            send_feishu(
                "signed msg",
                webhook_url="https://open.feishu.cn/test",
                app_id="app123",
                app_secret="secret456",
            )

        mock_signed.assert_called_once()
        mock_plain.assert_not_called()

    def test_plain_mode_used_without_credentials(self) -> None:
        """未提供 app_id/app_secret 时应使用普通 webhook 模式。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with (
            patch("trade_krono_cli.notify.feishu._send_webhook") as mock_plain,
            patch("trade_krono_cli.notify.feishu._send_signed_webhook") as mock_signed,
        ):
            mock_plain.return_value = {"code": 0}

            send_feishu("plain msg", webhook_url="https://open.feishu.cn/test")

        mock_plain.assert_called_once()
        mock_signed.assert_not_called()


class TestGenSign:
    """签名生成函数测试。"""

    def test_gen_sign_output_format(self) -> None:
        """签名输出应为 Base64 字符串。"""
        import base64

        from trade_krono_cli.notify.feishu import _gen_sign

        sign = _gen_sign("1234567890", "my_secret")
        # 验证是有效的 base64 字符串
        decoded = base64.b64decode(sign)
        assert isinstance(decoded, bytes)
        assert len(decoded) == 32  # SHA-256 输出 32 字节

    def test_gen_sign_deterministic(self) -> None:
        """相同输入应产生相同签名。"""
        from trade_krono_cli.notify.feishu import _gen_sign

        s1 = _gen_sign("timestamp", "secret")
        s2 = _gen_sign("timestamp", "secret")
        assert s1 == s2

    def test_gen_sign_differs_on_different_secret(self) -> None:
        """不同 secret 应产生不同签名。"""
        from trade_krono_cli.notify.feishu import _gen_sign

        s1 = _gen_sign("timestamp", "secret_a")
        s2 = _gen_sign("timestamp", "secret_b")
        assert s1 != s2


class TestWebhookPayload:
    """Webhook payload 格式测试。"""

    def test_plain_webhook_payload_structure(self) -> None:
        """普通 webhook 的 payload 应包含正确的结构。"""
        from trade_krono_cli.notify.feishu import _send_webhook

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            _send_webhook("https://test.webhook", "Hello 世界")

            payload = mock_post.call_args[1]["json"]
            assert payload["msg_type"] == "post"
            assert "content" in payload
            assert "post" in payload["content"]
            assert "zh_cn" in payload["content"]["post"]
            title = payload["content"]["post"]["zh_cn"]["title"]
            assert title == "投研报告"

    def test_signed_webhook_payload_structure(self) -> None:
        """签名 webhook 的 payload 应包含 timestamp 和 sign 字段。"""
        from trade_krono_cli.notify.feishu import _send_signed_webhook

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            _send_signed_webhook("https://test.webhook", "msg", "app_id", "app_secret")

            payload = mock_post.call_args[1]["json"]
            assert "timestamp" in payload
            assert "sign" in payload
            assert payload["msg_type"] == "post"


class TestWebhookTimeout:
    """Webhook 请求超时测试。"""

    def test_request_has_timeout(self) -> None:
        """Webhook 请求应设置 timeout=15 秒。"""
        from trade_krono_cli.notify.feishu import _send_signed_webhook, _send_webhook

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            _send_webhook("https://test.webhook", "msg")
            _, kwargs = mock_post.call_args
            assert kwargs.get("timeout") == 15

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            _send_signed_webhook("https://test.webhook", "msg", "app_id", "app_secret")
            _, kwargs = mock_post.call_args
            assert kwargs.get("timeout") == 15


class TestEdgeCases:
    """边界情况测试。"""

    def test_empty_content(self) -> None:
        """空内容消息仍能正常发送。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            result = send_feishu("", webhook_url="https://test.webhook")
        assert result is True

    def test_multiline_content(self) -> None:
        """多行内容应原样传递。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            multiline = "股票A: BUY\n股票B: SELL\n股票C: HOLD"
            send_feishu(multiline, webhook_url="https://test.webhook")

            payload = mock_post.call_args[1]["json"]
            content = payload["content"]["post"]["zh_cn"]["content"][0][0]["text"]
            assert content == multiline

    def test_markdown_content(self) -> None:
        """Markdown 格式内容应被正确传递。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0}
            mock_post.return_value = mock_resp

            markdown = "**加粗** *斜体* `代码`"
            send_feishu(markdown, webhook_url="https://test.webhook")

            payload = mock_post.call_args[1]["json"]
            content = payload["content"]["post"]["zh_cn"]["content"][0][0]["text"]
            assert content == markdown

    def test_exception_in_send_webhook_returns_false(self) -> None:
        """_send_webhook 抛异常时 send_feishu 应返回 False。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu.requests.post") as mock_post:
            mock_post.side_effect = ConnectionError("network error")
            result = send_feishu("msg", webhook_url="https://test.webhook")
        assert result is False

    def test_exception_in_send_signed_webhook_returns_false(self) -> None:
        """_send_signed_webhook 抛异常时 send_feishu 应返回 False。"""
        from trade_krono_cli.notify.feishu import send_feishu

        with patch("trade_krono_cli.notify.feishu._send_signed_webhook") as mock_signed:
            mock_signed.side_effect = ConnectionError("timeout")
            result = send_feishu(
                "msg",
                webhook_url="https://test.webhook",
                app_id="app_id",
                app_secret="secret",
            )
        assert result is False
