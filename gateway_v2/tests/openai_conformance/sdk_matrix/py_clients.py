"""Sync and async stock-SDK facades with ONE synchronous interface, so each matrix cell is
written once. Only ``base_url`` / ``api_key`` / ``http_client`` / ``max_retries`` differ from a
default client; nothing in the SDK is patched."""

from __future__ import annotations

import asyncio
from typing import Any

import openai

__all__ = ["AsyncFacade", "SyncFacade", "facade_for"]

_KEY = "sk-gw34-matrix"


class SyncFacade:
    def __init__(self, base_url: str, *, max_retries: int = 0) -> None:
        self.client = openai.OpenAI(
            base_url=f"{base_url}/v1", api_key=_KEY, max_retries=max_retries
        )

    def create(self, **kw: Any) -> Any:
        return self.client.chat.completions.create(**kw)

    def stream(self, **kw: Any) -> list[Any]:
        return list(self.client.chat.completions.create(stream=True, **kw))

    def stream_helper(self, **kw: Any) -> Any:
        with self.client.chat.completions.stream(**kw) as stream:
            for _event in stream:
                pass
            return stream.get_final_completion()

    def close_early(self, **kw: Any) -> int:
        stream = self.client.chat.completions.create(stream=True, **kw)
        count = 0
        for _chunk in stream:
            count += 1
            break
        stream.close()
        return count

    def embeddings(self, **kw: Any) -> Any:
        return self.client.embeddings.create(**kw)

    def responses_create(self, **kw: Any) -> Any:
        return self.client.responses.create(**kw)

    def responses_stream(self, **kw: Any) -> list[Any]:
        return list(self.client.responses.create(stream=True, **kw))

    def files_list(self) -> Any:
        return self.client.files.list()

    def close(self) -> None:
        self.client.close()


class AsyncFacade:
    def __init__(self, base_url: str, *, max_retries: int = 0) -> None:
        self.loop = asyncio.new_event_loop()
        self.client = openai.AsyncOpenAI(
            base_url=f"{base_url}/v1", api_key=_KEY, max_retries=max_retries
        )

    def _run(self, coro: Any) -> Any:
        return self.loop.run_until_complete(coro)

    def create(self, **kw: Any) -> Any:
        return self._run(self.client.chat.completions.create(**kw))

    def stream(self, **kw: Any) -> list[Any]:
        async def go() -> list[Any]:
            stream = await self.client.chat.completions.create(stream=True, **kw)
            return [chunk async for chunk in stream]

        return self._run(go())

    def stream_helper(self, **kw: Any) -> Any:
        async def go() -> Any:
            async with self.client.chat.completions.stream(**kw) as stream:
                async for _event in stream:
                    pass
                return await stream.get_final_completion()

        return self._run(go())

    def close_early(self, **kw: Any) -> int:
        async def go() -> int:
            stream = await self.client.chat.completions.create(stream=True, **kw)
            count = 0
            async for _chunk in stream:
                count += 1
                break
            await stream.close()
            return count

        return self._run(go())

    def embeddings(self, **kw: Any) -> Any:
        return self._run(self.client.embeddings.create(**kw))

    def responses_create(self, **kw: Any) -> Any:
        return self._run(self.client.responses.create(**kw))

    def responses_stream(self, **kw: Any) -> list[Any]:
        async def go() -> list[Any]:
            stream = await self.client.responses.create(stream=True, **kw)
            return [event async for event in stream]

        return self._run(go())

    def files_list(self) -> Any:
        return self._run(self.client.files.list())

    def close(self) -> None:
        self._run(self.client.close())
        self.loop.close()


def facade_for(client: str, base_url: str, *, max_retries: int = 0) -> SyncFacade | AsyncFacade:
    if client == "python-sync":
        return SyncFacade(base_url, max_retries=max_retries)
    return AsyncFacade(base_url, max_retries=max_retries)
