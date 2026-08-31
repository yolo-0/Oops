"""LLM response helpers shared by Anthropic-compatible providers."""
from typing import Any, Iterable, List


def extract_text_content(content: Iterable[Any]) -> str:
    """Return text blocks from Anthropic-style response content."""
    texts: List[str] = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
            continue

        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type", block_type)
            text = block.get("text", text)

        if isinstance(text, str) and (block_type in (None, "text")):
            texts.append(text)

    return "\n".join(t for t in texts if t)


async def messages_create(client: Any, **kwargs: Any) -> Any:
    """
    调用 Anthropic 兼容接口的 messages.create。

    部分第三方网关（如 DeepSeek 的 Anthropic 兼容端点）不支持 temperature 参数，
    这里在报错信息指向 temperature 参数时自动去掉 temperature 重试一次，
    保证官方 Anthropic 与第三方兼容接口都能跑通。
    注意：instructor 的兼容层可能把底层 TypeError 包装成其他异常类型，
    因此这里按异常消息判断而不是只捕获 TypeError。
    """
    try:
        return await client.messages.create(**kwargs)
    except Exception as ex:
        if "temperature" in str(ex) and "temperature" in kwargs:
            kwargs.pop("temperature")
            return await client.messages.create(**kwargs)
        raise
