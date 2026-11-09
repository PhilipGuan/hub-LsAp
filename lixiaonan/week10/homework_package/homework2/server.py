import random
import heapq
from typing import List
from fastmcp import FastMCP

mcp = FastMCP(name="homework2")

# 工具名字、传入参数类型、返回值类型、函数的注释（工具说明）


@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]

@mcp.tool
def get_news() -> str:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"

# 给定 n 个非负整数表示每个宽度为 1 的柱子的高度图，计算按此排列的柱子，下雨之后能接多少雨水。
@mcp.tool
def trap(height: List[int]) -> int:
    # 特殊情况处理
    if not height:
        return 0
        # 数组长度
    n = len(height)
    # 左侧最大边界存储结果
    left_max = [height[0]] + [0] * (n - 1)
    # 遍历存储各个位置的左侧最大边界存储结果
    for i in range(1, n):
        left_max[i] = max(left_max[i - 1], height[i])
    # 右侧最大边界存储结果
    right_max = [0] * (n - 1) + [height[n - 1]]
    # 遍历存储各个位置右侧最大边界存储结果
    for i in range(n - 2, -1, -1):
        right_max[i] = max(right_max[i + 1], height[i])
    # 结果为：先求出每个位置左右最大边界中的最小值减去当前高度，然后进行求和
    ans = sum(min(left_max[i], right_max[i]) - height[i] for i in range(n))

    return ans

# 一个整数数组 nums，有一个大小为 k 的滑动窗口从数组的最左侧移动到数组的最右侧。你只可以看到在滑动窗口内的 k 个数字。滑动窗口每次只向右移动一位。
@mcp.tool
def maxSlidingWindow(nums: List[int], k: int) -> List[int]:
    n = len(nums)
    # heapq 模块默认实现的是“小根堆”（Min Heap）——即堆顶元素永远是最小的。但我们可以通过“取反”技巧，非常方便地将其转换为“大根堆”（Max Heap）来使用
    q = [(-nums[i], i) for i in range(k)]  # 初始化存入前k个值，第一列存入值，第二列存入索引位置
    heapq.heapify(q)
    # 二元数组对列存储结果
    ans = [-q[0][0]]
    for i in range(k, n):
        # 加入新元素
        heapq.heappush(q, (-nums[i], i))
        # 通过判断下标大小，判断最大元素是否在滑动窗口中
        while (q[0][1] <= i - k):
            # 弹出最大元素
            heapq.heappop(q)
        ans.append(-q[0][0])

    return ans

# 给你一个未排序的整数数组 nums ，请你找出其中没有出现的最小的正整数。
@mcp.tool
def firstMissingPositive(nums: List[int]) -> int:
    n = len(nums)
    # 将负数替换为N+1
    for i in range(n):
        if nums[i] <= 0:
            nums[i] = n + 1

            # 遍历将所有元素
    # 如果当前元素值在0-n之间，说明当前元素值对应数组索引位置已经有对应元素，因此将其排除，排除方式为将索引对应值置为负数。
    for i in range(n):
        num = abs(nums[i])
        if num <= n:
            nums[num - 1] = -abs(nums[num - 1])

    # 寻找第一个数值不为负数的索引值
    for i in range(n):
        if nums[i] > 0:
            return i + 1

    # 若不存在，说明数组所有值均大于n+1，所以结果为n+1

    return n + 1

if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)