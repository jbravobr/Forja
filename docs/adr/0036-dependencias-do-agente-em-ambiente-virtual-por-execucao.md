# 0036. Dependências do agente em ambiente virtual por execução

## Contexto

A execução real 5 mostrou que o agente não conseguia adicionar dependência: o gate 1 recusava o manifesto (classe "outro") e as instruções proibiam. A Especificação (linha 617) prevê a dependência nova examinada pelo gate 10, então era lacuna. O dono decidiu em 31/10/2026 que o agente deve poder usar pacotes externos, com a regra de não trazer dependência desnecessária (DRY), e que no Python a Forja usa um ambiente virtual isolado por execução.

## Decisão

- **O gate 1 aceita manifesto** para o implementador e o autor de testes, mas só a edição que muda dependências: em `pyproject.toml`, `project.dependencies`, `optional-dependencies` e `dependency-groups`; em `requirements*.txt`, linhas novas que sejam requisito simples do registro (sem URL, caminho, opção ou arquivo). Lockfile e manifestos de outras linguagens seguem recusados. O implementador edita o manifesto só dentro do escopo da tarefa; o autor de testes o edita em qualquer caminho, porque a biblioteca dos testes (Hypothesis, por exemplo) vive no manifesto da raiz, fora do diretório de testes (ajuste de 08/11/2026, achado do E2E 9).
- **As instruções trocam a proibição pela regra de necessidade**: biblioteca padrão e o que o projeto já declara primeiro; dependência nova só se o trabalho não se faz sem ela; o `reason` da ação diz por que nada disponível serve. A refatoração mantém a proibição.
- **Existência antes da instalação.** O check `dependency_install` (gate 2, bloqueante) confere cada nome novo no PyPI; nome ausente, registro sem resposta ou manifesto ilegível rejeita a tentativa sem instalar nada.
- **Ambiente virtual por execução** (`languages\python\environment.py`): `<run>\venv`, criado na primeira necessidade a partir do interpretador do repositório, com os pacotes dele visíveis por um `.pth` atrás dos instalados. Antes disso, e para quem nunca adiciona dependência, o interpretador é o do repositório. O instalador recebe só o ambiente que o `pip` precisa (`INSTALL_ENVIRONMENT`), sem chaves nem tokens.
- **Idade e uso seguem com o gate 10, depois da instalação.**

## Alternativas

- **Instalar no ambiente do repositório ou no da Forja.** Contamina o ambiente do usuário. Rejeitado.
- **`--only-binary=:all:`.** Evitaria scripts de build na instalação, mas recusa pacote só com código-fonte. Dispensado pelo dono (01/11/2026).
- **Julgar idade e uso antes de instalar.** Fecharia a janela em que o código de um pacote recém-registrado roda. Dispensado pelo dono (01/11/2026).
- **Sandbox para o instalador.** Contra o ADR 0018. Rejeitado.
- **Descartar o ambiente ao fim da execução.** A retomada precisa reencontrá-lo, e a Forja não apaga nada ao descartar uma execução. Rejeitado; o ambiente fica no diretório da execução.

## Consequências

- **Instalar executa código do pacote.** Sem sandbox, com as permissões do usuário. Um pacote recém-registrado com nome plausível passa na existência, é instalado e só depois reprovado pelo gate 10. Risco aceito pelo dono.
- **Instalação editável do próprio repositório aparece** no ambiente: o `forja-base.pth` chama `site.addsitedir` no `site-packages` base, que também processa os `.pth` dele (limite fechado na fila 12, passo 1; antes o `.pth` era só uma linha de caminho e não era recursivo). Prova em `test_an_editable_install_of_a_src_layout_repository_stays_visible`.
- **Só o Python tem ambiente.** Nas outras linguagens a dependência passa pelos gates 1 e 10 e a ferramenta da linguagem a resolve; não há E2E delas.
- **Testes.** `tests\checks\test_dependency_install.py`, `test_scope.py`, `test_new_dependency.py`, `tests\languages\test_manifests.py`, `tests\languages\python\test_environment.py`, `tests\executors\test_package.py` e `tests\cli\test_session_install_environment.py`.

O uso está em `docs/guia/gates/seguranca-e-dependencias.md`.
