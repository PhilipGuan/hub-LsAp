"""使用本地 HTTP 模型替身验证真实 LangChain 调用，无需密钥或外网。"""
import asyncio
from collections import deque
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch

import httpx

from agent import sql_agent, table_agent
from models.chat import ChatRequest
from models.workflow import SQLCandidate, TableSelection, WorkflowFailure
from services.chat_service import stream_ask
from services.llm_service import LLMService


class LangChainTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.replies = deque()
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.requests.append((self.path, body))
                status, content = cls.replies.popleft()
                self.send_response(status)
                if body.get('stream') and status == 200:
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    if isinstance(content, dict):
                        self.wfile.write(content['sse'].encode())
                        return
                    for text in content:
                        chunk = {'id': 'test', 'object': 'chat.completion.chunk',
                                 'created': 0, 'model': 'test', 'choices': [
                                     {'index': 0, 'delta': {'content': text}, 'finish_reason': None}]}
                        self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                        self.wfile.flush()
                    self.wfile.write(b'data: [DONE]\n\n')
                else:
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    result = ({'error': {'message': 'test error', 'type': 'authentication_error'}}
                              if status != 200 else {
                                  'id': 'test', 'object': 'chat.completion', 'created': 0,
                                  'model': 'test', 'choices': [{'index': 0, 'message': {
                                      'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}]})
                    self.wfile.write(json.dumps(result).encode())

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    async def asyncSetUp(self):
        self.replies.clear()
        self.requests.clear()
        with patch('config.LLM_BASE_URL', f'http://127.0.0.1:{self.server.server_port}/v1'), \
                patch('config.LLM_API_KEY', 'test-key'):
            self.llm = LLMService()
        self.addAsyncCleanup(self.llm.close)

    async def test_workflow_rewrites_sql_and_streams_answer(self):
        self.replies.extend([
            (200, json.dumps({'tables': ['tracks'], 'reason': '统计曲目'})),
            (200, json.dumps({'sql': 'SELECT nonexistent FROM tracks', 'params': {}})),
            (200, json.dumps({'sql': 'SELECT COUNT(*) AS total FROM tracks', 'params': {}})),
            (200, ['<thi', 'nk>内部推理</think>', '共有 ', '3503 首曲目。']),
        ])
        events = [event async for event in stream_ask(
            ChatRequest(question='共有多少首曲目？'), llm_factory=lambda: self.llm)]
        final = events[-1].data
        self.assertEqual(events[-1].event, 'done')
        self.assertEqual(final.status, 'success')
        self.assertEqual(final.rewrite_count, 1)
        self.assertEqual(final.rows, [[3503]])
        self.assertEqual(final.answer, '共有 3503 首曲目。')
        self.assertFalse(final.summary_degraded)
        self.assertEqual([e.data.delta for e in events if e.event == 'answer_delta'],
                         ['共有 ', '3503 首曲目。'])
        for path, body in self.requests:
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(body['max_tokens'], 3000)
            self.assertNotIn('max_completion_tokens', body)
            self.assertFalse(body['enable_thinking'])
            self.assertEqual(body['messages'][0]['role'], 'system')
            self.assertEqual(body['messages'][1]['role'], 'user')
        for _, body in self.requests[:3]:
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertIn('JSON Schema', body['messages'][0]['content'])
        self.assertNotIn('response_format', self.requests[-1][1])
        self.assertTrue(self.llm.client.is_closed)

    async def test_invalid_output_and_authentication_errors(self):
        chain = self.llm.make_agent('selection', table_agent.INSTRUCTIONS, TableSelection)
        for status, text, code in [
            (200, 'not JSON', 'model_output_error'),
            (200, '{"tables": [], "reason": "test", "unknown": 1}', 'model_output_error'),
            (401, '', 'model_auth_error'), (403, '', 'model_auth_error'),
            (500, '', 'model_error'),
        ]:
            with self.subTest(code=code, status=status):
                self.replies.append((status, text))
                with self.assertRaises(WorkflowFailure) as raised:
                    await self.llm.run_agent(chain, {'question': 'test'}, TableSelection)
                self.assertEqual(raised.exception.error.code, code)

    async def test_sql_parameters_remain_typed(self):
        self.replies.append((200, json.dumps({
            'sql': 'SELECT :country, :count', 'params': {'country': '中国', 'count': 3}})))
        chain = self.llm.make_agent('sql', sql_agent.INSTRUCTIONS, SQLCandidate)
        result = await self.llm.run_agent(chain, {'question': '含 {花括号}'}, SQLCandidate)
        self.assertEqual(result.params, {'country': '中国', 'count': 3})
        self.assertIsInstance(result.params['count'], int)
        body = self.requests[0][1]
        self.assertEqual(json.loads(body['messages'][1]['content'])['question'], '含 {花括号}')

    async def test_empty_summary_degrades_without_losing_query_result(self):
        self.replies.extend([
            (200, '{"tables": ["tracks"], "reason": "统计曲目"}'),
            (200, '{"sql": "SELECT COUNT(*) AS total FROM tracks", "params": {}}'),
            (200, [' ', '']),
        ])
        events = [event async for event in stream_ask(
            ChatRequest(question='共有多少首曲目？'), llm_factory=lambda: self.llm)]
        final = events[-1].data
        self.assertEqual(final.status, 'success')
        self.assertTrue(final.summary_degraded)
        self.assertEqual(final.rows, [[3503]])
        self.assertTrue(any(e.event == 'error' and not e.data.fatal for e in events))

    async def test_provider_sse_and_stream_errors(self):
        chain = self.llm.make_agent('summary', '回答问题')
        self.replies.append((200, {'sse': (
            ': heartbeat\r\n\r\n'
            'data: {"choices": [{"delta":\r\n'
            'data: {"reasoning_content": "隐藏推理"}}]}\r\n\r\n'
            'data: {"choices": [{"delta": {"content": "中文答案"}}]}\r\n\r\n'
            'data: {"choices": [], "usage": {}}\r\n\r\n'
            'data: [DONE]\r\n\r\n'
        )}))
        self.assertEqual([text async for text in self.llm.stream_agent(chain, {})], ['中文答案'])
        for payload in (
            'data: {"error": {"message": "失败"}}\n\n',
            'data: invalid JSON\n\n',
        ):
            self.replies.append((200, {'sse': payload}))
            with self.assertRaises(WorkflowFailure) as raised:
                _ = [text async for text in self.llm.stream_agent(chain, {})]
            self.assertEqual(raised.exception.error.code, 'summary_error')

    async def test_timeout_and_stream_cancellation(self):
        class TimedOut:
            async def ainvoke(self, data):
                raise TimeoutError

        with self.assertRaises(WorkflowFailure) as raised:
            await self.llm.run_agent(TimedOut(), {}, TableSelection)
        self.assertEqual(raised.exception.error.code, 'model_timeout')

        chain = self.llm.make_agent('selection', table_agent.INSTRUCTIONS, TableSelection)
        with patch.object(self.llm.client, 'send', side_effect=httpx.ReadTimeout('test timeout')):
            with self.assertRaises(WorkflowFailure) as raised:
                await self.llm.run_agent(chain, {}, TableSelection)
        self.assertEqual(raised.exception.error.code, 'model_timeout')

        closed = []

        class CancelledStream:
            async def astream(self, data):
                try:
                    yield '部分回答'
                    raise asyncio.CancelledError
                finally:
                    closed.append(True)

        stream = self.llm.stream_agent(CancelledStream(), {})
        self.assertEqual(await anext(stream), '部分回答')
        with self.assertRaises(asyncio.CancelledError):
            await anext(stream)
        self.assertEqual(closed, [True])
        stream = self.llm.stream_agent(CancelledStream(), {})
        self.assertEqual(await anext(stream), "部分回答")
        await stream.aclose()
        self.assertEqual(closed, [True, True])


if __name__ == '__main__':
    unittest.main()
