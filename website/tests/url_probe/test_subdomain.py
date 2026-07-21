import unittest

from modules.url_probe.subdomain import _valid_name, normalize_domain


class SubdomainInputTest(unittest.TestCase):
    def test_accepts_domain_and_url(self):
        self.assertEqual(normalize_domain("Example.COM."), "example.com")
        self.assertEqual(normalize_domain("https://www.example.com/path"), "www.example.com")

    def test_rejects_invalid_domain(self):
        with self.assertRaises(ValueError):
            normalize_domain("localhost")

    def test_certificate_name_must_be_inside_domain(self):
        self.assertEqual(_valid_name("*.api.example.com", "example.com"), "api.example.com")
        self.assertIsNone(_valid_name("notexample.com", "example.com"))


if __name__ == "__main__":
    unittest.main()
