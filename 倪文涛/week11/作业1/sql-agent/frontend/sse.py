"""从逐行 UTF-8 文本解析 SSE；空行分隔事件，注释仅用于心跳。"""
import json


def parse_events(lines):
    event = "message"
    data = []
    for line in lines:
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    # 未被空行结束的事件不提交；调用者通过缺少 done 判断中断。
