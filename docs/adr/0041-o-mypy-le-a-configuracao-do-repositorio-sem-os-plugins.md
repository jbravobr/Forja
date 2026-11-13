# 0041. O mypy lê a configuração do repositório, sem os plugins

Status: proposto. Substitui a linha "A configuração do repositório não vale" do [ADR 0039](0039-os-tipos-do-python-sao-medidos-pelo-mypy-na-catraca-do-lint.md).

## Contexto

O ADR 0039 fez o `mypy` rodar com `--config-file=`, sem ler nada do repositório, por dois motivos: um `plugins` na configuração é código do repositório que o `mypy` importa e executa, e a base e a tentativa poderiam ficar com opções diferentes. A avaliação técnica de 11/11/2026 (item 24) apontou o custo: a Especificação diz que a configuração do repositório prevalece ("Regras para linters e formatadores", regra 4), e um repositório em modo estrito era medido no modo padrão, em que código sem anotação quase não é verificado.

Os dois motivos foram conferidos no código:

- **Plugins.** Só a chave `plugins` faz o `mypy` executar código do repositório. As demais chaves são opções.
- **Opções diferentes.** A medição da base roda numa cópia da worktree em que só os arquivos da linguagem voltam ao commit anterior (`checks/layout.py`, `_before`). Os arquivos de configuração do `mypy` não são arquivos de produção, então a base e a tentativa leem a mesma configuração. O implementador também não pode alterá-los: o gate 1 só lhe permite arquivos de produção e as dependências do manifesto.

## Decisão

- **O `mypy` lê uma cópia da configuração, nunca o arquivo.** A Forja procura na ordem do `mypy` (`mypy.ini`, `.mypy.ini`, `pyproject.toml`, `setup.cfg`), grava a cópia fora da worktree, na pasta de cache do adaptador, e passa o caminho em `--config-file`.
- **A cópia não leva `plugins`,** nem as chaves que fazem o `mypy` gravar relatório na worktree (`*_report`, `junit_xml`).
- **Configuração com `plugins` deixa o `types` inconclusivo** (`unsupported_construct`). Sem o plugin o `mypy` acusa erros que o projeto não tem, e a contagem não seria a do projeto. Com `on_inconclusive = "premise"`, o padrão do `types`, a tentativa segue e a lacuna vai para o resumo como suposição.
- **A cópia só é usada se disser o que o arquivo do repositório diz e nada mais.** Nome de opção é comparado sem maiúsculas, como o leitor de INI do `mypy` faz (`Plugins` num TOML é `plugins`). Nome de opção ou de seção fora do formato esperado, ou valor com caractere de controle, poderia acrescentar uma linha à cópia; nesse caso não há cópia, o `mypy` roda com os padrões e o `types` fica inconclusivo. A cópia gerada é relida com o leitor que o `mypy` usa e conferida contra as opções de origem.
- **Sem configuração, nada muda:** `--config-file=`, os padrões do `mypy`.
- **As opções da linha de comando da Forja continuam valendo sobre a configuração:** `--follow-imports=silent`, sem cache, `import-not-found` e `import-untyped` desligados, o interpretador do repositório.

## Alternativas

- **Manter o ADR 0039.** Contraria a Especificação e mede o repositório estrito como se não fosse. Rejeitado.
- **Rodar sem os plugins e contar os erros.** A contagem inclui erros que só existem pela falta do plugin; com o check bloqueante, reprovaria código certo. Rejeitado.
- **Passar o arquivo do repositório quando ele não declara `plugins`.** As chaves de relatório gravariam arquivos na worktree. Rejeitado.

## Consequências

- **Repositório com plugin do `mypy` (Django, pydantic) fica sem medição de tipos,** declarada como suposição. Antes era medido com os padrões, sem o plugin.
- **`$MYPY_CONFIG_FILE_DIR` é trocado pelo diretório do projeto** na cópia, porque a cópia mora em outro lugar.
- **O adaptador grava um arquivo** (a cópia) ao planejar o comando. O nome vem do conteúdo, então a mesma configuração cai sempre no mesmo arquivo.
- **Testes.** `tests\languages\python\test_mypy_config.py`, `tests\languages\python\test_adapter.py`, `tests\checks\test_layout_adapters.py`.

O uso está em `docs/guia/gates/tipos.md`.
