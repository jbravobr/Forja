# 0039. Os tipos do Python são medidos pelo mypy, na catraca do lint

## Contexto

A tabela de adaptadores da Especificação prevê "ruff, mypy" para o lint do Python, mas o adaptador só rodava o `ruff` e nenhum gate rodava o `mypy`. Em 03/11/2026 o dono fixou o critério para o arcabouço de qualidade: ferramenta barata, confiável e, se externa, OSS. Conferido contra o critério: TypeScript (`tsc`), Java (`javac`, SpotBugs, PMD, Checkstyle), Go (compilador, `go vet`, golangci-lint) e C# (compilador, analisadores Roslyn) já analisam tipos; o Python não. O `mypy` (MIT) passa. A documentação do `mypy` 2.4.1 foi lida no passo 0 da fila 15 (`docs\plano-analise-estatica.md`); o changelog não foi lido por inteiro.

## Decisão

- **Um check `types` no gate 3**, em modo observação, separado do `lint`, que é bloqueante.
- **A catraca é a do lint, generalizada.** `LintCheck` virou uma catraca parametrizada pela operação; `TypesCheck` a reaproveita. Reprova o erro que aumentou por (arquivo, código) em arquivo mudado.
- **Operação nova, `Operation.TYPES`.** Os adaptadores respondem `NotSupported` por padrão; só o Python a oferece.
- **A configuração do repositório não vale** (substituído pelo [ADR 0041](0041-o-mypy-le-a-configuracao-do-repositorio-sem-os-plugins.md)) (`--config-file=`), por segurança (um plugin é código do repositório) e por determinismo. Quem quer opções suas as põe em `[checks.types.commands]` do `forja.toml`.
- **Sem cache, import não achado desligado, `--follow-imports=silent`, só `severity` igual a `error`.**
- **Falha é `Inconclusive`:** `mypy` ausente, saída fora de 0 e 1, JSON ilegível, saída 1 sem erro lido.
- **O `mypy` vem da Forja (ADR 0037):** entrada `wheel` fixada em 2.4.1 no manifesto, pura (`py3-none-any`), `needed_by = ["python"]`. No `doctor` é item não obrigatório.

## Alternativas

- **Copiar o `LintCheck` para os tipos.** Duplicaria a catraca, o defeito que a fila 13 mede. Rejeitado.
- **Pôr os tipos dentro do `lint`.** O `lint` é bloqueante e um check novo nasce em observação. Rejeitado.
- **Honrar a configuração do repositório.** Abre execução de código por `plugins` e deixa a base e a tentativa com opções diferentes. Rejeitado.
- **Modo estrito.** Sem calibração (C2 foi dispensada). Fora do escopo.
- **`pyright`, instalação de stubs (`--install-types`, que roda `pip`) e instrução ao agente sobre tipos.** Ninguém pediu. Fora do escopo.
- **Rodas compiladas por versão do Python.** A Forja não sabe qual Python o ambiente dela usa. Rejeitado.

## Consequências

- **`# type: ignore` e `# mypy:` da tentativa escondem erro.** O `mypy` não tem opção para ignorá-los. Limite aceito e documentado.
- **Erro novo pode vir de mudança em outro arquivo.** A catraca é por arquivo mudado.
- **Código sem anotação quase não é verificado** no modo padrão.
- **Mudança de versão do `mypy` muda mensagens**, mas a chave é o código.
- **A roda pura é mais lenta**, e as dependências dela (`pip`) não têm checksum no manifesto (limite herdado do ADR 0037).
- **Pontos para o E2E:** `--python-executable` com o ambiente virtual da execução e com instalação editável; JSON em execução limpa; código de saída no Windows.
- **Testes.** `tests\languages\python\test_adapter.py`, `tests\checks\test_layout_adapters.py`, `test_measurer.py`, `test_pieces.py`, `tests\toolchain\test_manifest.py`, `tests\cli\test_install_languages.py`, `tests\cli\test_main.py`.

O uso está em `docs/guia/gates/tipos.md`.
