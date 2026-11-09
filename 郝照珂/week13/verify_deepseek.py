"""从指定本地 env 文件读取密钥，只在进程环境内使用；不输出或复制密钥。"""
import argparse
import concurrent.futures
import json
import os
import time
import math
from itertools import permutations
from datetime import datetime
from pathlib import Path
from fastapi.testclient import TestClient
from app import app
from graph_agent import GraphSQLAgent
from sql_core import CASES, DBParser

def configure(path):
    values={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1);values[k.strip()]=v.strip().strip('\"\'')
    os.environ['SQL_API_KEY']=values['DEEPSEEK_API_KEY']
    os.environ['SQL_BASE_URL']='https://api.deepseek.com'
    os.environ['SQL_MODEL']='deepseek-flash'

def same_cells(actual,expected):
    if not isinstance(actual,list) or len(actual)!=len(expected): return False
    for a,b in zip(actual,expected):
        if len(a)!=len(b): return False
        for x,y in zip(a,b):
            if isinstance(x,(int,float)) and isinstance(y,(int,float)):
                if not math.isclose(x,y,rel_tol=1e-9,abs_tol=1e-6): return False
            elif x!=y: return False
    return True

def equivalent(actual,expected):
    # Additional descriptive columns (e.g. GenreId) are allowed. Require one
    # consistent projection for every row, preserving row order and all values.
    if not isinstance(actual,list) or len(actual)!=len(expected): return False
    if not expected: return True
    if not actual or len(actual[0])>12: return False
    for indexes in permutations(range(len(actual[0])),len(expected[0])):
        if same_cells([[row[i] for i in indexes] for row in actual],expected): return True
    return False

def verify_case(i):
    case=CASES[i];started=time.monotonic()
    with TestClient(app) as client:
        response=client.post('/api/chat',json={'mode':'live','question':case['question']})
    result=response.json()
    expected=DBParser().query(case['sql'],case['tables'])['rows']
    actual=result.get('result',{}).get('rows')
    # SQL aliases can differ; compare ordered cell values, not column labels.
    passed=response.status_code==200 and result.get('status')=='success' and equivalent(actual,expected)
    return {'case':i+1,'question':case['question'],'http_status':response.status_code,'passed':passed,'expected_rows':expected,'seconds':round(time.monotonic()-started,2),'response':result}

def main():
    p=argparse.ArgumentParser();p.add_argument('--credentials',required=True);args=p.parse_args()
    configure(args.credentials)
    out=Path(__file__).parent/'outputs';out.mkdir(exist_ok=True)
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for r in pool.map(verify_case,range(len(CASES))):
            results.append(r)
            print(json.dumps({'case':r['case'],'passed':r['passed'],'seconds':r['seconds']},ensure_ascii=False),flush=True)
            (out/'deepseek_validation.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    with TestClient(app) as client:
        follow=client.post('/api/chat',json={'mode':'live','question':'改为前三名','history':[{'role':'user','content':CASES[0]['question']},{'role':'assistant','content':results[0]['response'].get('answer','')}]}).json()
    expected=results[0]['expected_rows'][:3]
    results.append({'case':'followup','passed':follow.get('status')=='success' and equivalent(follow.get('result',{}).get('rows'),expected),'response':follow})
    class InjectedError(GraphSQLAgent):
        def generate(self,s):
            return {'sql':'SELECT SUM(TotalAmount) FROM invoices','error':''}
    repair=InjectedError('live').run('所有发票的销售总额是多少？')
    expected_total=DBParser().query('SELECT SUM(Total) FROM invoices',['invoices'])['rows'][0][0]
    actual=repair.get('result',{}).get('rows',[])
    passed=repair.get('status')=='success' and repair.get('repairs',0)>0 and len(actual)==1 and abs(float(actual[0][0])-expected_total)<0.01
    results.append({'case':'injected_sql_repair','passed':passed,'note':'仅故障 SQL 为人工注入，选表、改写和汇总均为真实模型调用。','response':repair})
    report={'verified_at':datetime.now().isoformat(),'provider':'DeepSeek','model':'deepseek-flash','endpoint':'https://api.deepseek.com','passed':sum(r['passed'] for r in results),'total':len(results),'cases':results}
    (out/'deepseek_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'total':report['total']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
