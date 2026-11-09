import hashlib
import unittest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from graph_agent import GraphSQLAgent
from sql_core import CASES,DEFAULT_DB
from app import app

class Tests(unittest.TestCase):
    def test_five_queries(self):
        results=[GraphSQLAgent().run(c['question']) for c in CASES]
        self.assertTrue(all(r['status']=='success' for r in results))
        self.assertEqual(results[0]['result']['rows'][0],['USA',523.06])
        self.assertEqual(results[1]['result']['rows'][0],['Rock',826.65])
        self.assertEqual(len(results[2]['result']['rows']),12)
        self.assertEqual(results[3]['result']['rows'],[[59]])
        self.assertEqual(results[4]['result']['rows'],[])
    def test_repair_edge(self):
        r=GraphSQLAgent(inject_error=True).run(CASES[0]['question'])
        self.assertEqual(r['status'],'success')
        self.assertEqual(r['repairs'],1)
        self.assertEqual([x['stage'] for x in r['trace']].count('sql_validate'),2)
    def test_limit(self):
        a=GraphSQLAgent(inject_error=True)
        a.fixture.ask=lambda stage,p: {'tables':['invoices']} if stage=='select' else {'sql':'SELECT missing FROM invoices'}
        r=a.run(CASES[0]['question'])
        self.assertEqual(r['status'],'failed');self.assertEqual(r['repairs'],2)
    def test_clarification(self):
        self.assertEqual(GraphSQLAgent().run('你好')['status'],'clarification')
    def test_context(self):
        r=GraphSQLAgent().run('改为前三名',[{'role':'user','content':CASES[0]['question']}])
        self.assertEqual(len(r['result']['rows']),3)
        self.assertEqual(GraphSQLAgent().run('改为前三名')['status'],'clarification')
    def test_readonly(self):
        before=hashlib.sha256(DEFAULT_DB.read_bytes()).digest()
        db=GraphSQLAgent().db
        for sql in ['DELETE FROM customers','SELECT 1; DROP TABLE customers','SELECT * FROM employees']:
            self.assertFalse(db.check_sql(sql,['customers'])['ok'])
        self.assertEqual(before,hashlib.sha256(DEFAULT_DB.read_bytes()).digest())
    def test_live_chains_with_fake_model(self):
        model=FakeListChatModel(responses=['{"question":"客户总数"}','{"tables":["customers"],"reason":"计数"}','{"sql":"SELECT COUNT(*) FROM customers"}','{"answer":"59 位客户","evidence":[],"caveats":[]}'])
        r=GraphSQLAgent('live',model=model).run('有多少客户')
        self.assertEqual(r['status'],'success');self.assertEqual(r['result']['rows'],[[59]])
    def test_api(self):
        c=TestClient(app)
        r=c.post('/api/chat',json={'question':CASES[3]['question']})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['result']['rows'],[[59]])
        self.assertEqual(c.post('/api/chat',json={'question':' '}).status_code,422)
        self.assertEqual(c.post('/api/chat',json={'question':'x','history':[{'role':'system','content':'x'}]}).status_code,422)
        self.assertEqual(c.get('/').status_code,200)

if __name__=='__main__': unittest.main(verbosity=2)
