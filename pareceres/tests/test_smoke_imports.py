"""Smoke test de import dos SDKs de terceiros.

Guardrail para a classe de bug que derrubou o Gemini em produção: um pacote
ERRADO no requirements (google-generativeai em vez de google-genai) passa em
todos os testes mockados, mas quebra no primeiro uso real com ImportError.
Aqui exercitamos o caminho de import de verdade — sem mock, sem rede.
"""

from django.test import SimpleTestCase


class IntegrationImportSmokeTest(SimpleTestCase):
    def test_gemini_sdk_importavel(self):
        # Reproduz `from google import genai; genai.Client(...)` — exatamente o
        # que falhava em prod. api_key falsa não conecta, só valida o import.
        from pareceres.integrations.gemini import GeminiClient
        client = GeminiClient._make_client("fake-key-smoke")
        self.assertIsNotNone(client)

    def test_anthropic_sdk_importavel(self):
        import anthropic  # noqa: F401
        from pareceres.integrations.anthropic import AnthropicClient  # noqa: F401

    def test_document_ai_sdk_importavel(self):
        from google.cloud import documentai  # noqa: F401
        from pareceres.integrations import document_ai  # noqa: F401

    def test_auth_libs_importaveis(self):
        # PyJWT + cryptography — base da verificação do JWT do Clerk (middleware).
        import jwt
        import cryptography  # noqa: F401
        self.assertTrue(hasattr(jwt, "decode"))
