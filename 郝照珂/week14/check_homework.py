"""Local regression checks. Kafka, Redis and model boundaries use explicit mocks."""
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from app import broker, config, history, rag, store, worker
from app.main import app


class HomeworkChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(store, 'DATA', self.root),
                        patch.object(config, 'UPLOADS', self.root / 'uploads'),
                        patch.object(config, 'PROCESSED', self.root / 'processed'),
                        patch.object(broker, 'dispatch')]
        for item in self.patches:
            item.start()
        config.UPLOADS.mkdir()
        config.PROCESSED.mkdir()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def upload(self):
        response = self.client.post('/api/documents', files=[
            ('files', ('first.pdf', b'%PDF-1.4\nexample', 'application/pdf')),
            ('files', ('second.pdf', b'%PDF-1.4\nexample', 'application/pdf'))])
        self.assertEqual(response.status_code, 202)
        return response.json()['files']

    def test_batch_upload_outbox_and_validation(self):
        files = self.upload()
        self.assertEqual(len(files), 2)
        self.assertTrue(all(file['status'] == 'queued' for file in files))
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox WHERE published=0').fetchone()[0], 2)
        response = self.client.post('/api/documents', files={'files': ('fake.pdf', b'not-pdf')})
        self.assertIn('error', response.json()['files'][0])
        self.assertEqual(len(store.documents()), 2)

    def test_worker_completion_replay_and_scope(self):
        files = self.upload()
        document = store.document(files[0]['id'])
        payload = {'document_id': document['id'], 'job_id': document['job_id']}
        with patch.object(worker, 'parse_pdf', return_value=('# Kafka\n\nKafka 通过消息队列传递消息。',
                                                           ['Kafka 通过消息队列传递消息。'])) as parse:
            worker.process(payload)
            worker.process(payload)
            self.assertEqual(parse.call_count, 1)
        self.assertEqual(store.document(document['id'])['status'], 'completed')
        self.assertEqual(len(store.all_chunks([])), 1)
        self.assertEqual(len(rag.retrieve('Kafka 消息', [])), 1)
        self.assertEqual(rag.retrieve('Kafka', [files[1]['id']]), [])
        self.assertEqual(self.client.get(f'/api/documents/{document["id"]}/markdown').status_code, 200)

    def test_failed_retry_and_old_event(self):
        document = store.document(self.upload()[0]['id'])
        payload = {'document_id': document['id'], 'job_id': document['job_id']}
        with patch.object(worker, 'parse_pdf', side_effect=RuntimeError('parser unavailable')):
            with self.assertLogs('app.worker', level='ERROR'):
                worker.process(payload)
        self.assertEqual(store.document(document['id'])['status'], 'failed')
        self.assertEqual(self.client.post(f'/api/documents/{document["id"]}/retry').status_code, 200)
        self.assertNotEqual(store.document(document['id'])['job_id'], document['job_id'])
        with patch.object(worker, 'parse_pdf') as parse:
            worker.process(payload)
            parse.assert_not_called()
        self.assertEqual(store.document(document['id'])['status'], 'queued')

    def test_images_paths_and_multimodal_payload(self):
        folder = config.PROCESSED / 'doc'
        folder.mkdir()
        image = folder / 'figure.png'
        Image.new('RGB', (30, 30), 'green').save(image)
        markdown = folder / 'content.md'
        markdown.write_text('# 图表\n\n说明\n\n![示例](figure.png)', encoding='utf-8')
        text = rag.normalize_markdown(markdown)
        self.assertIn('/assets/doc/figure.png', text)
        self.assertEqual(self.client.get('/assets/doc/figure.png').status_code, 200)
        with self.assertRaises(ValueError):
            rag.asset_path('/assets/../../outside.png')
        user_image = rag.image_data(image.read_bytes())
        source = {'id': 1, 'document_id': 'doc', 'name': '课程.pdf', 'text': text}
        with patch.object(rag, 'retrieve', return_value=[source]), patch.object(
                rag, 'complete', side_effect=['图表 关键词', '回答 ![示例](/assets/doc/figure.png)']) as complete:
            answer, sources = rag.answer('解释图片', user_image, [], [])
        content = complete.call_args.args[0][-1]['content']
        self.assertEqual(sum(item['type'] == 'image_url' for item in content), 2)
        self.assertIn('/assets/doc/figure.png', answer)
        self.assertEqual(len(sources), 1)

    def test_chat_and_redis_history_transaction(self):
        session = str(uuid.uuid4())
        client = MagicMock()
        client.lrange.return_value = []
        with patch.object(history, 'client', client), patch.object(rag, 'answer', return_value=('测试回答', [])):
            response = self.client.post('/api/chat', data={'question': '你好', 'session_id': session})
            self.assertEqual(response.status_code, 200, response.text)
            pipeline = client.pipeline.return_value.__enter__.return_value
            pipeline.rpush.assert_called_once()
            pipeline.execute.assert_called_once()
            saved = pipeline.rpush.call_args.args
            self.assertEqual(json.loads(saved[1])['content'], '你好')
            self.assertEqual(json.loads(saved[2])['content'], '测试回答')

    def test_mineru_legacy_command(self):
        def run(command, **kwargs):
            output = Path(command[command.index('-o') + 1])
            (output / 'content.md').write_text('# 课程\n\nRedis 保存历史。', encoding='utf-8')
            return MagicMock(returncode=0)

        with patch('app.worker.subprocess.run', side_effect=run) as invoke:
            markdown, chunks = worker.parse_pdf('test-document')
        self.assertIn('Redis', markdown)
        self.assertTrue(chunks)
        self.assertIsInstance(invoke.call_args.args[0], list)


if __name__ == '__main__':
    unittest.main(verbosity=2)
