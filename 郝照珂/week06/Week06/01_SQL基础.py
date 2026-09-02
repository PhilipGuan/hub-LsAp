import sqlite3 # python 自带的模块，sqlite 轻量级关系型数据库

# 连接到数据库，如果文件不存在会自动创建
conn = sqlite3.connect('library.db')
cursor = conn.cursor()

# 创建 authors 表 （csv）
cursor.execute('''
CREATE TABLE IF NOT EXISTS authors (
    author_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    nationality TEXT
);
''')

# 创建 books 表，外键关联 authors 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS books (
    book_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author_id INTEGER,
    published_year INTEGER,
    FOREIGN KEY (author_id) REFERENCES authors (author_id)
);
''')

# 创建 borrowers 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS borrowers (
    borrower_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE
);
''')

# 提交更改
conn.commit()
print("数据库和表已成功创建。")


# 插入作者数据
cursor.execute("INSERT INTO authors (name, nationality) VALUES (?, ?)", ('J.K. Rowling', 'British'))
cursor.execute("INSERT INTO authors (name, nationality) VALUES (?, ?)", ('George Orwell', 'British'))
cursor.execute("INSERT INTO authors (name, nationality) VALUES (?, ?)", ('Isaac Asimov', 'American'))
conn.commit()

# 插入书籍数据
# J.K. Rowling 的 author_id 可能是 1，我们用 SELECT 查询来获取
cursor.execute("SELECT author_id FROM authors WHERE name = 'J.K. Rowling'")
jk_rowling_id = cursor.fetchone()[0]

cursor.execute("INSERT INTO books (title, author_id, published_year) VALUES (?, ?, ?)", ('Harry Potter', jk_rowling_id, 1997))

# 插入 George Orwell 的书籍
cursor.execute("SELECT author_id FROM authors WHERE name = 'George Orwell'")
george_orwell_id = cursor.fetchone()[0]
cursor.execute("INSERT INTO books (title, author_id, published_year) VALUES (?, ?, ?)", ('1984', george_orwell_id, 1949))

conn.commit()

# 插入借阅人数据
cursor.execute("INSERT INTO borrowers (name, email) VALUES (?, ?)", ('Alice', 'alice@example.com'))
cursor.execute("INSERT INTO borrowers (name, email) VALUES (?, ?)", ('Bob', 'bob@example.com'))
conn.commit()

print("数据已成功插入。")

# 查询所有书籍及其对应的作者名字
print("\n--- 所有书籍和它们的作者 ---")
cursor.execute('''
SELECT books.title, authors.name
FROM books
JOIN authors ON books.author_id = authors.author_id;
''')

books_with_authors = cursor.fetchall()
for book, author in books_with_authors:
    print(f"书籍: {book}, 作者: {author}")

# 更新一本书的出版年份
print("\n--- 更新书籍信息 ---")
cursor.execute("UPDATE books SET published_year = ? WHERE title = ?", (1998, 'Harry Potter and the Sorcerer\'s Stone'))
conn.commit()
print("书籍 'Harry Potter and the Sorcerer\'s Stone' 的出版年份已更新。")

# 查询更新后的数据
cursor.execute("SELECT title, published_year FROM books WHERE title = 'Harry Potter'")
updated_book = cursor.fetchone()
print(f"更新后的信息: 书籍: {updated_book[0]}, 出版年份: {updated_book[1]}")

# 删除一个借阅人
print("\n--- 删除借阅人 ---")
cursor.execute("DELETE FROM borrowers WHERE name = ?", ('Bob',))
conn.commit()
print("借阅人 'Bob' 已被删除。")

# 再次查询借阅人列表，验证删除操作
print("\n--- 剩余的借阅人 ---")
cursor.execute("SELECT name FROM borrowers")
remaining_borrowers = cursor.fetchall()
for borrower in remaining_borrowers:
    print(f"姓名: {borrower[0]}")

# 关闭连接
conn.close()
print("\n数据库连接已关闭。")

# 关系型数据的使用：创建数据库、设计表（第一范式、第二范式、bc范式）、增删改查 SQL