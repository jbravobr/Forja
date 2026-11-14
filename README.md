# Forja

<p align="center">
  <img src="https://img.shields.io/static/v1?label=Python&message=3.12%2B&color=blue&style=for-the-badge&logo=python"/>
  <img src="https://img.shields.io/static/v1?label=uv&message=gerenciador&color=blue&style=for-the-badge"/>
  <img src="https://img.shields.io/static/v1?label=Plataforma&message=Windows&color=lightgrey&style=for-the-badge"/>
  <img src="https://img.shields.io/static/v1?label=License&message=MIT&color=blue&style=for-the-badge"/>
  <img src="https://img.shields.io/static/v1?label=STATUS&message=EM%20DESENVOLVIMENTO&color=red&style=for-the-badge"/>
</p>

> Status do projeto: em desenvolvimento ⚠️. O que ainda falta está nas [Tarefas em aberto](#tarefas-em-aberto).

### Tópicos

- [Descrição do projeto](#descrição-do-projeto)
- [Funcionalidades](#funcionalidades)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Como rodar a aplicação](#como-rodar-a-aplicação-️)
- [Como rodar os testes](#como-rodar-os-testes)
- [Casos de uso](#casos-de-uso)
- [Configurando a execução](#configurando-a-execução)
- [Linguagens, dependências e libs utilizadas](#linguagens-dependências-e-libs-utilizadas-)
- [Resolvendo problemas](#resolvendo-problemas-)
- [Tarefas em aberto](#tarefas-em-aberto)
- [Documentação](#documentação)
- [Desenvolvedores e contribuintes](#desenvolvedores-e-contribuintes-)
- [Licença](#licença)

## Descrição do projeto

<p align="justify">
  A Forja é uma ferramenta de linha de comando, escrita em Python, que conduz CLIs de agentes de código (Claude Code, Codex, Cursor, Grok e qualquer outra descrita por você) para criar, refatorar, corrigir e documentar software. O LLM faz apenas o trabalho probabilístico, e a ferramenta exige dele fatos verificáveis.
</p>

<p align="justify">
  Tudo o que pode ser medido ou verificado por máquina é executado pela própria Forja e funciona como gate, não como instrução. A Forja substitui arquivos de instrução (AGENTS.md, CLAUDE.md e similares) por medições que reprovam a entrega.
</p>

## Funcionalidades

- ✅ ✅ Quatro objetivos: `refatorar`, `corrigir`, `investigar` e `criar`. Veja [Comandos](docs/guia/comandos.md).
- ✅ ✅ Uma branch por execução, uma worktree por tentativa e um commit por etapa. O agente trabalha numa cópia sem `.git`.
- ✅ ✅ Catorze gates: escopo, segredos, lint (formato, lint e tipos), build, testes (com vermelho/verde), e2e (com dados de teste e propriedades), mutação, métricas (complexidade, CRAP, duplicação), arquitetura, segurança, teste diferencial, voz, citações e rastreabilidade, e documentação. Veja [Gates](docs/guia/gates/README.md).
- ✅ ✅ Papéis de implementador, autor de testes, documentador, especificador e arqueólogo, cada um com a sua CLI, se você quiser. Veja [CLIs de agente](docs/guia/clis.md).
- ✅ ✅ Documentação gerada junto com cada tarefa: README, registro de mudança e registro de decisão. Veja [Documentação gerada](docs/guia/documentacao-gerada.md).
- ✅ ✅ Diário de bordo: o agente vê o histórico das tentativas e a entrega o leva junto. Veja [Diário de bordo](docs/guia/diario-de-bordo.md).
- ✅ ✅ Tarefas em paralelo, com escopos disjuntos. Veja [Paralelismo](docs/guia/paralelismo.md).
- ✅ ✅ Retomada de execuções interrompidas, sem perder o que foi salvo.
- ✅ ✅ Interface de terminal com progresso, atividade do agente, detalhes (`d`) e log (`l`); modo texto para CI. Veja [Ambiente](docs/guia/ambiente.md).
- ✅ ✅ Instalação das ferramentas externas pelo próprio comando `forja instalar`. Veja [Instalação](docs/guia/instalacao.md).

## Pré-requisitos

- [uv](https://docs.astral.sh/uv/): instala a Forja e o Python 3.12 que ela exige. O Python do sistema pode ser mais antigo, então use sempre `uv run` em vez de `python`.
- [git](https://git-scm.com/downloads): a Forja não o instala; `forja doctor` o verifica.
- A CLI do agente (por exemplo o Claude Code), com o seu login. A Forja não instala a CLI nem faz o login.
- Rede, na instalação das ferramentas externas.
- Windows x64, a única plataforma suportada. `forja instalar` também instala as ferramentas no Linux x64, mas a Forja não é testada lá. Em outras plataformas `forja instalar` não instala nada e as ferramentas ficam por sua conta.

## Instalação

O pacote ainda não está publicado em registro. A partir de uma cópia do repositório:

```bash
uv tool install .
forja doctor
forja instalar
```

`forja doctor` só verifica e diz o que falta. `forja instalar` baixa as ferramentas que faltam e, rodado de novo, não baixa o que já está instalado.

Detalhes de instalação, atualização e remoção em [Instalação](docs/guia/instalacao.md).

## Como rodar a aplicação ▶️

Para executar, entre no repositório alvo e rode:

```bash
forja
```

A Forja pergunta o que fazer, mostra a confirmação e começa.

## Como rodar os testes

```bash
uv sync                                  # cria o ambiente e instala as dependências
uv run pytest -q -m "not slow"           # suíte sem os testes lentos
uv run pytest -q                         # suíte completa
uv run ruff format . && uv run ruff check .
uv run mypy                              # modo strict
uv run python scripts/mutate.py          # gate de mutação nos arquivos alterados desde o HEAD
```

Os testes marcados com `slow` são execuções de ponta a ponta que iniciam muitos processos, como os de `tests/acceptance/test_resume.py` e `tests/orchestrator/test_resume_end_to_end.py`. Os marcadores `tools`, `integration` e `live` estão declarados em `pyproject.toml`.

## Casos de uso

Refatorar um pacote em CI, sem perguntas, com um `execucao.toml`:

```bash
forja --config execucao.toml
```

```toml
objetivo = "refatorar"
cli = "claude-code"
escopo = ["src/meuapp"]
tokens = 2000000
tarefas = 5

[interface]
modo = "texto"
```

Outros usos, cada um com exemplo em [Comandos](docs/guia/comandos.md):

- `forja corrigir` corrige um defeito a partir de uma descrição, com critérios seus ou escritos por um agente.
- `forja investigar` responde a uma pergunta sobre o código, sem alterá-lo.
- `forja criar` cria um projeto a partir de uma ideia.
- `forja --plano` mostra o plano e sai, sem escrever nada no repositório.
- `forja revisao` e `forja agente` estão descritos na mesma página.

## Configurando a execução

- `execucao.toml`: guarda as respostas da preparação (objetivo, CLI, escopo, limite de tokens, número de tarefas, paralelismo). Chave desconhecida recusa o arquivo. Todas as chaves em [execucao.toml e modo CI](docs/guia/execucao-toml.md).
- `forja.toml`: política do repositório (limiares, modo observação ou bloqueante de cada gate, comandos). Predicados e lógica ficam no código. Todas as chaves em [forja.toml](docs/guia/forja-toml.md).
- `[clis.<nome>]`: descrição de uma CLI que a Forja não conhece. Veja [CLIs de agente](docs/guia/clis.md).
- `FORJA_HOME` e as demais variáveis, pastas e arquivos que a Forja lê ou cria: [Ambiente](docs/guia/ambiente.md).

## Linguagens, dependências e libs utilizadas 📚

Linguagens que a Forja conduz: Python, TypeScript, JavaScript, Go, Java (Maven e Gradle) e C#.

Dependências de execução do pacote:

- [pydantic](https://docs.pydantic.dev/) (esquemas validados)
- [rich](https://github.com/Textualize/rich) e [Textual](https://textual.textualize.io/) (interface)
- [ruff](https://docs.astral.sh/ruff/) (lint e formato de Python)
- [lizard](https://github.com/terryyin/lizard) (complexidade)
- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) com as gramáticas das seis linguagens (mutação)

Ferramentas externas instaladas por `forja instalar`: gitleaks, osv-scanner, jscpd e Semgrep, mais as de cada linguagem detectada. Desenvolvimento: `pytest`, `mypy`, `coverage`, `pytest-xdist` e `pytest-timeout`.

## Resolvendo problemas ❗

- `forja doctor` mostra o que falta no ambiente.
- Execução incompleta bloqueia `--config` no mesmo repositório: rode `forja` sem `--config` e escolha retomar ou descartar. Veja [Comandos](docs/guia/comandos.md).
- Tarefa bloqueada por conflito de integração, com `paralelismo` acima de 1: veja [Paralelismo](docs/guia/paralelismo.md).
- Um gate reprovou e você quer entender o porquê: [Gates](docs/guia/gates/README.md).

## Tarefas em aberto

- 📝 📝 Publicar o pacote em registro (hoje a instalação é a partir do repositório).
- 📝 📝 Suporte a macOS.
- 📝 📝 A Gemini CLI está suspensa e não pode ser escolhida.

## Documentação

O [guia](docs/guia/README.md) reúne uma página por assunto, com exemplo comentado de cada comando, argumento e configuração.

- [Instalação](docs/guia/instalacao.md): instalar a Forja, `forja doctor`, `forja instalar`, atualizar e remover.
- [Comandos](docs/guia/comandos.md): todos os comandos e argumentos.
- [Ambiente](docs/guia/ambiente.md): variáveis, pastas, arquivos, teclas e temas.
- [execucao.toml e modo CI](docs/guia/execucao-toml.md): execução sem perguntas.
- [forja.toml](docs/guia/forja-toml.md): a política do repositório.
- [CLIs de agente](docs/guia/clis.md): a CLI de cada papel e o adaptador genérico.
- [Gates](docs/guia/gates/README.md): os catorze gates e uma página por assunto.
- [Documentação gerada](docs/guia/documentacao-gerada.md): o que a Forja escreve no repositório.
- [Diário de bordo](docs/guia/diario-de-bordo.md): o histórico das tentativas.
- [Paralelismo](docs/guia/paralelismo.md): tarefas ao mesmo tempo.

Convenções de desenvolvimento: código, comentários e docstrings em inglês; README em português do Brasil; `mypy` em modo strict; `ruff` com linha de 100 colunas.

## Desenvolvedores e contribuintes 🐙

O histórico de autoria está no git: `git shortlog -sn`.

## Licença

Licença MIT. O texto está em [LICENSE](LICENSE).
