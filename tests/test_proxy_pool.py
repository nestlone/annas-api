import unittest
from unittest.mock import patch

from annas_api import proxy_pool
from annas_api.proxy_pool import ProxyPool, get_pool


class FakeProviderResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class ProxyPoolTests(unittest.TestCase):
    URL = "https://api.example.com/ip/get?appKey=k&appSecret=s&wt=text&method=http"

    def test_fetch_returns_http_proxy(self):
        pool = ProxyPool(self.URL)
        with patch.object(proxy_pool.requests, "get", return_value=FakeProviderResponse("1.2.3.4:8080\n")):
            self.assertEqual(pool.acquire(), "http://1.2.3.4:8080")

    def test_each_acquire_returns_a_fresh_proxy(self):
        pool = ProxyPool(self.URL)
        responses = [FakeProviderResponse("1.2.3.4:8080"), FakeProviderResponse("5.6.7.8:9090")]
        with patch.object(proxy_pool.requests, "get", side_effect=responses) as get:
            self.assertEqual(pool.acquire(), "http://1.2.3.4:8080")
            self.assertEqual(pool.rotate(), "http://5.6.7.8:9090")
            self.assertEqual(get.call_count, 2)

    def test_scheme_override(self):
        pool = ProxyPool(self.URL, scheme="socks5")
        with patch.object(proxy_pool.requests, "get", return_value=FakeProviderResponse("1.2.3.4:1080")):
            self.assertEqual(pool.acquire(), "socks5://1.2.3.4:1080")

    def test_bad_payload_raises(self):
        pool = ProxyPool(self.URL)
        with patch.object(proxy_pool.requests, "get", return_value=FakeProviderResponse("no-port-here")):
            with self.assertRaises(RuntimeError):
                pool.acquire()

    def test_provider_error_payload_is_rejected(self):
        # xiaoxiangdaili answers {"code":1014,...} when the account runs out of
        # traffic; that JSON must not be mistaken for an ip:port endpoint.
        payload = '{"code":1014,"success":false,"data":null,"msg":"应用剩余流量不足"}'
        pool = ProxyPool(self.URL)
        with patch.object(proxy_pool.requests, "get", return_value=FakeProviderResponse(payload)):
            with self.assertRaises(RuntimeError) as caught:
                pool.acquire()
        self.assertIn("应用剩余流量不足", str(caught.exception))

    def test_get_pool_disabled_without_url(self):
        self.assertIsNone(get_pool({"proxy_pool_url": ""}))
        self.assertIsNone(get_pool({}))

    def test_get_pool_reuses_instance_for_same_url(self):
        config = {"proxy_pool_url": self.URL}
        self.assertIs(get_pool(config), get_pool(config))


if __name__ == "__main__":
    unittest.main()
