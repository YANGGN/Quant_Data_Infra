import os
import struct
import time
import unittest
from unittest.mock import patch
from quant_data.errors import ResourceLimitError
from quant_data.operations import equibles_transcript_backfill as job


def partial_frame(sender, *args):
    os.write(sender.fileno(), struct.pack("!i", 1024) + b"x")
    time.sleep(5)


def complete_frame(sender, *args):
    sender.send((200, {"x-ratelimit-remaining": "99999"}, b"[]"))
    sender.close()


class EquiblesTransportDeadlineTests(unittest.TestCase):
    path = "/v1/stocks/MSFT/investor-events?eventType=EarningsCall&limit=100&offset=0"

    def test_partial_pipe_frame_cannot_extend_request_wall_deadline(self):
        started = time.monotonic()
        with patch.object(job, "_http_child", partial_frame), patch.object(job, "REQUEST_TIMEOUT_SECONDS", 1):
            with self.assertRaises(ResourceLimitError):
                job.EquiblesTransport("synthetic-test-token").request(self.path)
        self.assertLess(time.monotonic() - started, 2.5)

    def test_original_complete_frame_survives_bounded_read(self):
        with patch.object(job, "_http_child", complete_frame):
            result = job.EquiblesTransport("synthetic-test-token").request(self.path)
        self.assertEqual(result, (200, {"x-ratelimit-remaining": "99999"}, b"[]"))
