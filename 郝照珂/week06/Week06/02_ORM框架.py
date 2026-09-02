# pip install sqlalchemy
# 导入 SQLAlchemy 所需的模块
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

# 创建数据库引擎，这里使用 SQLite
# check_same_thread=False 允许在多线程环境下使用，但对于单文件示例可以忽略
# sqlite、mysql
engine = create_engine('sqlite:///library_orm.db', echo=True)

# 创建 ORM 模型的基类
# object relation mapping
Base = declarative_base()


# --- 定义 ORM 模型（与数据库表对应） ---

class Author(Base):
    __tablename__ = 'authors'  # 映射到数据库中的表名

    author_id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    nationality = Column(String)

    # 定义与 Book 表的关系，'books' 是 Author 实例可以访问的属性
    books = relationship("Book", back_populates="author")

    def __repr__(self):
        return f"<Author(name='{self.name}', nationality='{self.nationality}')>"


class Book(Base):
    __tablename__ = 'books'

    book_id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    published_year = Column(Integer)

    # 定义外键，关联到 authors 表的 author_id
    author_id = Column(Integer, ForeignKey('authors.author_id'))

    # 定义与 Author 表的关系，'author' 是 Book 实例可以访问的属性
    author = relationship("Author", back_populates="books")

    def __repr__(self):
        return f"<Book(title='{self.title}', published_year={self.published_year})>"


class Borrower(Base):
    __tablename__ = 'borrowers'

    borrower_id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True)

    def __repr__(self):
        return f"<Borrower(name='{self.name}', email='{self.email}')>"


# --- 创建数据库和表 ---
# 这一步会根据上面定义的模型，在数据库中创建相应的表
Base.metadata.create_all(engine)
print("数据库和表已成功创建。")

# 创建会话（Session）
# Session 是我们与数据库进行所有交互的接口
Session = sessionmaker(bind=engine)
session = Session()

# --- 示例一：插入数据 (Create) ---
print("\n--- 插入数据 ---")
# 实例化模型对象
jk_rowling = Author(name='J.K. Rowling', nationality='British')
george_orwell = Author(name='George Orwell', nationality='British')
isaac_asimov = Author(name='Isaac Asimov', nationality='American')

# 将对象添加到会话中
session.add_all([jk_rowling, george_orwell, isaac_asimov])

# 插入书籍数据，通过对象关系来设置作者
book_hp = Book(title='Harry Potter', published_year=1997, author=jk_rowling)
book_1984 = Book(title='1984', published_year=1949, author=george_orwell)

session.add_all([book_hp, book_1984])

# 插入借阅人数据
borrower_alice = Borrower(name='Alice', email='alice@example.com')
borrower_bob = Borrower(name='Bob', email='bob@example.com')
session.add_all([borrower_alice, borrower_bob])

# 提交所有更改到数据库
session.commit()
print("数据已成功插入。")

# --- 示例二：查询数据 (Read) ---
print("\n--- 所有书籍和它们的作者 ---")
# ORM 方式的 JOIN 查询
# 我们可以直接通过对象的属性来查询关联数据
results = session.query(Book).join(Author).all()
for book in results:
    print(f"书籍: {book.title}, 作者: {book.author.name}")

# --- 示例三：更新和删除数据 (Update & Delete) ---
print("\n--- 更新书籍信息 ---")
# 查询要更新的对象
book_to_update = session.query(Book).filter_by(title='Harry Potter').first()
if book_to_update:
    book_to_update.published_year = 1998
    session.commit()
    print("书籍 'Harry Potter' 的出版年份已更新。")

# 再次查询，验证更新
updated_book = session.query(Book).filter_by(title='Harry Potter').first()
if updated_book:
    print(f"更新后的信息: 书籍: {updated_book.title}, 出版年份: {updated_book.published_year}")

print("\n--- 删除借阅人 ---")
# 查询要删除的对象
borrower_to_delete = session.query(Borrower).filter_by(name='Bob').first()
if borrower_to_delete:
    session.delete(borrower_to_delete)
    session.commit()
    print("借阅人 'Bob' 已被删除。")

# 再次查询借阅人列表，验证删除操作
print("\n--- 剩余的借阅人 ---")
remaining_borrowers = session.query(Borrower).all()
for borrower in remaining_borrowers:
    print(f"姓名: {borrower.name}")

# 关闭会话
session.close()
print("\n会话已关闭。")
