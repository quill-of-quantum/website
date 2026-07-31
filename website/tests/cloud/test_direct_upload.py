import io
import os
import tempfile
import unittest

from modules.cloud import service


class DirectUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_upload_folder = service.UPLOAD_FOLDER
        service.UPLOAD_FOLDER = self.temp_dir.name

    def tearDown(self):
        service.UPLOAD_FOLDER = self.original_upload_folder
        self.temp_dir.cleanup()

    def test_stream_is_written_and_atomically_published(self):
        meta = {}
        content = b"a large file in small test form"
        saved, error = service.save_uploaded_stream(
            io.BytesIO(content), "archive.zip", meta, expected_size=len(content)
        )
        self.assertIsNone(error)
        with open(os.path.join(self.temp_dir.name, saved["stored_name"]), "rb") as file:
            self.assertEqual(file.read(), content)
        self.assertEqual(meta[saved["stored_name"]]["original_name"], "archive.zip")

    def test_interrupted_stream_is_deleted(self):
        saved, error = service.save_uploaded_stream(
            io.BytesIO(b"partial"), "archive.zip", {}, expected_size=100
        )
        self.assertIsNone(saved)
        self.assertIn("上传中断", error["error"])
        temp_folder = os.path.join(self.temp_dir.name, ".direct_uploads")
        self.assertEqual(os.listdir(temp_folder), [])


if __name__ == "__main__":
    unittest.main()
