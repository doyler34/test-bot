import asyncio
import unittest
from unittest import mock

from panel import whois

# The shape ARIN answers in, cut down.
ARIN = {
    "handle": "NET-99-96-0-0-1", "name": "SBCIS-SBIS", "type": "DIRECT ALLOCATION", "country": "US",
    "startAddress": "99.96.0.0", "endAddress": "99.127.255.255",
    "cidr0_cidrs": [{"v4prefix": "99.96.0.0", "length": 11}],
    "events": [{"eventAction": "registration", "eventDate": "2008-04-11T10:44:15-04:00"},
               {"eventAction": "last changed", "eventDate": "2010-06-09T11:20:37-04:00"}],
    "remarks": [{"description": ["For abuse issues contact abuse@att.net"]}],
    "entities": [
        {"handle": "AT-88-Z", "roles": ["registrant"],
         "vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", "AT&T Corp."],
                                  ["adr", {"label": "208 S. Akard Street\nDallas"}, "text", ["", "", "", "", "", "", ""]]]],
         "entities": [{"handle": "ABUSE7-ARIN", "roles": ["abuse"],
                       "vcardArray": ["vcard", [["fn", {}, "text", "abuse"],
                                                ["email", {}, "text", "abuse@att.net"]]]}]}],
}


class WhoisTests(unittest.TestCase):
    def test_reads_a_registry_answer(self):
        r = whois.read(ARIN)
        self.assertEqual((r["owner"], r["country"], r["name"]), ("AT&T Corp.", "US", "SBCIS-SBIS"))
        self.assertEqual(r["range"], "99.96.0.0 – 99.127.255.255")
        self.assertEqual(r["cidrs"], ["99.96.0.0/11"])
        self.assertEqual((r["registered"], r["changed"]), ("2008-04-11", "2010-06-09"))
        self.assertEqual(r["abuse"], "abuse@att.net")
        self.assertIn("abuse@att.net", r["remarks"][0])

    def test_only_real_addresses_and_no_private_ones(self):
        self.assertEqual(whois.clean_ip(" 99.102.29.3 "), "99.102.29.3")
        self.assertIsNone(whois.clean_ip("99.102.29"))
        self.assertIsNone(whois.clean_ip("1.2.3.4; rm -rf"))
        self.assertIn("private", asyncio.run(whois.lookup("192.168.1.5"))["error"])

    def test_a_registry_that_cant_be_reached_says_so(self):
        import urllib.error
        whois._cache.clear()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            self.assertIn("Couldn't reach the registry", asyncio.run(whois.lookup("8.8.8.8"))["error"])
