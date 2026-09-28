import unittest
from unittest.mock import patch

from annas_api import engine


class FakePool:
    def acquire(self, rotate=False):
        return "http://9.9.9.9:8000"

    def rotate(self):
        return "http://9.9.9.9:8001"


class ProxyRoutingTests(unittest.TestCase):
    def test_configured_host_and_subdomain_bypass_proxy(self):
        with patch.dict(engine.CONFIG, {"proxy_bypass_hosts": ["annas-archive.gl"]}, clear=False):
            self.assertTrue(engine.should_bypass_proxy("https://annas-archive.gl/search"))
            self.assertTrue(engine.should_bypass_proxy("https://zh.annas-archive.gl/search"))
            self.assertFalse(engine.should_bypass_proxy("https://annas-archive.example/search"))

    def test_bypassed_host_disables_environment_proxy(self):
        with patch.dict(engine.CONFIG, {"proxy_bypass_hosts": ["annas-archive.gl"]}, clear=False):
            with patch.object(engine, "get_pool", return_value=None), patch.object(
                engine, "detect_proxy", return_value="http://127.0.0.1:7890"
            ):
                self.assertEqual(
                    engine.request_proxies("https://annas-archive.gl/search"),
                    {"http": None, "https": None},
                )
                self.assertEqual(
                    engine.request_proxies("https://example.com/file"),
                    {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
                )

    def test_pool_takes_precedence_over_bypass(self):
        with patch.dict(engine.CONFIG, {"proxy_bypass_hosts": ["annas-archive.gl"]}, clear=False):
            with patch.object(engine, "get_pool", return_value=FakePool()):
                self.assertEqual(
                    engine.resolve_proxy("https://annas-archive.gl/search"),
                    "http://9.9.9.9:8000",
                )
                self.assertEqual(
                    engine.request_proxies("https://cdn.example.com/book.epub"),
                    {"http": "http://9.9.9.9:8000", "https": "http://9.9.9.9:8000"},
                )

    def test_pool_failure_falls_back_to_direct(self):
        class DeadPool:
            def acquire(self, rotate=False):
                raise RuntimeError("provider down")

        with patch.dict(engine.CONFIG, {"proxy_bypass_hosts": ["annas-archive.gl"]}, clear=False):
            with patch.object(engine, "get_pool", return_value=DeadPool()):
                self.assertIsNone(engine.resolve_proxy("https://annas-archive.gl/search"))

    def test_browser_ignores_pool_by_default(self):
        with patch.object(engine, "get_pool", return_value=FakePool()), patch.dict(
            engine.CONFIG, {"proxy_bypass_hosts": ["annas-archive.gl"], "proxy_pool_browser": False}, clear=False
        ):
            self.assertIsNone(engine.resolve_proxy("https://annas-archive.gl/search", browser=True))

    def test_browser_uses_pool_when_enabled(self):
        with patch.object(engine, "get_pool", return_value=FakePool()), patch.dict(
            engine.CONFIG, {"proxy_pool_browser": True}, clear=False
        ):
            self.assertEqual(
                engine.resolve_proxy("https://annas-archive.gl/search", browser=True),
                "http://9.9.9.9:8000",
            )

    def test_download_book_sends_browser_direct_and_download_via_pool(self):
        seen = {}

        class Pool:
            def acquire(self):
                return "http://1.2.3.4:1111"

            def rotate(self):
                return "http://1.2.3.4:2222"

        def fake_resolve(md5, quiet=False, proxy=engine._UNSET):
            seen["resolve"] = proxy
            return "https://cdn.example.com/book.epub"

        def fake_download(url, output_dir, **kwargs):
            seen["download"] = kwargs.get("proxy")
            seen["provider"] = kwargs.get("proxy_provider")
            return output_dir + "/book.epub"

        with patch.object(engine, "get_pool", return_value=Pool()), patch.dict(
            engine.CONFIG, {"proxy_pool_browser": False}, clear=False
        ), patch.object(engine, "resolve_direct_url", side_effect=fake_resolve), patch(
            "annas_api.downloader.download", side_effect=fake_download
        ):
            engine.download_book(md5="a" * 32, output_dir="/tmp/x", quiet=True)

        self.assertIs(seen["resolve"], engine._UNSET)
        self.assertEqual(seen["download"], "http://1.2.3.4:1111")
        self.assertEqual(seen["provider"](), "http://1.2.3.4:2222")


if __name__ == "__main__":
    unittest.main()
