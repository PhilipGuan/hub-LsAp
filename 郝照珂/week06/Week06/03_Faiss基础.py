import numpy as np
import faiss # pip install faiss-cpu
# 比较底层的向量检索工具，但不是向量数据库

import time

# ==================== 第 1 步: 数据准备 ====================
print("--- 1. 准备向量数据 ---")

# 定义向量的维度
d = 128
# 数据库中向量的数量
nb = 10000
# 查询向量的数量
nq = 10

# 生成随机的数据库向量数据 (float32 类型)
np.random.seed(1234)
xb = np.random.random((nb, d)).astype('float32')
# 生成随机的查询向量数据
xq = np.random.random((nq, d)).astype('float32')

print(f"数据准备完成。数据库向量数量: {nb}, 维度: {d}")
print(f"查询向量数量: {nq}")

# ==================== 第 2 步: 暴力检索 (IndexFlatL2) ====================
print("\n--- 2. 执行暴力检索（精确搜索）---")

# 创建一个索引，使用 L2 距离（欧式距离），这是暴力检索最简单的方式
# IndexFlatL2 会遍历所有向量进行精确计算，所以它速度慢但结果精确
index_flat = faiss.IndexFlatL2(d)
print("IndexFlatL2 创建完成。")

# 将数据添加到索引中
index_flat.add(xb)
print(f"向量数据已添加到索引中。索引中向量数量: {index_flat.ntotal}")

# 执行搜索，k=4 表示查找每个查询向量的 4 个最近邻
k = 4
start_time = time.time()
D_flat, I_flat = index_flat.search(xq, k)
end_time = time.time()

print(f"暴力检索耗时: {end_time - start_time:.4f} 秒")
print("检索结果（距离和索引）：")
print("距离 (D):")
print(D_flat)
print("索引 (I):")
print(I_flat)

# ==================== 第 3 步: PQ 检索 (IndexIVFPQ) ====================
print("\n--- 3. 执行 PQ 检索（近似搜索）---")

# 定义 PQ 索引的参数
nlist = 100  # 聚类的数量 (划分倒排索引的桶数)
m = 8        # 向量被分割的子向量数量 (m = d / 子向量维度)
             # 这里 d=128, 所以每个子向量维度为 128/8 = 16

# 创建一个倒排文件索引 (IndexIVF)，它需要一个量化器 (quantizer)
quantizer = faiss.IndexFlatL2(d)
index_pq = faiss.IndexIVFPQ(quantizer, d, nlist, m, 8) # 8 是子向量的位数

# 训练索引，这是近似索引的必要步骤
# 训练过程会根据数据进行聚类和子向量量化器的学习
print("开始训练 PQ 索引...")
index_pq.train(xb)
print("PQ 索引训练完成。")

# 将数据添加到索引中
index_pq.add(xb)
print(f"向量数据已添加到 PQ 索引中。索引中向量数量: {index_pq.ntotal}")

# 执行搜索
start_time = time.time()
D_pq, I_pq = index_pq.search(xq, k)
end_time = time.time()

print(f"PQ 检索耗时: {end_time - start_time:.4f} 秒")
print("检索结果（距离和索引）：")
print("距离 (D):")
print(D_pq)
print("索引 (I):")
print(I_pq)

# ==================== 第 4 步: 结果比较 ====================
print("\n--- 4. 比较两种检索结果 ---")
# 比较第一条查询的 top-k 结果
print("暴力检索结果（第一条查询）：", I_flat[0])
print("PQ 检索结果（第一条查询）：", I_pq[0])

# 注意，PQ 检索的结果与暴力检索可能不完全相同
# 这是因为 PQ 检索通过牺牲部分精度来换取极高的搜索速度
