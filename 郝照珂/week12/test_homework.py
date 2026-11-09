import io
import unittest
from unittest.mock import patch,AsyncMock
from PIL import Image
from fastapi.testclient import TestClient
from app import app,Observation,classify
from langchain_sql_agent import LangChainSQLAgent,demo_model

def observation(**changes):
    data=dict(clear=True,corridor=True,electric_vehicle=False,battery=False,charging_wire=False,fire_or_smoke=False,severe_blockage=False,large_combustibles=False,clutter=False,evidence=['测试证据'],suggestion='人工核查')
    data.update(changes);return Observation(**data)

class Tests(unittest.TestCase):
    def test_categories(self):
        for flags,expected in [({},'无风险'),({'clutter':True},'低风险'),({'large_combustibles':True},'中风险'),({'severe_blockage':True},'中风险'),({'electric_vehicle':True,'clutter':True},'高风险'),({'battery':True},'高风险'),({'charging_wire':True},'高风险'),({'corridor':False},'非楼道'),({'clear':False},'待复核')]:
            self.assertEqual(classify(observation(**flags)),expected)
    def test_upload(self):
        out=io.BytesIO();Image.new('RGB',(40,40)).save(out,format='PNG')
        with patch('app.infer',new=AsyncMock(return_value=observation(battery=True))):
            r=TestClient(app).post('/api/analyze',files={'file':('test.png',out.getvalue(),'image/png')})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['risk'],'高风险')
    def test_invalid(self):
        self.assertEqual(TestClient(app).post('/api/analyze',files={'file':('x.jpg',b'bad','image/jpeg')}).status_code,400)
    def test_oversized(self):
        self.assertEqual(TestClient(app).post('/api/analyze',files={'file':('x.jpg',b'x'*(8*1024*1024+1),'image/jpeg')}).status_code,413)
    def test_sql(self):
        r=LangChainSQLAgent(demo_model()).run('共有多少位客户？')
        self.assertEqual(r['status'],'success');self.assertEqual(r['result']['rows'],[[59]])
        self.assertIn('sql_rewrite',[x['stage'] for x in r['trace']])
    def test_sql_guard(self):
        a=LangChainSQLAgent(demo_model())
        self.assertFalse(a.db.check_sql('DELETE FROM customers',['customers'])['ok'])
    def test_homepage(self):
        self.assertIn('getUserMedia',TestClient(app).get('/').text)

if __name__=='__main__':unittest.main(verbosity=2)
