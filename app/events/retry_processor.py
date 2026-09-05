"""Active Retry Processor and Dead-Letter Queue (DLQ) routing engine."""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from app.events.broker import EventBroker, EventMessage


class RetryProcessor:
    """
    Manages active delayed retries and dead-letter queue routing for failed messages.
    Enforces scheduled backoff and republishes eligible events to the primary topic.
    """

    MAIN_TOPIC = "payment.events"
    RETRY_1_TOPIC = "payment.events.retry.1"
    RETRY_2_TOPIC = "payment.events.retry.2"
    DLQ_TOPIC = "payment.events.DLQ"

    GROUP_ID = "iro-retry-processor"

    @classmethod
    async def route_failure(
        cls,
        broker: EventBroker,
        message: EventMessage,
        reason: str,
        is_malformed: bool = False,
        backoff_sec: Optional[float] = None,
    ) -> str:
        """
        Route a failed event message to either a retry topic or directly to DLQ.
        Returns the destination topic name.
        """
        now = datetime.now(timezone.utc)
        headers = dict(message.headers)

        if is_malformed:
            # Poisonous / invalid schema messages bypass retries and go directly to DLQ
            headers["x-death-reason"] = "MALFORMED_EVENT_SCHEMA"
            headers["x-death-detail"] = reason
            headers["x-failed-at"] = now.isoformat()
            headers["x-original-topic"] = message.topic
            await broker.publish(cls.DLQ_TOPIC, message.value, key=message.key, headers=headers)
            return cls.DLQ_TOPIC

        current_retry = int(headers.get("x-retry-count", "0"))

        if current_retry == 0:
            delay = backoff_sec if backoff_sec is not None else 10.0
            retry_time = now + timedelta(seconds=delay)
            headers["x-retry-count"] = "1"
            headers["x-retry-timestamp"] = retry_time.isoformat()
            headers["x-failure-reason"] = reason
            headers["x-original-topic"] = message.topic
            await broker.publish(cls.RETRY_1_TOPIC, message.value, key=message.key, headers=headers)
            return cls.RETRY_1_TOPIC

        elif current_retry == 1:
            delay = backoff_sec if backoff_sec is not None else 30.0
            retry_time = now + timedelta(seconds=delay)
            headers["x-retry-count"] = "2"
            headers["x-retry-timestamp"] = retry_time.isoformat()
            headers["x-failure-reason"] = reason
            headers["x-original-topic"] = message.topic
            await broker.publish(cls.RETRY_2_TOPIC, message.value, key=message.key, headers=headers)
            return cls.RETRY_2_TOPIC

        else:
            # Exhausted retries
            headers["x-death-reason"] = "EXCEEDED_MAX_RETRIES"
            headers["x-failure-reason"] = reason
            headers["x-failed-at"] = now.isoformat()
            headers["x-original-topic"] = message.topic
            await broker.publish(cls.DLQ_TOPIC, message.value, key=message.key, headers=headers)
            return cls.DLQ_TOPIC

    @classmethod
    async def process_retries(
        cls,
        broker: EventBroker,
        timeout: float = 0.5,
        max_messages: int = 10,
    ) -> int:
        """
        Poll retry topics, inspect retry timestamps, and republish eligible messages to main topic.
        Returns count of messages successfully republished.
        """
        republished = 0
        now = datetime.now(timezone.utc)

        for retry_topic in (cls.RETRY_1_TOPIC, cls.RETRY_2_TOPIC):
            for _ in range(max_messages):
                msg = await broker.get_message(retry_topic, cls.GROUP_ID, timeout=timeout)
                if not msg:
                    break

                retry_ts_str = msg.headers.get("x-retry-timestamp")
                if retry_ts_str:
                    try:
                        retry_time = datetime.fromisoformat(retry_ts_str)
                    except ValueError:
                        retry_time = now

                    # If delay has not elapsed yet, re-queue message and stop for this cycle
                    if now < retry_time:
                        await broker.publish(retry_topic, msg.value, key=msg.key, headers=msg.headers)
                        await broker.commit(cls.GROUP_ID, msg)
                        break

                # Backoff duration has elapsed: republish to primary topic
                headers = dict(msg.headers)
                headers["x-republished-from"] = retry_topic
                await broker.publish(cls.MAIN_TOPIC, msg.value, key=msg.key, headers=headers)
                await broker.commit(cls.GROUP_ID, msg)
                republished += 1

        return republished
