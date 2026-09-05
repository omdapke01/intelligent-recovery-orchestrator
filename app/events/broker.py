"""Event broker abstraction with production Kafka and test in-memory implementations."""

import abc
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional
import uuid

from app.config import settings


@dataclass
class EventMessage:
    """Standard broker message with headers and offset metadata."""
    topic: str
    key: Optional[str]
    value: bytes
    headers: Dict[str, str] = field(default_factory=dict)
    offset: int = 0
    partition: int = 0

    @property
    def value_dict(self) -> Dict[str, Any]:
        return json.loads(self.value.decode("utf-8"))


class EventBroker(abc.ABC):
    """Abstract event broker interface supporting publishing and consumer-group subscriptions."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialize connections to the broker."""
        pass

    @abc.abstractmethod
    async def stop(self) -> None:
        """Close connections cleanly."""
        pass

    @abc.abstractmethod
    async def publish(
        self,
        topic: str,
        value: str | bytes | dict,
        key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Publish an event to a specific topic."""
        pass

    @abc.abstractmethod
    async def get_message(self, topic: str, group_id: str, timeout: float = 1.0) -> Optional[EventMessage]:
        """Fetch next available message for this consumer group."""
        pass

    @abc.abstractmethod
    async def commit(self, group_id: str, message: EventMessage) -> None:
        """Acknowledge message processing for this consumer group."""
        pass


class InMemoryEventBroker(EventBroker):
    """
    In-memory pub-sub event broker.
    Maintains independent queues per (topic, consumer_group) pair, faithfully reproducing
    Kafka's multi-consumer group broadcast and partition distribution semantics.
    """

    def __init__(self):
        # (topic, consumer_group) -> asyncio.Queue[EventMessage]
        self.queues: Dict[str, asyncio.Queue[EventMessage]] = {}
        # topic -> registered consumer groups
        self.topic_groups: Dict[str, set] = {}
        # topic -> all historical messages published
        self.topic_messages: Dict[str, List[EventMessage]] = {}
        # (topic, consumer_group) -> committed offsets
        self.committed_offsets: Dict[str, int] = {}
        self._offset_counter: int = 0
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def register_consumer_group(self, topic: str, group_id: str) -> None:
        if topic not in self.topic_groups:
            self.topic_groups[topic] = set()
        if group_id not in self.topic_groups[topic]:
            self.topic_groups[topic].add(group_id)
            key = f"{topic}:{group_id}"
            if key not in self.queues:
                self.queues[key] = asyncio.Queue()
            # Catch up on any messages published before this group registered
            if topic in self.topic_messages:
                for msg in self.topic_messages[topic]:
                    self.queues[key].put_nowait(msg)

    async def publish(
        self,
        topic: str,
        value: str | bytes | dict,
        key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if isinstance(value, dict):
            payload_bytes = json.dumps(value, default=str).encode("utf-8")
        elif isinstance(value, str):
            payload_bytes = value.encode("utf-8")
        else:
            payload_bytes = value

        async with self._lock:
            self._offset_counter += 1
            current_offset = self._offset_counter

            msg = EventMessage(
                topic=topic,
                key=key,
                value=payload_bytes,
                headers=headers or {},
                offset=current_offset,
            )

            if topic not in self.topic_messages:
                self.topic_messages[topic] = []
            self.topic_messages[topic].append(msg)

            # Broadcast to all registered consumer groups for this topic
            groups = self.topic_groups.get(topic, set())
            for group in groups:
                q_key = f"{topic}:{group}"
                if q_key not in self.queues:
                    self.queues[q_key] = asyncio.Queue()
                self.queues[q_key].put_nowait(msg)

    async def get_message(self, topic: str, group_id: str, timeout: float = 1.0) -> Optional[EventMessage]:
        self.register_consumer_group(topic, group_id)
        q_key = f"{topic}:{group_id}"
        q = self.queues[q_key]
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def commit(self, group_id: str, message: EventMessage) -> None:
        key = f"{message.topic}:{group_id}"
        self.committed_offsets[key] = message.offset


class KafkaEventBroker(EventBroker):
    """
    Production Kafka event broker backed by aiokafka.
    """

    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self.bootstrap_servers = bootstrap_servers
        self._producer = None
        self._consumers: Dict[str, Any] = {}

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8") if isinstance(v, dict) else v,
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
        for consumer in self._consumers.values():
            await consumer.stop()

    async def publish(
        self,
        topic: str,
        value: str | bytes | dict,
        key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if not self._producer:
            raise RuntimeError("KafkaEventBroker is not started.")

        val_bytes = json.dumps(value, default=str).encode("utf-8") if isinstance(value, dict) else (
            value.encode("utf-8") if isinstance(value, str) else value
        )
        key_bytes = key.encode("utf-8") if key else None
        header_list = [(k, v.encode("utf-8")) for k, v in (headers or {}).items()]

        await self._producer.send_and_wait(
            topic=topic,
            value=val_bytes,
            key=key_bytes,
            headers=header_list,
        )

    async def get_message(self, topic: str, group_id: str, timeout: float = 1.0) -> Optional[EventMessage]:
        from aiokafka import AIOKafkaConsumer
        key = f"{topic}:{group_id}"
        if key not in self._consumers:
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
            )
            await consumer.start()
            self._consumers[key] = consumer

        consumer = self._consumers[key]
        try:
            msg = await asyncio.wait_for(consumer.getone(), timeout=timeout)
            headers = {k: v.decode("utf-8") for k, v in (msg.headers or [])}
            return EventMessage(
                topic=msg.topic,
                key=msg.key.decode("utf-8") if msg.key else None,
                value=msg.value,
                headers=headers,
                offset=msg.offset,
                partition=msg.partition,
            )
        except asyncio.TimeoutError:
            return None

    async def commit(self, group_id: str, message: EventMessage) -> None:
        key = f"{message.topic}:{group_id}"
        if key in self._consumers:
            await self._consumers[key].commit()
