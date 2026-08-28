# es_study

基于 Elasticsearch 的三大检索能力演示：**全文检索**、**条件过滤**、**向量检索**。

数据源：`Week06/汽车知识手册.pdf`（逐页提取文本后分块入库）。

## 文件说明

| 文件 | 作用 |
|------|------|
| `config.py` | 统一配置：ES 地址、索引名、数据/模型路径、分块参数 |
| `common.py` | 公共工具：连接 ES、打印结果、滑窗分块、读取 PDF |
| `build_index.py` | 读取 PDF → 分块 → 生成向量 → 创建索引 → 批量写入 ES |
| `01_full_text_search.py` | 全文检索（`match` + 内置 cjk 分词） |
| `02_filter_search.py` | 条件过滤（`bool` + `filter` 的 `range`） |
| `03_vector_search.py` | 向量检索（`knn` + `dense_vector`） |

## 运行顺序

在 `es_study` 目录下执行（需先启动 ES，且已安装 IK 分词插件）：

```powershell
# 1. 先建索引并写入数据（含向量）
python build_index.py

# 2. 再分别运行三个检索脚本
python 01_full_text_search.py
python 02_filter_search.py
python 03_vector_search.py
```

## 分块策略

- `chunk_size = 100` 字
- `overlap = 20` 字（滑窗步长 80 字）

## 测试用例

### 全文检索
- 一般：查「前排座椅通风」→ 应命中 `page_115/116/117` 相关页
- 一般：查「行车记录仪」→ 应命中 `page_275/276`
- 边界：空字符串查询；无匹配词（返回 0 条）；纯英文/数字查询

### 条件过滤
- 一般：`page_num` 范围过滤（如 1~50）
- 一般：全文检索 + `page_num` 范围过滤组合
- 边界：过滤无匹配（如 `page_num > 1000` → 0 条）；范围边界值

### 向量检索
- 一般：语义相近查询「座椅怎么通风」→ 命中通风相关页
- 一般：`k=3` 返回 top3
- 边界：`k` 大于文档总数；空查询
