import os
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import create_engine, Column, Integer, String

# from sqlalchemy.ext.declarative import declarative_base


# 返回一个基类。任何继承它的类，都会被自动识别为一张数据库表。
Base = declarative_base()


# 文件表：自增主键id用来唯一标识，文件名，文件路径，文件状态，字符串，不可为空
class File(Base):
    """文件表 - 核心文件管理系统"""
    __tablename__ = 'files'

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    filepath = Column(String(1000), nullable=False)
    filestate = Column(String(20), nullable=False)

    # 表级别的设置,SQLite默认不记录最大自增序号，每次都是重新算，所以id可能出现复用情况，这个参数可以保证不重复使用
    __table_args__ = (
        {'sqlite_autoincrement': True}
    )


"""
os.getcwd() = get current working directory，返回"当前工作目录"的绝对路径。不是文件夹的目录，一定是命令运行所在的目录
更建议用下面的方法，不会随着命令运行的目录改变而改变
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(BASE_DIR, 'db.db')
"""
db_path = os.path.join(os.getcwd(), 'db.db')
engine = create_engine(f'sqlite:///{db_path}')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
