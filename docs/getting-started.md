# Getting Started

Passos rápidos para desenvolver e visualizar a documentação localmente.

1. Criar e ativar um ambiente virtual

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Instalar dependências de documentação e o pacote em modo editável

```powershell
pip install --upgrade pip
pip install -r docs/requirements.txt
pip install -e .
```

3. Rodar servidor de desenvolvimento do MkDocs

```powershell
mkdocs serve
```

4. Verificar build (usado também no CI)

```powershell
mkdocs build --clean --strict
```
