import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from annas_api import engine


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = Path(self.folder.name) / 'sample.djvu'
        self.source.write_bytes(b'example source')

    def run_book(self, converter):
        with patch('annas_api.downloader.download', return_value=str(self.source)), \
             patch.object(engine, 'find_djvu_tool', return_value='ddjvu'), \
             patch.object(engine, 'detect_proxy', return_value=None), \
             patch.object(engine.subprocess, 'run', side_effect=converter), \
             patch.dict(engine.CONFIG, {'auto_convert_djvu': True}):
            return engine.download_book(direct_url='https://example.org/sample.djvu', quiet=True)

    def test_converts_and_checks_pdf(self):
        def convert(command, check):
            self.assertEqual(command[1], '-format=pdf')
            document = fitz.open()
            document.new_page()
            document.save(command[-1])
            document.close()
            return subprocess.CompletedProcess(command, 0)

        result = self.run_book(convert)
        self.assertEqual(Path(result).suffix, '.pdf')
        self.assertTrue(self.source.exists())
        with fitz.open(result) as document:
            self.assertEqual(document.page_count, 1)

    def test_failed_conversion_returns_original(self):
        def fail(command, check):
            raise subprocess.CalledProcessError(1, command)

        # Capture the engine's fallback notice so the expected failure does not
        # masquerade as a real error in test and CI output.
        notice = io.StringIO()
        with contextlib.redirect_stdout(notice):
            result = self.run_book(fail)
        self.assertEqual(Path(result), self.source)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.source.with_suffix('.pdf').exists())
        self.assertIn("DjVu 转 PDF 失败", notice.getvalue())


if __name__ == '__main__':
    unittest.main()
