"""Verify packaged PWA resources and the public API after the cleanup."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class WebAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            'MUSICLOAD_DATA_DIR': cls.storage.name,
            'MUSICLOAD_DOWNLOAD_DIR': cls.storage.name,
            'NAVIDROME_URL': '',
        })
        cls.env.start()
        from musicload.web.app import app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.env.stop()
        cls.storage.cleanup()

    def test_page_has_one_executable_source_and_all_scripts_exist(self):
        from bs4 import BeautifulSoup
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.text, 'html.parser')
        scripts = page.find_all('script')
        self.assertEqual(len(scripts), 8)
        for script in scripts:
            self.assertTrue(script.get('src'))
            self.assertEqual(self.client.get(script['src']).status_code, 200)
        for link in page.select('link[rel="stylesheet"]'):
            self.assertEqual(self.client.get(link['href']).status_code, 200)

    def test_manifest_icons_and_service_worker_are_available(self):
        manifest = self.client.get('/static/manifest.json').json()
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['scope'], '/')
        for icon in manifest['icons']:
            self.assertEqual(self.client.get(icon['src']).status_code, 200)
        worker = self.client.get('/sw.js')
        self.assertEqual(worker.status_code, 200)
        self.assertIn('fetch(event.request)', worker.text)

    def test_preview_api_contract_and_invalid_id(self):
        response = self.client.get('/api/stream-url/abcdefghijk')
        self.assertEqual(response.json()['url'], '/api/preview/abcdefghijk')
        self.assertEqual(self.client.get('/api/preview/invalid').status_code, 400)

    def test_manual_folder_endpoints_revalidate_and_preserve_root(self):
        root = Path(self.storage.name)
        empty = root / 'Artist' / 'Empty'
        empty.mkdir(parents=True)
        (empty / 'old.lrc').write_text('lyrics')
        result = self.client.get('/api/library/empty-folders')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(empty.exists())
        entry = result.json()['folders'][0]['entry_path']
        new_song = empty / 'New.mp3'
        new_song.write_bytes(b'music')
        self.assertEqual(self.client.delete('/api/library/empty-folders', params={'entry_path': entry}).status_code, 409)
        self.assertTrue(new_song.exists())
        new_song.unlink()
        self.assertEqual(self.client.delete('/api/library/empty-folders', params={'entry_path': entry}).status_code, 200)
        self.assertTrue(root.exists())
        self.assertFalse(empty.exists())
        self.assertEqual(self.client.delete('/api/library/empty-folders', params={'entry_path': '..'}).status_code, 409)

    def test_lifespan_starts_queue_and_cleans_background_tasks(self):
        from musicload.web import app as module
        with TestClient(module.app) as client:
            self.assertIsNotNone(module.queue_manager)
            self.assertEqual(client.get('/api/queue/jobs').status_code, 200)
        self.assertFalse(module._background_tasks)
