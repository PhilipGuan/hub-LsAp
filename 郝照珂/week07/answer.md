# PageIndex 问答结果

- doc_id: `pi-39edbe45c2794256b57b78ee41bb939c`
- question: 这篇论文提出的 GraphRAG 方法是什么？它包含哪些主要步骤？

## Answer

## GraphRAG 方法概述

GraphRAG（Graph Retrieval-Augmented Generation）是一种**基于图的检索增强生成方法**,用于解决大规模私有文本语料上的"全局理解（global sensemaking）"问题。与传统的向量 RAG 只检索局部相关记录不同,GraphRAG 通过构建**实体知识图谱**并对整个语料进行层次化总结,从而能够回答需要理解整个数据集的问题(如"这个语料的主要主题是什么"而非"某段话讲了什么")。

其核心思路是:利用 LLM 构建知识图谱 → 划分社区 → 自底向上总结 → 用 map-reduce 方式回答查询。

## 主要步骤(Indexing Time 索引阶段)

1. **源文档 → 文本块(Source Documents → Text Chunks)**
   将语料切分为文本块。块的大小是关键设计参数:块越长,LLM 调用次数越少(成本低),但对块开头信息的召回会下降。

2. **文本块 → 实体与关系(Text Chunks → Entities & Relationships)**
   LLM 从每个文本块中抽取重要实体、实体间关系,并为它们生成简短描述;同时还可抽取"声明(claims)"(关于实体的重要事实性陈述,如日期、事件)。提示词可通过领域专属的 few-shot 示例进行定制。

3. **实体与关系 → 知识图谱(Entities & Relationships → Knowledge Graph)**
   将抽取出的实体、关系、声明实例聚合为图谱的节点和边。实体描述被汇总,关系聚合为边(重复次数作为边权重),声明同样被聚合。论文使用精确字符串匹配做实体对齐,但也可用更软性的匹配方法。

4. **知识图谱 → 图社区(Knowledge Graph → Graph Communities)**
   使用社区检测算法对图进行划分。论文采用**Leiden 算法**进行**层次化**检测,递归地在每个社区内继续检测子社区,直到叶子社区无法再细分。每一层都形成互斥且完备(collectively exhaustive)的社区划分,支持"分而治之"的全局总结。

5. **图社区 → 社区摘要(Graph Communities → Community Summaries)**
   为层次结构中的每个社区生成"报告式摘要":
   - **叶子级社区**:按节点/边的显著度(节点度数)排序,优先添加源节点、目标节点、边及相关声明的描述,直到达到 token 上限。
   - **更高级社区**:若元素摘要超出窗口,则按摘要 token 数降序排列子社区,用更短的子社区摘要替代更长的元素摘要,直到装入上下文窗口。

## 查询阶段(Query Time)

6. **社区摘要 → 社区答案 → 全局答案(Community Summaries → Community Answers → Global Answer)**
   采用 **map-reduce** 多阶段流程:
   - **准备社区摘要**:随机打乱并分块,确保相关信息分散而非集中在单一窗口。
   - **Map(生成社区答案)**:并行生成中间答案,并让 LLM 对每个答案打分(0-100,表示对回答问题的帮助程度),过滤掉 0 分的答案。
   - **Reduce(归约为全局答案)**:按帮助度分数降序排列中间答案,迭代装入上下文窗口,最终生成返回给用户的全局答案。

## 小结

GraphRAG 本质上是"**知识图谱生成 + 查询聚焦式摘要**"的结合:在索引期用 LLM 构建实体知识图谱并做层次化社区总结,在查询期通过 map-reduce 汇总相关社区摘要来回答全局性问题。论文通过 LLM-as-a-judge 评估表明,在两个约 100 万 token 的语料上,GraphRAG 在**全面性(comprehensiveness)和多样性(diversity)**方面显著优于向量 RAG,并在根级别(社区层级较高时)大幅降低了 token 成本。
