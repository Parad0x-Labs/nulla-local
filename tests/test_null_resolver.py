from __future__ import annotations

import base64
import unittest

from core.null_resolver import (
    NULL_DOMAIN_SIZE,
    decode_null_domain,
    domain_filters,
    pad_name64,
    resolve_x402_endpoint,
)
from core.nulla_wallet import b58encode


def _blob(name="web0", owner=b"\x01" * 32, arweave=b"\x02" * 32,
          endpoint="https://parad0xlabs.com/x402", passport=b"\x00" * 32) -> bytes:
    raw = bytearray(NULL_DOMAIN_SIZE)
    raw[0] = 0x4E  # 'N'
    raw[1:1 + len(name.encode())] = name.encode()
    raw[65:97] = owner
    raw[97:129] = arweave
    ep = endpoint.encode()
    raw[129:129 + len(ep)] = ep
    raw[257:289] = passport
    return bytes(raw)


class DecodeTests(unittest.TestCase):
    def test_decodes_all_fields(self) -> None:
        rec = decode_null_domain(_blob())
        self.assertIsNotNone(rec)
        self.assertEqual(rec.name, "web0")
        self.assertEqual(rec.owner, b58encode(b"\x01" * 32))
        self.assertEqual(rec.arweave_txid, base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode())
        self.assertEqual(rec.x402_endpoint, "https://parad0xlabs.com/x402")
        self.assertIsNone(rec.passport_hash)  # all-zero -> None

    def test_passport_hash_when_set(self) -> None:
        rec = decode_null_domain(_blob(passport=b"\xab" * 32))
        self.assertEqual(rec.passport_hash, ("ab" * 32))

    def test_empty_endpoint_is_blank(self) -> None:
        rec = decode_null_domain(_blob(endpoint=""))
        self.assertEqual(rec.x402_endpoint, "")

    def test_rejects_short_blob(self) -> None:
        self.assertIsNone(decode_null_domain(b"\x4e" + b"\x00" * 10))

    def test_rejects_wrong_discriminator(self) -> None:
        bad = bytearray(_blob())
        bad[0] = 0x00
        self.assertIsNone(decode_null_domain(bytes(bad)))


class FilterTests(unittest.TestCase):
    def test_pad_name64(self) -> None:
        self.assertEqual(len(pad_name64("web0")), 64)
        self.assertIsNone(pad_name64("x" * 65))  # overflows the 64-byte field

    def test_domain_filters_shape(self) -> None:
        f = domain_filters("web0")
        self.assertEqual(f[0], {"dataSize": NULL_DOMAIN_SIZE})
        self.assertEqual(f[1]["memcmp"]["offset"], 0)
        self.assertEqual(f[2]["memcmp"]["offset"], 1)
        # disc byte 'N' base58-encodes to a non-empty ascii string
        self.assertTrue(isinstance(f[1]["memcmp"]["bytes"], str) and f[1]["memcmp"]["bytes"])

    def test_overflow_name_yields_no_filters(self) -> None:
        self.assertIsNone(domain_filters("x" * 65))


class ResolveGuardTests(unittest.TestCase):
    def test_resolve_x402_endpoint_none_on_overflow(self) -> None:
        # name too long -> filters None -> resolve returns None, no network call
        self.assertIsNone(resolve_x402_endpoint("x" * 65))


if __name__ == "__main__":
    unittest.main()
