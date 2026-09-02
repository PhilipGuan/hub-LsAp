import json  # 导入json模块，用于处理JSON数据

# pip install --upgrade charset-normalizer
# pip install pdfplumber
import pdfplumber  # 导入pdfplumber模块，用于处理PDF文件，内容读取

# 加载存储问题的JSON文件
questions = json.load(open("questions.json", encoding="utf-8"))
print("questions.json")  # 打印提示信息，显示加载的JSON文件名
print(questions[0])  # 打印第一个问题的内容

print("\n")  # 打印空行

# 打开PDF文件，单个pdf简单的内容解析，没有做格式识别
pdf = pdfplumber.open("汽车知识手册.pdf") # 以这个文件作为模拟的知识库
print("pages: ", len(pdf.pages))  # 打印提示信息，显示PDF文件的页数
print(pdf.pages[23].extract_text())  # 提取并打印第23页的文本内容，不包含格式
