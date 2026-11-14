# Registros de decisão de arquitetura

Cada arquivo registra uma decisão que mudou ou completou a especificação, ou que fecha uma porta. O formato é o de Nygard: contexto, decisão e consequências. Decisões de rotina (pydantic, hatchling, ruff) não têm registro. Os planos e os documentos de medição que os registros citam (`plano-3.md`, `medicao-2.md`, "questão N do plano", "decisão N do plano") não estão mais no repositório; as citações ficam como registro do que se via na hora.

Status: Aceito, Proposto (ainda sem revisão específica) ou Substituído.

| Nº | Decisão | Status |
| --- | --- | --- |
| [0001](0001-sem-pr-a-entrega-e-a-branch-mais-o-resumo.md) | Sem PR: a entrega é a branch mais o resumo | Aceito |
| [0002](0002-uma-sessao-da-forja-por-repositorio.md) | Uma sessão da Forja por repositório | Aceito |
| [0003](0003-check-como-unidade-de-modo-e-politica.md) | Check como unidade de modo e de política | Aceito |
| [0004](0004-adaptadores-descrevem-e-o-nucleo-executa.md) | Adaptadores descrevem; o núcleo executa | Aceito |
| [0005](0005-limiares-por-linguagem-ficam-na-politica.md) | Limiares por linguagem ficam na política | Aceito |
| [0006](0006-estado-reconstruido-so-pelos-eventos.md) | Estado reconstruído só pelos eventos | Aceito |
| [0007](0007-job-object-por-chamada.md) | Job Object por chamada e identidade do processo | Aceito |
| [0008](0008-fachada-restrita-de-git.md) | Fachada restrita de git e fotografia do repositório | Aceito |
| [0009](0009-a-redacao-cobre-so-o-que-a-forja-envia.md) | A redação cobre só o que a Forja envia | Aceito |
| [0010](0010-chave-de-cache-sem-versao-de-politica-e-tipo-de-medicao.md) | Chave de cache sem versão de política nem tipo de medição | Aceito |
| [0011](0011-a-retomada-fecha-as-operacoes-abertas-com-um-efeito.md) | A retomada fecha as operações abertas com um efeito | Proposto |
| [0012](0012-nomes-das-branches.md) | Nomes das branches da execução e da tarefa | Aceito |
| [0013](0013-a-forja-nunca-faz-push.md) | A Forja nunca faz push | Aceito |
| [0014](0014-os-gates-leem-o-diff-da-tentativa-e-comparam-com-o-commit-pai.md) | Os gates leem o diff da tentativa e comparam com o commit pai | Aceito |
| [0015](0015-git-em-memoria-para-testar-o-orquestrador.md) | Git em memória para testar o orquestrador | Aceito |
| [0016](0016-claude-code-como-primeiro-executor.md) | Claude Code como primeiro executor e o runner de CLI | Aceito |
| [0017](0017-o-comando-forja-guia-e-pergunta-pouco.md) | O comando `forja` guia, pergunta pouco e confirma uma vez | Aceito (a linha sobre instalação foi substituída pelo 0037) |
| [0018](0018-a-forja-roda-no-ambiente-do-usuario-e-nao-cria-sandbox.md) | A Forja roda no ambiente do usuário e não cria sandbox | Aceito |
| [0019](0019-a-medicao-2-decide-seguir-com-ressalvas.md) | A medição 2 decide seguir, com ressalvas | Aceito |
| [0020](0020-a-mutacao-de-python-usa-motor-proprio.md) | A mutação de Python usa motor próprio | Substituído por 0021 |
| [0021](0021-a-mutacao-segue-o-unclebob-mutator.md) | A mutação das seis linguagens segue o unclebob/mutator | Aceito |
| [0022](0022-linha-nova-sem-teste-volta-ao-autor-e-o-adaptador-decide-o-teste.md) | Linha nova sem teste volta ao autor; o adaptador decide o que é teste; o lint antigo roda numa cópia | Aceito |
| [0023](0023-a-forja-e-agnostica-de-cli-uma-por-papel-e-a-diversidade-de-autor-e-so-uma-sugestao.md) | A Forja é agnóstica de CLI, com uma CLI por papel; a diversidade de autor é só uma sugestão | Aceito |
| [0024](0024-o-usuario-fornece-as-regras-do-semgrep-e-sem-elas-o-sast-nao-executa.md) | O usuário fornece as regras do Semgrep; sem elas o SAST não executa | Aceito |
| [0025](0025-a-rede-do-e2e-e-desligada-por-armadilha-de-proxy-sem-sandbox.md) | A rede do E2E é desligada por armadilha de proxy, sem sandbox | Aceito |
| [0026](0026-o-registro-de-mudanca-e-escrito-pela-forja-e-a-documentacao-tem-commits-proprios.md) | O registro de mudança é escrito pela Forja e a documentação tem commits próprios | Aceito |
| [0027](0027-a-entrada-vira-elementos-os-fatos-sao-do-dono-e-o-andaime-e-dado-do-adaptador.md) | A entrada vira elementos, os fatos são do dono e o andaime é dado do adaptador | Aceito |
| [0028](0028-a-unidade-do-grafo-e-o-diretorio-e-as-camadas-sao-declaradas-pelo-usuario.md) | A unidade do grafo é o diretório e as camadas são declaradas pelo usuário | Aceito |
| [0029](0029-o-teste-diferencial-so-executa-com-casos-declarados-no-repositorio.md) | O teste diferencial só executa com casos declarados no repositório | Aceito |
| [0030](0030-o-teste-diferencial-compara-medianas-e-poe-o-caso-instavel-em-quarentena.md) | O teste diferencial compara medianas e põe o caso instável em quarentena | Aceito |
| [0031](0031-o-diario-e-uma-projecao-do-log-fiel-ao-que-foi-escrito.md) | O diário é uma projeção do log, fiel ao que foi escrito | Aceito |
| [0032](0032-o-paralelismo-usa-escopos-disjuntos-e-a-guarda-de-git-ignora-por-nome.md) | Paralelismo por escopos disjuntos, com guarda de git por nome | Aceito |
| [0033](0033-o-adaptador-generico-de-cli-e-um-modelo-de-comando.md) | Adaptador genérico de CLI: um modelo de comando, sem código | Aceito |
| [0034](0034-a-mutacao-muta-a-funcao-inteira-sem-capacidade-incremental.md) | A mutação cobre a função inteira das funções tocadas, sem a capacidade incremental | Aceito |
| [0035](0035-a-voz-e-instrucao-e-medida-nunca-bloqueio.md) | A voz é instrução e medida, nunca bloqueio | Aceito |
| [0036](0036-dependencias-do-agente-em-ambiente-virtual-por-execucao.md) | Dependências do agente em ambiente virtual por execução | Aceito |
| [0037](0037-a-forja-instala-as-ferramentas-externas-oss-exceto-o-git.md) | A Forja instala as ferramentas externas OSS, exceto o git | Aceito |
| [0038](0038-a-duplicacao-e-medida-pelo-jscpd-no-gate-8-com-a-catraca-do-proprio-jscpd.md) | A duplicação é medida pelo jscpd no gate 8, com a catraca do próprio jscpd | Aceito |
| [0039](0039-os-tipos-do-python-sao-medidos-pelo-mypy-na-catraca-do-lint.md) | Os tipos do Python são medidos pelo mypy, na catraca do lint | Aceito (a linha sobre a configuração do repositório foi substituída pelo 0041) |
| [0040](0040-teste-de-propriedades-por-instrucao-ao-agente-e-check-em-observacao.md) | Teste de propriedades por instrução ao agente e check em observação | Aceito |
| [0041](0041-o-mypy-le-a-configuracao-do-repositorio-sem-os-plugins.md) | O mypy lê a configuração do repositório, sem os plugins | Proposto |
