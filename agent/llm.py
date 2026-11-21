"""공통 LLM 클라이언트 (OpenAI 호환 API).

- 설정은 Streamlit Secrets(배포) 또는 환경변수(로컬)에서 읽는다. 코드에 키를 적지 않는다.
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL                    : 기본 제공자 (Gemini)
    LLM_FALLBACK_API_KEY, LLM_FALLBACK_BASE_URL, LLM_FALLBACK_MODEL : 선택, 예비 제공자 (예: Groq)
- 일시적 오류(한도 초과, 연결, 시간 초과, 서버 오류)는 재시도하고, 그래도 실패하면 예비 제공자로 넘어간다.
- 무료 티어는 입력이 제공자 서비스 개선에 쓰일 수 있으므로 가상 데이터만 보낸다(첨부3 제7조).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

from openai import (APIConnectionError, APITimeoutError, InternalServerError, OpenAI,
                    RateLimitError)

RETRYABLE = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
TIMEOUT_SEC = 30
RETRIES = 2              # 제공자당 추가 시도 횟수
BACKOFF_SEC = (1.5, 4.0)


class LLMUnavailable(RuntimeError):
    """모든 제공자가 실패했을 때. 화면에는 '잠시 후 다시 시도' 안내와 직접 입력 화면을 보여 준다."""


@dataclass(frozen=True)
class Provider:
    name: str
    api_key: str
    base_url: str
    model: str


def _setting(key: str) -> str | None:
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:          # Streamlit 밖(테스트, 스크립트)에서는 secrets가 없다
        pass
    return os.environ.get(key)


def load_providers() -> list[Provider]:
    providers = []
    for prefix, name in (("LLM_", "primary"), ("LLM_FALLBACK_", "fallback")):
        key, url, model = (_setting(prefix + k) for k in ("API_KEY", "BASE_URL", "MODEL"))
        if key and url and model:
            providers.append(Provider(name, key, url, model))
    return providers


def _extract_json(text: str) -> dict:
    """```json 블록이나 앞뒤 설명이 붙은 응답에서도 JSON 객체를 꺼낸다."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


class LLMClient:
    def __init__(self, providers: list[Provider] | None = None):
        self.providers = providers if providers is not None else load_providers()
        if not self.providers:
            raise LLMUnavailable("LLM 설정이 없습니다. Secrets에 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL을 넣어 주세요.")
        self.last_provider: str | None = None

    def _call(self, p: Provider, messages: list[dict], json_mode: bool, temperature: float,
              max_tokens: int) -> str:
        client = OpenAI(api_key=p.api_key, base_url=p.base_url, timeout=TIMEOUT_SEC, max_retries=0)
        kwargs = {"model": p.model, "messages": messages, "temperature": temperature,
                  "max_tokens": max_tokens}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        r = client.chat.completions.create(**kwargs)
        return r.choices[0].message.content or ""

    def chat(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.2,
             max_tokens: int = 1500) -> str:
        errors = []
        for p in self.providers:
            for attempt in range(RETRIES + 1):
                try:
                    text = self._call(p, messages, json_mode, temperature, max_tokens)
                    self.last_provider = p.name
                    return text
                except RETRYABLE as e:
                    errors.append(f"{p.name}: {type(e).__name__}")
                    if attempt < RETRIES:
                        time.sleep(BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)])
                except Exception as e:      # 인증 오류 등 재시도해도 안 되는 오류 → 다음 제공자
                    errors.append(f"{p.name}: {type(e).__name__}")
                    break
        raise LLMUnavailable("LLM 응답을 받지 못했습니다 (" + ", ".join(errors) + ")")

    def chat_json(self, messages: list[dict], *, temperature: float = 0.0,
                  max_tokens: int = 1500) -> dict:
        """JSON 객체로 답을 받는다. 형식이 깨지면 한 번 고쳐 달라고 다시 요청한다."""
        text = self.chat(messages, json_mode=True, temperature=temperature, max_tokens=max_tokens)
        try:
            return _extract_json(text)
        except (json.JSONDecodeError, ValueError):
            retry = messages + [{"role": "assistant", "content": text},
                                {"role": "user", "content": "방금 답을 설명 없이 올바른 JSON 객체 하나로만 다시 보내 주세요."}]
            return _extract_json(self.chat(retry, json_mode=True, temperature=0.0, max_tokens=max_tokens))


class FakeLLM:
    """테스트용. 미리 정한 답을 순서대로 돌려준다 (네트워크 없이 parser·report 테스트)."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.last_provider = "fake"

    def chat(self, messages, **_):
        self.calls.append(messages)
        return self.replies.pop(0)

    def chat_json(self, messages, **kw):
        return _extract_json(self.chat(messages, **kw))


class NoLLM:
    """LLM 설정이 없거나 쓸 수 없을 때. 호출하면 LLMUnavailable → 규칙 추출·템플릿 설명으로 넘어간다."""
    last_provider = None

    def chat(self, *a, **k):
        raise LLMUnavailable("LLM 설정 없음")

    def chat_json(self, *a, **k):
        raise LLMUnavailable("LLM 설정 없음")
