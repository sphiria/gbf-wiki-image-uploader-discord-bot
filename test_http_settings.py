"""Exercise image requests without logging into the wiki or starting Discord."""

import hashlib
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from http_settings import BROWSER_USER_AGENT
from images import WikiImages


class ImageRequestTests(unittest.TestCase):
    def test_download_headers_and_payload(self):
        request_patch = patch("images.requests.get")
        get = request_patch.start()
        self.addCleanup(request_patch.stop)
        owner = types.SimpleNamespace(
            _proxy_url="http://proxy.example:8888", _sleep_after_failed_probe=Mock()
        )
        get.return_value = types.SimpleNamespace(
            status_code=200, content=b"image bytes"
        )
        with patch.dict("os.environ", {"USER_AGENT": "wiki-agent"}):
            success, digest, size, stream = WikiImages.get_image(
                owner, "https://game.example/image.png"
            )
        self.assertTrue(success)
        self.assertEqual(digest, hashlib.sha1(b"image bytes").hexdigest())
        self.assertEqual(size, 11)
        self.assertEqual(stream.tell(), 0)
        self.assertEqual(stream.read(), b"image bytes")
        self.assertEqual(
            get.call_args.kwargs,
            {
                "headers": {"User-Agent": BROWSER_USER_AGENT},
                "proxies": {"http": owner._proxy_url, "https": owner._proxy_url},
                "stream": True,
                "timeout": 30,
            },
        )
        get.reset_mock()
        get.return_value = types.SimpleNamespace(status_code=404)
        self.assertEqual(
            WikiImages.get_image(owner, "https://game.example/missing.png"),
            (False, "", 0, False),
        )
        get.assert_called_once()
        owner._sleep_after_failed_probe.assert_called_once()


class ConcurrentImageRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_headers_payload_and_missing_image(self):
        owner = types.SimpleNamespace(
            _proxy_url="http://proxy.example:8888",
            _async_sleep_after_failed_probe=AsyncMock(),
        )
        urls = ["https://game.example/image.png", "https://game.example/missing.png"]
        session = AsyncMock()

        def response(url, **kwargs):
            result = types.SimpleNamespace(
                status=200 if url == urls[0] else 404,
                read=AsyncMock(return_value=b"image bytes"),
            )
            context = AsyncMock()
            context.__aenter__.return_value = result
            return context

        session.get = Mock(side_effect=response)
        with patch("images.aiohttp.ClientSession") as client:
            client.return_value.__aenter__.return_value = session
            results, pending = await WikiImages.get_images_concurrent(owner, urls)
        self.assertEqual(pending, [])
        results = {result[0]: result[1:] for result in results}
        success, digest, size, stream = results[urls[0]]
        self.assertTrue(success)
        self.assertEqual(
            (digest, size, stream.tell(), stream.read()),
            (hashlib.sha1(b"image bytes").hexdigest(), 11, 0, b"image bytes"),
        )
        self.assertEqual(results[urls[1]], (False, "", 0, False))
        self.assertEqual(session.get.call_count, 2)
        for call in session.get.call_args_list:
            self.assertEqual(
                call.kwargs,
                {
                    "headers": {"User-Agent": BROWSER_USER_AGENT},
                    "proxy": owner._proxy_url,
                },
            )
        owner._async_sleep_after_failed_probe.assert_awaited_once()
