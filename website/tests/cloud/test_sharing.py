import os
import tempfile
import unittest
from unittest.mock import patch

from modules.cloud.sharing import (
    MAX_SHARE_SECONDS,
    MIN_SHARE_SECONDS,
    ShareTokenError,
    create_share,
    list_shares,
    normalize_share_seconds,
    resolve_share,
    revoke_share,
)


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_patch = patch("modules.cloud.sharing.SHARE_STORE_PATH", os.path.join(self.temp_dir.name, "shares.json"))
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp_dir.cleanup()

    def test_token_round_trip_and_expiry(self):
        files = [{"stored_name": "stored-file.zip", "display_name": "file.zip"}]
        token, record = create_share(files, 3600, now=1000)
        self.assertEqual(record["expires_at"], 4600)
        self.assertEqual(resolve_share(token, now=4599)["files"], files)
        with self.assertRaises(ShareTokenError):
            resolve_share(token, now=4600)

    def test_tampered_token_is_rejected(self):
        token, _ = create_share([{"stored_name": "file.txt"}], 3600, now=1000)
        with self.assertRaises(ShareTokenError):
            resolve_share(token + "x", now=1001)

    def test_share_can_be_listed_and_revoked(self):
        token, record = create_share([{"stored_name": "file.txt"}], 3600, now=1000)
        self.assertTrue(list_shares(now=1001)[0]["active"])
        self.assertTrue(revoke_share(record["id"]))
        with self.assertRaises(ShareTokenError):
            resolve_share(token, now=1001)

    def test_duration_is_bounded(self):
        self.assertEqual(normalize_share_seconds(1), MIN_SHARE_SECONDS)
        self.assertEqual(normalize_share_seconds(10**10), MAX_SHARE_SECONDS)
        self.assertEqual(normalize_share_seconds(None), 7 * 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
