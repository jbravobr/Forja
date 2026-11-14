# 0038. A duplicação é medida pelo jscpd no gate 8, com a catraca do próprio jscpd

## Contexto

A Especificação prevê o `jscpd` entre as camadas transversais (linha 400) e coloca o DRY entre os predicados da biblioteca de engenharia (linha 16), mas a tabela de gates não tinha linha para duplicação e nenhum check a media. O dono apontou a lacuna em 02/11/2026. A documentação do `jscpd` 5.4.2 foi lida inteira no passo 0 da fila 13 (`docs\plano-gate-duplicacao.md`).

## Decisão

- **Um check `duplication` no gate 8** (métricas estruturais), em modo observação. A tabela da Especificação foi emendada: a linha do gate 8 passa a incluir duplicação nova.
- **A catraca é a do `jscpd`.** `--baseline-from-ref <commit pai>` e o campo `isNew` do relatório JSON. A identidade do clone é o hash de conteúdo do `jscpd`. Reprova só clone novo com um lado em arquivo de produção alterado pela tentativa.
- **Limiares globais e padrões do `jscpd`** (`minTokens` 50, `minLines` 5, `mild`, só `exact`), ajustáveis em `[checks.duplication.limits]`. O `jscpd` não tem limiar por linguagem.
- **Testes fora da medição**, pela classificação de testes do medidor.
- **Falha da ferramenta é `Inconclusive`.** A Forja não liga nenhuma porta do `jscpd`, então saída diferente de 0 é falha.
- **O texto do agente vem dos mesmos limites do check** (`orchestrator\dry_brief.py`, `checks\duplication.limit_of`), na seção `## Duplication` do pacote dos papéis que mudam produção.
- **`jscpd` é transversal:** parâmetro do `PipelineMeasurer`, não operação de adaptador. Item obrigatório do `forja doctor`; a fila 14 o instala.

## Alternativas

- **Comparar clones na Forja, com cópia privada à moda de `ComplexityCheck._before`.** Refaz o que o `jscpd` já faz e arrisca divergir dele. Rejeitado.
- **Limiar por linguagem.** O `jscpd` não tem; simular exigiria uma execução por linguagem. Rejeitado.
- **Ligar `--threshold` ou `--fail-on-new-clones`.** Misturaria o veredito do gate com falha da ferramenta. Rejeitado.
- **Medir também clones renomeados, parecidos e semânticos.** Mais ruído, sem calibração (C2 foi dispensada). Fora do escopo.
- **Gate novo na Especificação.** O gate 8 já trata de estrutura do código. Rejeitado.

## Consequências

- **A tentativa pode esconder clone** com `.gitignore`, `.ignore` ou `jscpd:ignore-start`/`jscpd:ignore-end`. Desligar o `.gitignore` varreria `.venv` e dependências. Limite aceito e documentado.
- **Duplicação antiga não reprova.** É dívida para o planejador.
- **Pontos para o E2E:** `--baseline-from-ref` em worktree ligada, clone de trabalho não commitado, globs com barra invertida no Windows.
- **Mede texto repetido, não intenção.**
- **Mudar o texto do agente exige novo `VERSION`** de `dry_brief.py`.
- **Testes.** `tests\checks\test_duplication.py`, `test_duplication_report.py`, `test_pieces.py`, `tests\orchestrator\test_dry_brief.py` e `tests\executors\test_package.py`.

O uso está em `docs/guia/gates/duplicacao.md`.
