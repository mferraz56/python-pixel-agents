# Guia do Desenvolvedor

- Como rodar a aplicação localmente (veja README.md)
- Como executar testes: `pytest` no root do repositório
- Notas sobre importabilidade para geração automática de API: o `mkdocstrings` precisa importar o pacote; no ambiente local instale com `pip install -e .` ou ajuste `PYTHONPATH`.

Dicas rápidas:

- Use `mkdocs build --strict` para validar links e erros.
- Documente funções públicas com docstrings claras (estilo recomendado: Google).
