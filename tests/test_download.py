import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.engine import (
    FileTooLargeError,
    fetch_url_to_temp,
    place_downloaded_file,
)


class DownloadHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/large":
            body = b"x" * (2 * 1024 * 1024)
        else:
            body = b"small lecture"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="L01.pdf"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class FakeContext:
    async def cookies(self, _url):
        return []


class FakePage:
    context = FakeContext()

    async def evaluate(self, _expression):
        return "WBLE-Agent-Test"


class StreamingDownloadTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DownloadHandler)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    async def test_streaming_download_and_size_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            partial_dir = os.path.join(temp_dir, ".partial")
            result = await fetch_url_to_temp(
                FakePage(),
                f"{self.base_url}/small",
                partial_dir,
                1024 * 1024,
                allow_html=False,
            )
            complete, added, classified = await place_downloaded_file(
                result, temp_dir, "Lecture 1"
            )
            self.assertTrue(complete)
            self.assertTrue(classified)
            self.assertEqual(added, 1)
            with open(
                os.path.join(temp_dir, "Lectures", "L01.pdf"), "rb"
            ) as file:
                self.assertEqual(file.read(), b"small lecture")

            with self.assertRaises(FileTooLargeError):
                await fetch_url_to_temp(
                    FakePage(),
                    f"{self.base_url}/large",
                    partial_dir,
                    1024 * 1024,
                    allow_html=False,
                )
            self.assertEqual(os.listdir(partial_dir), [])


if __name__ == "__main__":
    unittest.main()
