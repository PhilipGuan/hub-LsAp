"""从已有作业配置启动服务；密钥仅在进程内存中传递。"""
import argparse
import uvicorn
from verify_deepseek import configure

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--credentials',required=True)
    p.add_argument('--port',type=int,default=8013)
    args=p.parse_args()
    configure(args.credentials)
    uvicorn.run('app:app',host='127.0.0.1',port=args.port)
