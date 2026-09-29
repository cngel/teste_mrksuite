"""
Testes da seleção de backend de LLM (MODEL_PROVIDER) — só verificam que o
modelo certo é construído para cada provedor; não fazem pedidos de rede.
"""
from __future__ import annotations

import pytest

from app import config
from app.agent import _build_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel


@pytest.fixture
def restore_provider():
    original = config.MODEL_PROVIDER
    yield
    config.MODEL_PROVIDER = original


def test_ollama_e_o_provedor_por_omissao(restore_provider):
    config.MODEL_PROVIDER = "ollama"
    assert isinstance(_build_model(), OpenAIChatModel)


def test_groq_constroi_groq_model(restore_provider):
    from pydantic_ai.models.groq import GroqModel

    config.MODEL_PROVIDER = "groq"
    config.GROQ_API_KEY = "fake-key"
    assert isinstance(_build_model(), GroqModel)


def test_gemini_constroi_google_model(restore_provider):
    config.MODEL_PROVIDER = "gemini"
    config.GEMINI_API_KEY = "fake-key"
    assert isinstance(_build_model(), GoogleModel)


def test_anthropic_constroi_anthropic_model(restore_provider):
    config.MODEL_PROVIDER = "anthropic"
    config.ANTHROPIC_API_KEY = "fake-key"
    model = _build_model()
    assert isinstance(model, AnthropicModel)
    assert model.model_name == config.ANTHROPIC_MODEL_NAME


def test_provedor_desconhecido_levanta_erro_claro(restore_provider):
    config.MODEL_PROVIDER = "bogus"
    with pytest.raises(ValueError, match="MODEL_PROVIDER desconhecido"):
        _build_model()
