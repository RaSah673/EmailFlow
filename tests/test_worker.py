import importlib.util
from itertools import islice
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
DEPENDENCIES = ('confluent_kafka', 'psycopg_pool', 'httpx', 'opentelemetry')


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in DEPENDENCIES), 'Install backend requirements')
class WorkerTests(unittest.TestCase):
    def test_urgent_batch_precedence_preserves_order_within_each_topic(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql:///emailflow'}):
            from app.worker import TOPICS, queued_tasks

        standard_first, standard_next = [Mock(topic=lambda: TOPICS['standard']) for _ in range(2)]
        urgent_first, urgent_next = [Mock(topic=lambda: TOPICS['urgent']) for _ in range(2)]
        consumer = Mock()
        consumer.poll.return_value = standard_first
        consumer.consume.return_value = [urgent_first, standard_next, urgent_next]

        tasks = list(islice(queued_tasks(consumer), 4))

        self.assertEqual(tasks, [urgent_first, urgent_next, standard_first, standard_next])
        consumer.consume.assert_called_once_with(num_messages=3, timeout=0)
