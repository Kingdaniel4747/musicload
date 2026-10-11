"""Offline regressions for preview fallback and subprocess ownership."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from musicload.web import preview


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_stream_url_stays_same_origin(self):
        result = await preview.get_stream_url('abcdefghijk')
        self.assertEqual(result.url, '/api/preview/abcdefghijk')
        self.assertEqual(result.expires_in, 0)
        with self.assertRaises(HTTPException) as error:
            await preview.get_stream_url('../invalid')
        self.assertEqual(error.exception.status_code, 400)

    async def test_cancelled_startup_kills_and_reaps_ffmpeg(self):
        entered = asyncio.Event()
        async def wait_for_audio(*args):
            entered.set()
            await asyncio.Event().wait()
        process = Mock()
        process.returncode = None
        process.stdout.read = AsyncMock(side_effect=wait_for_audio)
        process.stderr.read = AsyncMock(return_value=b'')
        process.wait = AsyncMock(return_value=-9)
        process.kill = Mock(side_effect=lambda: setattr(process, 'returncode', -9))
        candidate = preview._AudioStreamCandidate('https://example.invalid/audio', {}, '1', 'https', False)
        with patch.object(preview.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            task = asyncio.create_task(preview._start_preview_candidate(candidate))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()

    async def test_failed_candidates_invalidate_expiring_urls(self):
        candidate = preview._AudioStreamCandidate('https://example.invalid/audio', {}, '1', 'https', False)
        preview._stream_candidate_cache.put('abcdefghijk', [candidate])
        with patch.object(preview, '_start_preview_candidate', AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as error:
                await preview.preview_audio('abcdefghijk')
        self.assertEqual(error.exception.status_code, 502)
        self.assertIsNone(preview._stream_candidate_cache.get('abcdefghijk'))

    def test_audio_priority_and_missing_bitrate_fallback(self):
        self.assertEqual(preview._format_quality({'abr': None, 'tbr': 192}), 192)
        audio = {'url':'https://example.invalid/a', 'vcodec':'none', 'abr':128}
        video = {'url':'https://example.invalid/v', 'vcodec':'h264', 'abr':256}
        self.assertGreater(preview._candidate_priority(audio), preview._candidate_priority(video))

    def test_source_headers_cannot_inject_ffmpeg_headers(self):
        headers = preview._safe_http_headers({'http_headers': {'User-Agent':'Musicload', 'Bad':'x\r\nInjected: yes'}}, {})
        self.assertEqual(headers, {'User-Agent':'Musicload'})
        message = preview._preview_error_summary(b'Forbidden https://example.invalid/audio?token=private')
        self.assertNotIn('private', message)


if __name__ == '__main__':
    unittest.main()
