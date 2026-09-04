import json
import os
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from modules.cloud.endpoints import load_public_origins, share_links


class EndpointTests(unittest.TestCase):
    def test_primary_and_all_backups_are_joined_to_share_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "endpoints.json")
            with open(path, "w", encoding="utf-8") as file:
                json.dump({"primary": ["http://frp:8080"], "backups": ["https://one", "https://two/"]}, file)
            with patch("modules.cloud.endpoints.ENDPOINTS_PATH", path), patch(
                "modules.cloud.endpoints.subprocess.run", side_effect=subprocess.SubprocessError
            ):
                self.assertEqual(load_public_origins()[0], ["http://frp:8080"])
                links = share_links("/s/cloud/token")
            self.assertEqual([item["url"] for item in links], [
                "http://frp:8080/s/cloud/token", "https://one/s/cloud/token", "https://two/s/cloud/token"
            ])


if __name__ == "__main__":
    unittest.main()
