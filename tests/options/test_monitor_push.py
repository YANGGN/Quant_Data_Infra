"""Real Web Push encryption/signing with fixture keys and intercepted HTTP.

Requires the options-monitor development profile. No live subscriptions,
credentials, DNS lookup or network delivery are used.
"""
import base64
import json
import socket
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from http_ece import decrypt
import requests
from pywebpush import WebPushException

from quant_data.options.monitor.push import send_push


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class WebPushIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.receiver_key = ec.generate_private_key(ec.SECP256R1())
        public = self.receiver_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = b"0123456789abcdef"
        self.subscription = dict(id="fixture-browser", endpoint="https://fcm.googleapis.com/fixture",
                                 keys=dict(p256dh=encoded(public), auth=encoded(self.auth)))
        signer = ec.generate_private_key(ec.SECP256R1())
        self.signing_key = encoded(signer.private_numbers().private_value.to_bytes(32, "big"))
        self.alert = dict(id="fixture-alert", title="Fixture title", reason="Fixture body")
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("Network forbidden")))
        self.resolution = self.enterContext(patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]))
        self.request = self.enterContext(patch.object(requests.Session, "request", autospec=True))
        self.response = requests.Response()
        self.response.status_code = 201
        self.response._content = b"fixture response"
        self.request.return_value = self.response

    def test_sdk_encrypts_and_signs_one_bounded_nonredirecting_request(self):
        self.assertTrue(send_push(self.subscription, self.alert, self.signing_key))
        self.request.assert_called_once()
        args, kwargs = self.request.call_args
        self.assertFalse(args[0].trust_env)
        self.assertEqual(args[1:], ("POST", self.subscription["endpoint"]))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 8)
        headers = requests.structures.CaseInsensitiveDict(kwargs["headers"])
        self.assertEqual(str(headers["ttl"]), "1800")
        self.assertEqual(headers["content-encoding"], "aes128gcm")
        self.assertTrue(headers["authorization"].startswith("vapid "))
        decoded = decrypt(kwargs["data"], private_key=self.receiver_key,
                          auth_secret=self.auth, version="aes128gcm")
        self.assertEqual(json.loads(decoded), dict(id=self.alert["id"],
                          title=self.alert["title"], body=self.alert["reason"]))

    def test_redirect_and_delivery_errors_are_not_retried(self):
        for status in (302, 410, 500):
            with self.subTest(status=status):
                self.request.reset_mock()
                self.response.status_code = status
                with self.assertRaises(WebPushException):
                    send_push(self.subscription, self.alert, self.signing_key)
                self.request.assert_called_once()
                self.assertFalse(self.request.call_args.kwargs["allow_redirects"])

    def test_timeout_is_not_retried(self):
        self.request.side_effect = requests.Timeout("fixture timeout")
        with self.assertRaises(requests.Timeout):
            send_push(self.subscription, self.alert, self.signing_key)
        self.request.assert_called_once()

    def test_private_dns_resolution_rejects_before_http(self):
        self.resolution.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with self.assertRaisesRegex(ValueError, "push_private_address"):
            send_push(self.subscription, self.alert, self.signing_key)
        self.request.assert_not_called()
