from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_xai import ChatXAI
from langchain.schema import AIMessage, BaseMessage
from typing import List, Optional, Literal
import logging
import requests
import json


class LLMService:
    def __init__(
        self,
        provider: Literal["xai", "openai", "openrouter"],
        model_name: str,
        api_key: str,
        base_url: Optional[str] = None,
    ):
        self.provider = provider
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.model = self._get_model()

    def _get_model(self, model_name: Optional[str] = None) -> BaseChatModel:
        """
        Создает модель. Если model_name не указан, использует self.model_name.
        Определяет провайдера по префиксу модели.
        """
        name = model_name or self.model_name
        
        # Определяем провайдера по префиксу модели
        if name.startswith("x-ai/"):
            provider = "openrouter"
        elif name.startswith("google/") or name.startswith("anthropic/") or name.startswith("meta/"):
            provider = "openrouter"
        elif name.startswith("gpt-") or name.startswith("o1-") or name.startswith("o3-"):
            provider = "openai"
        else:
            # Используем текущий провайдер по умолчанию
            provider = self.provider
        
        if provider == "openai":
            return ChatOpenAI(
                model=name,
                api_key=self.api_key
            )
        elif provider == "xai":
            return ChatXAI(
                model=name,
                api_key=self.api_key
            )
        elif provider == "openrouter":
            # Используем ChatOpenAI с base_url для OpenRouter
            return ChatOpenAI(
                model=name,
                api_key=self.api_key,
                base_url=self.base_url or "https://openrouter.ai/api/v1"
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def invoke(
        self,
        messages: List[BaseMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model_name: Optional[str] = None
    ) -> str:
        """
        Вызывает модель с указанными параметрами.
        Если model_name указан, использует эту модель вместо дефолтной.
        """
        # Если указана другая модель, создаем её динамически
        if model_name and model_name != self.model_name:
            model = self._get_model(model_name)
        else:
            model = self.model
        
        payload = {
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        
        response = model.invoke(
            messages,
            **payload
        )
        logging.info(f"LLM Response content: {getattr(response, 'content', None)}")
        if isinstance(response, AIMessage):
            return response.content
        else:
            raise ValueError(f"Unexpected response format from model: {type(response)} {response}")