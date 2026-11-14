# 0040. Teste de propriedades por instrução ao agente e check em observação

## Contexto

Em 03/11/2026 o dono fixou que cada prática do arcabouço de qualidade vale para todas as linguagens suportadas quando fizer sentido, com ferramenta barata, confiável e, se externa, OSS. Teste de propriedades não existia na Forja. As ferramentas foram lidas no passo 0 da fila 16 (`docs\plano-teste-de-propriedades.md`); o que faltou ler está no plano.

## Decisão

- **Uma biblioteca por linguagem, como dependência de teste do repositório:** Python, Hypothesis; TypeScript, fast-check com `@fast-check/vitest`; Java, QuickTheories; Go, `rapid`; C#, FsCheck 3 com `FsCheck.Xunit`. Entram pelo mecanismo das dependências do agente (ADR 0036). Nada vai ao manifesto de ferramentas (ADR 0037).
- **O teste de propriedades é teste comum**, no executor de cada linguagem e sob os gates que já medem testes.
- **Instrução, não bloqueio** (como a voz, ADR 0035, e o DRY, ADR 0038): a seção `## Property tests` vai só ao autor de testes, com texto fixo da Forja.
- **Semente fixa na medição, onde a ferramenta permite sem editar o teste:** Python por `--hypothesis-seed` (só se o manifesto declara `hypothesis`) e Go por `RAPID_SEED` e `RAPID_NOFAILFILE`. Nas outras três a instrução manda fixar no teste. A Forja não inventa flag.
- **O check `properties`, no gate 6, em observação:** produção mudada sem teste mudado que cite a biblioteca da linguagem. Mede presença, nunca qualidade.
- **Java usa QuickTheories (decisão do dono, 06/11/2026).**

## Alternativas

- **jqwik para o Java.** A própria ferramenta pede, em texto, que agentes de IA não a usem (notas da 1.10.1 e linha na saída da 1.10), e está em manutenção mínima. A Forja é um agente escrevendo testes. Rejeitado.
- **Kotest (`kotest-property`) para o Java.** Licença Apache-2.0, sem restrição a IA e mantido. Mas a API é só Kotlin (`checkAll` é `suspend`, as formas com tipo são `inline` com `reified`), sem fontes em Java; exigiria Kotlin 2.2 ou superior no projeto. Rejeitado. A semente é fixável por `kotest.proptest.default.seed`, o que ele fazia melhor que o QuickTheories.
- **Deixar o Java sem teste de propriedades.** Rejeitado pelo dono em favor do QuickTheories.
- **Executor próprio para propriedades, `testing/quick` do Go, fuzzing nativo do Go.** Ninguém pediu ou foram avaliados e não escolhidos. Fora do escopo.
- **Bloquear a tentativa sem propriedade.** Nem toda mudança se presta a uma propriedade, e a calibração foi dispensada. Rejeitado; vale a observação.

## Consequências

- **QuickTheories está sem manutenção desde 2019.** Foi rodado em JDK 17 e 21 num programa mínimo, sem JUnit. Se quebrar num JDK ou JUnit novos, o Java perde a propriedade até haver outra biblioteca. Aceito pelo dono.
- **A semente de TypeScript, Java e C# depende do agente.** A Forja instrui e não impõe; teste aleatório sem semente pode fazer a repetição divergir.
- **Com `commands.test` no `forja.toml`, o Python perde a semente** do argv.
- **O check só mede presença.** Citar a biblioteca basta para satisfazê-lo.
- **Custo de tempo** nos testes e na mutação, medido no gate de mutação (passo 6) e no E2E.
- **Testes.** `tests\orchestrator\test_properties_brief.py`, `tests\checks\test_properties.py`, `tests\languages\python\test_adapter.py`, `tests\languages\go\test_adapter.py`, `tests\checks\test_changes.py`.

O uso está em `docs/guia/gates/teste-de-propriedades.md`.
