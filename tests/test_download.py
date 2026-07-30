import os
import ssl
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.engine import (
    FileTooLargeError,
    fetch_url_to_temp,
    is_certificate_verification_error,
    is_utar_https_url,
    place_downloaded_file,
    remap_tracked_download_path,
    tracked_resource_files_exist,
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
            complete, added, classified, relative_path = (
                await place_downloaded_file(result, temp_dir, "Lecture 1")
            )
            self.assertTrue(complete)
            self.assertTrue(classified)
            self.assertEqual(added, 1)
            self.assertEqual(relative_path, "Lectures/L01.pdf")
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

    def test_tls_fallback_is_strictly_limited_to_utar_https(self):
        self.assertTrue(
            is_utar_https_url(
                "https://wble-kpr.utar.edu.my/wble-kpr/"
            )
        )
        self.assertFalse(is_utar_https_url("http://wble-kpr.utar.edu.my/"))
        self.assertFalse(is_utar_https_url("https://utar.edu.my.example.com/"))
        self.assertFalse(is_utar_https_url("https://example.com/"))

    def test_certificate_verification_error_detection(self):
        ssl_error = ssl.SSLCertVerificationError(
            1, "CERTIFICATE_VERIFY_FAILED"
        )
        self.assertTrue(
            is_certificate_verification_error(
                urllib.error.URLError(ssl_error)
            )
        )
        self.assertFalse(
            is_certificate_verification_error(
                urllib.error.URLError("connection refused")
            )
        )

    async def test_local_file_is_source_of_truth_for_download_record(self):
        with tempfile.TemporaryDirectory() as course_dir:
            file_path = os.path.join(
                course_dir, "Files", "Lectures", "L01.pdf"
            )
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "wb") as file:
                file.write(b"lecture")

            resource_key = "https://example.test/resource/1"
            state = {
                "downloaded_files": [resource_key],
                "downloaded_file_paths": {
                    resource_key: ["Lectures/L01.pdf"]
                },
            }
            self.assertTrue(
                tracked_resource_files_exist(
                    course_dir, state, resource_key
                )
            )

            os.remove(file_path)
            self.assertFalse(
                tracked_resource_files_exist(
                    course_dir, state, resource_key
                )
            )

    async def test_legacy_database_record_without_path_is_not_trusted(self):
        resource_key = "https://example.test/resource/legacy"
        state = {"downloaded_files": [resource_key]}
        with tempfile.TemporaryDirectory() as course_dir:
            self.assertFalse(
                tracked_resource_files_exist(
                    course_dir, state, resource_key
                )
            )

    async def test_reclassification_updates_tracked_local_path(self):
        resource_key = "https://example.test/resource/reclassified"
        state = {
            "downloaded_file_paths": {
                resource_key: ["Others/Week 1.pdf"]
            }
        }
        updated = remap_tracked_download_path(
            state,
            os.path.join("Others", "Week 1.pdf"),
            os.path.join("Lectures", "Week 1.pdf"),
        )
        self.assertEqual(updated, 1)
        self.assertEqual(
            state["downloaded_file_paths"][resource_key],
            ["Lectures/Week 1.pdf"],
        )


if __name__ == "__main__":
    unittest.main()
