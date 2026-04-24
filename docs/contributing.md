# Contribuindo

Guidelines para contribuir com a documentação:

- Estilo de docstrings: Google style (curto resumo, parâmetros, retorno, exemplos quando cabível).
- Pequenos PRs preferíveis: um conjunto coeso (ex.: uma página ou uma referência de módulo por PR).
- Valide localmente com `mkdocs build --strict` antes de abrir PR.
- Adicione links de referência entre páginas quando relevante.

Checklist rápido:

- [ ] `mkdocs build --strict` passou localmente
- [ ] Updates em `docs/` adicionados e revisados
- [ ] Se adicionou API reference, confirme que `mkdocstrings` renderiza as assinaturas corretamente
