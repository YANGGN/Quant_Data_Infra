from __future__ import annotations

import unittest
from decimal import Decimal

from quant_data.errors import ValidationError
from quant_data.operations.job_retry import (
    JobStepError,
    RetryPolicy,
    retry_policy_for,
)


class Stage7RetryPolicyTests(unittest.TestCase):
    def test_named_policy_is_bounded_and_deterministic(self) -> None:
        policy = retry_policy_for("collector_declared")
        self.assertIs(policy, retry_policy_for("collector_declared"))
        self.assertEqual(policy.max_attempts, 3)
        transient = JobStepError(
            "rate_limited",
            retryable=True,
            safe_message="bounded rate limit",
        )
        self.assertEqual(
            policy.delay_after(transient, completed_attempts=1),
            Decimal("0.25"),
        )
        self.assertEqual(
            policy.delay_after(transient, completed_attempts=2),
            Decimal("0.50"),
        )
        self.assertIsNone(
            policy.delay_after(transient, completed_attempts=3),
        )

    def test_retry_after_is_honored_only_within_bounds(self) -> None:
        policy = retry_policy_for("collector_declared")
        bounded = JobStepError(
            "rate_limited",
            retryable=True,
            retry_after_seconds=Decimal("2.5"),
            safe_message="bounded rate limit",
        )
        self.assertEqual(
            policy.delay_after(
                bounded,
                completed_attempts=1,
                remaining_seconds=3,
            ),
            Decimal("2.5"),
        )
        self.assertIsNone(
            policy.delay_after(
                bounded,
                completed_attempts=1,
                remaining_seconds=2,
            )
        )
        too_long = JobStepError(
            "rate_limited",
            retryable=True,
            retry_after_seconds=Decimal("30"),
            safe_message="bounded rate limit",
        )
        self.assertIsNone(policy.delay_after(too_long, completed_attempts=1))

    def test_configuration_schema_and_timeout_are_never_retried(self) -> None:
        policy = retry_policy_for("collector_declared")
        for code in ("configuration", "schema", "timeout"):
            with self.subTest(code):
                error = JobStepError(code, safe_message="bounded step failure")
                self.assertIsNone(
                    policy.delay_after(error, completed_attempts=1)
                )

    def test_invalid_policy_and_unsafe_errors_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            retry_policy_for("unbounded")
        with self.assertRaises(ValidationError):
            RetryPolicy("collector_declared", max_attempts=4)
        with self.assertRaises(ValidationError):
            JobStepError(
                "configuration",
                retryable=True,
                safe_message="bad configuration",
            )
        with self.assertRaises(ValidationError):
            JobStepError(
                "temporary",
                retryable=True,
                safe_message="request https://example.invalid",
            )


if __name__ == "__main__":
    unittest.main()
