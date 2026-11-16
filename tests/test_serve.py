import json
import threading
import urllib.error
import urllib.request

from fixtures import TmpDirCase
import serve


class ServeTest(TmpDirCase):
    def setUp(self):
        super().setUp()
        self.week = self.tmp / "2026-W46"
        self.week.mkdir()
        (self.week / "index.html").write_text("<p>hi</p>", encoding="utf-8")
        (self.week / "facts.json").write_text("{}", encoding="utf-8")
        self.server = serve.make_server(self.week, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def request(self, path, body=None, headers=None):
        data = body if isinstance(body, bytes) else (None if body is None else json.dumps(body).encode())
        headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=headers)) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_serves_index(self):
        for path in ("/", "/index.html"):
            self.assertEqual(self.request(path), (200, b"<p>hi</p>"))

    def test_post_results_writes_file(self):
        payload = {"week": "2026-W46", "role": "technical", "answers": {"q1": {"choice": 1}}, "score": {"correct": 1, "total": 1}}
        status, _ = self.request("/results", payload)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads((self.week / "results.json").read_text()), payload)
        self.assertEqual([p.name for p in self.week.glob("*.tmp")], [])

    def test_rejects_wrong_week_and_oversize(self):
        self.assertEqual(self.request("/results", {"week": "2026-W01", "answers": {}})[0], 400)
        self.assertEqual(self.request("/results", {"week": "2026-W46", "answers": []})[0], 400)
        self.assertEqual(self.request("/results", b"not json")[0], 400)
        self.assertEqual(self.request("/results", b"x" * (1024 * 1024 + 1))[0], 413)
        self.assertFalse((self.week / "results.json").exists())

    def test_only_index_and_results_served(self):
        self.assertEqual(self.request("/facts.json")[0], 404)
        self.assertEqual(self.request("/../x")[0], 404)
        self.assertEqual(self.request("/results")[0], 404)  # no results saved yet
        self.assertEqual(self.request("/other", {"a": 1})[0], 404)

    def test_wrong_host_rejected(self):
        self.assertEqual(self.request("/", headers={"Host": "evil.example:80"})[0], 403)
        self.assertEqual(self.request("/results", {"week": "2026-W46", "answers": {}},
                                      headers={"Host": "evil.example"})[0], 403)
        self.assertFalse((self.week / "results.json").exists())
        port = self.server.server_address[1]
        self.assertEqual(self.request("/", headers={"Host": f"localhost:{port}"})[0], 200)

    def test_post_needs_json_content_type(self):
        status, _ = self.request("/results", {"week": "2026-W46", "answers": {}},
                                 headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertFalse((self.week / "results.json").exists())

    def test_get_results_returns_saved_file(self):
        payload = {"week": "2026-W46", "role": "business", "answers": {"q1": {"choice": 0}}}
        self.request("/results", payload)
        status, body = self.request("/results")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), payload)
