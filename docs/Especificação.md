# Forja — especificação

Oct 10, 2026 · @Rodrigo Amaro

## Visão e princípios

A Forja é uma ferramenta de linha de comando, escrita em Python, que conduz CLIs de agentes de código (Claude Code, Codex, OpenCode e outras) para criar, refatorar, manter e documentar software, deixando ao LLM apenas o trabalho probabilístico e cobrando dele fatos verificáveis. Tudo o que pode ser medido ou verificado por máquina é executado pela própria ferramenta e funciona como gate, não como instrução.

**Problema que resolve.** Arquivos de instrução (AGENTS.md, CLAUDE.md, SKILLS.md e similares) não são verificáveis: o modelo pode lê-los, ignorá-los, e ninguém fica sabendo. A Forja substitui instruções por medições que reprovam a entrega.

**Princípios**

1. **Gate, não instrução.** O que uma máquina consegue verificar vira critério de aceite automático.
2. **O determinístico mede e prova; o LLM propõe e explica.** A proposta só é aceita se a medição confirmar.
3. **Fatos, demonstração e prova.** Toda saída do LLM é afirmação com evidência, ação ou premissa declarada. Afirmação sem lastro é rejeitada.
4. **Políticas como código.** Cada predicado da biblioteca de engenharia (DRY, KISS, complexidade, princípios de *A Philosophy of Software Design*) é um objeto com racional, fragmento de prompt, detector, limiar e métrica oposta. O texto que orienta o LLM e a regra que o julga vêm da mesma fonte. Predicados são implementados em código; o TOML só ajusta valores.
5. **Métricas opostas contra Goodhart.** Nenhuma métrica é gate isolado. Reduzir complexidade não pode criar módulos rasos nem métodos *pass-through*. KISS, no sentido original de Kelly Johnson, arbitra conflitos: a solução precisa funcionar nas mãos de quem não a escreveu.
6. **Reusar o que encapsula correção difícil, construir a camada fina.** Instrumentação, mutação e parsing vêm de ferramentas maduras. Composição, fórmula, normalização e visualização são da Forja.
7. **LLM nunca aprova LLM.** Aprovação é sempre de um gate determinístico ou de uma pessoa.
8. **Agnóstica.** Independe de gateway, provedor de modelo e CLI específica. Suporta Java, Go, Python, C#, JavaScript e TypeScript.
9. **A ferramenta se submete às próprias regras.** A Forja passa pelos próprios gates, mede o próprio desempenho e prova a própria resiliência.

## Modos de operação e experiência de uso

O usuário executa um único comando, `forja`, é guiado na preparação, confirma uma vez e não interfere mais até a entrega final.

### Objetivos de execução

| Objetivo | Ponto de partida | Composição de papéis |
| --- | --- | --- |
| Refatoração | Repositório existente | Autor de testes (caracterização, se a rede de mutação for fraca) → implementador → documentador |
| Manutenção, funcionalidade ou correção | Repositório existente + descrição da mudança | Especificador → autor de testes (vermelho) → implementador (verde) → documentador |
| Engenharia reversa e documentação | Repositório existente | Arqueólogo → documentador |
| Criação a partir de ideia | Diretório vazio + ideia ou especificação | Especificador → andaime determinístico → fluxo de funcionalidade |

Depois do primeiro commit, todo repositório é "existente": a criação a partir de ideia só difere na porta de entrada.

### Preparação guiada

1. **Detecção automática:** repositório existente ou vazio, linguagens, toolchains e versões, CLIs instaladas e seu shell, terminal, README, testes, espaço em disco.
2. **Perguntas mínimas:** só o que não pode ser detectado — objetivo, entrada (descrição ou arquivo de especificação), CLI por papel (sugerindo diversidade de autor entre testes e implementação) e orçamento (tokens e tentativas).
3. **Prova de vida** de cada CLI escolhida.
4. **Resumo e confirmação única.** Esse é o ponto sem retorno: depois dele, a ferramenta não faz perguntas durante a execução. O que surgir sem evidência vira premissa declarada no resumo da execução.

**Momentos de interação.** Há apenas dois: a preparação guiada e uma nova invocação de `forja` depois de uma interrupção ou de uma parada bloqueante, quando a única pergunta é retomar ou descartar. Pausas por rede, disco ou falhas de infraestrutura são resolvidas sem perguntas: a execução aguarda e retoma sozinha, ou para com estado gravado.

**Ambiente.** A confirmação diz que a Forja roda neste terminal, o que ela impõe e o que fica com o ambiente (ADR 0018). O usuário decide seguir ou não, e a mesma declaração vai para o resumo da execução.

### Execução autônoma

Do ponto sem retorno até a entrega, a interface mostra progresso contínuo. Ao final, informa a branch da entrega e o caminho do resumo.

### Detalhes de interface

- **Retomada automática:** rodar `forja` após uma interrupção detecta a execução incompleta e pergunta apenas se deve retomar ou descartar.
- **Arquivo de execução:** as respostas da preparação são gravadas em `execucao.toml`. `forja --config execucao.toml` roda sem interação, para uso em CI.
- **Interface do usuário separada da interface do agente:** o usuário conhece `forja`; os comandos da fachada (`forja agente testar`, `forja agente medir`, `forja agente lint`) existem só para os agentes e não aparecem na ajuda principal.

## Arquitetura e componentes

O único componente probabilístico é o executor de CLI; todo o resto é código determinístico, testável e versionado. O núcleo emite eventos estruturados, e o registro e a interface são apenas consumidores desses eventos.

&#91;embedded content: componentes da Forja · núcleo, adaptadores, executores\]

O executor de CLI, em destaque, é o único ponto probabilístico; adaptadores e medidores alimentam o núcleo com evidências, e tudo atua sobre o repositório alvo por meio de worktrees.

| Componente | Responsabilidade |
| --- | --- |
| Orquestrador | Máquina de estados da execução: fases, fila de tarefas, papéis, tentativas, orçamento, retomada |
| Motor de políticas e gates | Avalia gates na ordem definida, com limiares por linguagem e métricas opostas; políticas versionadas |
| Montador de pacote | Seleciona deterministicamente evidências, políticas acionadas e escopo; aplica redação antes do envio |
| Verificador | Confere citações (arquivo:linha, métrica), escopo do diff por papel e esquema dos artefatos |
| Gestor de git | Branch da execução, worktrees por tentativa, commits atômicos com trailers, git notes, mutex de operações |
| Gerador de documentação | Registros de mudança, README, ADRs disparados por regra, resumo da execução, checagem de deriva |
| Linter de voz | Comentários, mensagens de commit e documentação: regras de Ousterhout e marcadores de texto gerado |
| Registro de eventos | JSONL append-only com checksum, intenção e efetivação; única fonte do estado para retomada e auditoria |
| Adaptadores de linguagem | Traduzem ferramentas nativas (build, testes, cobertura, mutação, lint, formatação) para o esquema único de evidências |
| Medidores próprios | Profundidade de módulo, *pass-through*, grafo de dependências e métricas de pacote (tree-sitter); acoplamento de mudança e churn (git) |
| Executores de CLI | Adaptadores por CLI: descoberta, prova de vida, invocação headless, fluxo de eventos, permissões por papel |
| Fachada do agente | Comandos `forja agente …` com saída compacta e estruturada, iguais em qualquer sistema operacional |
| Interface de terminal | Consome eventos e mostra progresso, atividade do agente e desfechos; texto simples fora de terminal interativo |

### Esquema único de evidências

Toda medição, de qualquer linguagem ou ferramenta, é normalizada num registro com: identificador, tipo de medição, alvo (arquivo, símbolo, módulo), valor, unidade, ferramenta e versão, versão da toolchain, hash da configuração efetiva, plataforma, escopo medido, hash do commit, versão das políticas e horário. O núcleo não conhece ferramentas; consome capacidades e medições declaradas pelos adaptadores (contrato 4).

## Contratos centrais

Cinco contratos ligam estado da execução, eventos persistidos, operações externas, artefatos, evidências e retomada. Eles nascem completos no incremento 0; o restante da Forja cresce por adaptadores e novos gates sem alterá-los.

Três regras atravessam todos os contratos:

- **O estado é reconstruído apenas a partir dos eventos.** Artefatos e evidências são referenciados pelos eventos e nunca interpretados para retomar.
- **Só o orquestrador muda estado**, e só por transições permitidas. Cada transição é um evento.
- **Toda operação externa tem regra de verificação e de repetição** definida antes de ser implementada.

### Contrato 1: estados e transições

Há quatro níveis aninhados: a execução contém tarefas; cada tarefa tem uma etapa por papel; cada etapa tem tentativas. A etapa aprovada é a unidade de commit, na branch da tarefa; a tarefa é a unidade de integração, e entra inteira na branch da execução ou não entra.

**Execução**

| Estado | Transições permitidas |
| --- | --- |
| `preparando` | → `confirmada` (usuário confirma) · → `descartada` |
| `confirmada` | → `medindo_base` |
| `medindo_base` | → `planejando` · → `pausada` · → `falhou` |
| `planejando` | → `executando` · → `falhou` |
| `executando` | → `fechando` (fila vazia, objetivo atingido ou orçamento esgotado) · → `pausada` · → `falhou` |
| `fechando` | → `entregue` · → `pausada` · → `falhou` |
| `pausada` | → estado anterior (causa resolvida) · → `falhou` |
| `falhou` | → estado anterior (retomada após correção pelo usuário) · → `descartada` |
| `entregue`, `descartada` | Terminais |

Uma execução encontrada em estado não terminal sem processo vivo é tratada como interrompida: a retomada roda a reconciliação do contrato 2 e volta ao estado registrado.

**Tarefa**

| Estado | Transições permitidas |
| --- | --- |
| `pendente` | → `aguardando_dependencia` · → `em_andamento` |
| `aguardando_dependencia` | → `pendente` (dependência integrada) · → `cancelada` (dependência bloqueada) |
| `em_andamento` | → `integrada` (todas as etapas aprovadas e integração verificada) · → `bloqueada` (tentativas esgotadas em alguma etapa) |
| `integrada`, `bloqueada`, `cancelada` | Terminais |

**Etapa** (um papel dentro da tarefa)

| Estado | Transições permitidas |
| --- | --- |
| `pendente` | → `em_andamento` (etapa anterior commitada) |
| `em_andamento` | → `commitada` (tentativa aprovada e commit feito na branch da tarefa) · → `esgotada` (limite de tentativas de qualidade) · → `pausada_infra` (limite de falhas de infraestrutura consecutivas) |
| `pausada_infra` | → `em_andamento` (retomada) |
| `commitada`, `esgotada` | Terminais; `esgotada` leva a tarefa a `bloqueada` |

**Tentativa** (dentro de uma etapa de papel)

| Estado | Transições permitidas |
| --- | --- |
| `criada` | → `copia_pronta` (worktree a partir da ponta da branch da tarefa e cópia de trabalho preparada) |
| `copia_pronta` | → `agente_executando` |
| `agente_executando` | → `validando` (CLI encerrou e diff aplicado ao worktree) · → `descartada` (falha ou interrupção) |
| `validando` | → `aprovada` · → `rejeitada` · → `descartada` (interrupção) |
| `aprovada` | → `commitada` |
| `rejeitada`, `descartada`, `commitada` | Terminais |

Uma tentativa `rejeitada` gera a próxima tentativa da mesma etapa com o motivo da rejeição, até o limite de qualidade. Uma tentativa `descartada` por falha de infraestrutura ou interrupção não conta nesse limite, mas conta num limite próprio de falhas de infraestrutura consecutivas por etapa e no orçamento de tokens; o consumo é registrado por chamada à CLI, não por tentativa. A tarefa vai para `integrada` quando todas as etapas estão `commitadas` e a integração passa no gate de integração.

### Contrato 2: protocolo de subprocesso e idempotência

**Protocolo de subprocesso.** Toda execução externa (CLI de agente, build, testes, ferramentas, git) passa por um único executor de processos do núcleo, dono do ciclo de vida:

1. **Início:** comando, diretório, variáveis de ambiente e tempo-limite declarados; processo criado dentro de um Job Object próprio da chamada, que encerra a árvore ao ser fechado; evento de intenção gravado antes.
2. **Fluxo:** stdout e stderr lidos de forma assíncrona e convertidos em eventos; log bruto em arquivo.
3. **Tempo-limite e cancelamento:** término gracioso, espera curta, encerramento da árvore inteira.
4. **Encerramento:** código de saída, duração e referência ao log gravados como efetivação.
5. **Recuperação:** na retomada, processos remanescentes da execução anterior são encerrados antes de qualquer reconciliação. Cada processo é identificado pelo PID e pelo horário de criação, gravados no evento de início; só se encerra o processo cuja identidade confere, para não atingir outro que reutilizou o PID.

**Gravação.** Checksum por linha protege o JSONL contra corrupção; não torna nada idempotente. O protocolo de intenção e efetivação apenas detecta operações incompletas. O que garante a retomada correta é a tabela abaixo.

| Operação | Identificador | Como verificar se já aconteceu | Seguro repetir? | Reconciliação |
| --- | --- | --- | --- | --- |
| Criar branch da execução | ID da execução no nome (`forja-run/<execução>`) | Ref existe | Sim | Recria se ausente |
| Normalização de estilo | Trailer `Stage-Id: normalize-<execução>` | Busca desse `Stage-Id` na branch da execução | Não | Registra efetivação se existir; senão, normaliza e commita de novo |
| Criar branch da tarefa | ID da tarefa no nome | Ref existe | Sim | Recria se ausente |
| Criar worktree da tentativa | ID da tentativa no caminho | `git worktree list` | Sim, após remover o parcial | Remove parcial e recria |
| Preparar cópia de trabalho | ID da tentativa | Hash do conteúdo copiado | Sim | Apaga e recopia |
| Chamada à CLI de agente | ID da tentativa | Não verificável | Não (custo e efeitos) | Descarta a tentativa; nova tentativa conta no limite de infraestrutura |
| Aplicar diff ao worktree | Hash do diff | Conteúdo do worktree igual ao esperado | Sim | Restaura o worktree e reaplica |
| Execução de ferramenta, medição ou gate | Chave de cache (contrato 5) | Evidência com a mesma chave existe | Sim | Reaproveita ou refaz |
| Commit da etapa | Trailers `Task-Id`, `Stage-Id`, `Attempt-Id` | Busca de `Stage-Id` na branch da tarefa | Não | Registra efetivação se existir; senão, refaz o commit |
| Integração da tarefa | `Task-Id` | Ponta da branch da tarefa contida na branch da execução | Não | Registra efetivação se existir; senão, integra e roda o gate de integração |
| Desfazer a integração | `Task-Id` | Ponta da branch da execução é o commit que ela tinha antes da integração | Sim | Move a branch da execução de volta a esse commit |
| Remover worktree | Caminho | Ausência no disco e no `git worktree list` | Sim | `git worktree prune` |

**Transferência entre cópia de trabalho e worktree.** A transferência é por comparação de manifestos, não por patch textual. Antes e depois da chamada à CLI, a Forja registra o manifesto da cópia: caminho, hash do conteúdo, tipo e bit de execução de cada arquivo. A diferença entre os dois é a mudança; aplicá-la significa levar o worktree ao manifesto esperado, o que é verificável e seguro de repetir.

| Caso | Regra |
| --- | --- |
| Arquivo criado, alterado ou removido | Aplicado pelo conteúdo em bytes |
| Renomeação | Remoção mais criação; o git detecta a renomeação no commit |
| Binário | Copiado em bytes; sujeito ao limite de tamanho e ao gate de escopo |
| Link simbólico criado pelo agente | Tentativa rejeitada: o link pode apontar para fora da cópia |
| Permissões | Só o bit de execução, o único que o git registra |
| Fim de linha | A cópia é gerada a partir do checkout com `.gitattributes`; mudança de fim de linha em linha não alterada reprova no gate de formatação |
| Nomes que diferem apenas em maiúsculas | Tentativa rejeitada: alguns sistemas de arquivos distinguem, outros (NTFS, APFS) não |
| Caminho longo demais para o Windows | Tentativa rejeitada, com motivo explícito |
| Conflito | Impossível por construção: o worktree é recém-criado e só a Forja escreve nele. O hash do worktree é verificado antes da aplicação; se mudou, a tentativa é descartada |

Nenhuma operação nova entra na Forja sem uma linha nessa tabela e um teste de reconciliação.

### Contrato 3: autorização, isolamento e proteção do git

A autorização é uma política separada do fluxo da tarefa. A única decisão humana acontece na preparação; depois do ponto sem retorno, toda ação é automática ou proibida.

| Classe | Ações |
| --- | --- |
| Decidida na preparação | Objetivo, CLI por papel e orçamento |
| Automática | Ler o repositório, criar e remover worktrees, executar adaptadores, enviar o pacote redigido, commit e integração na branch da execução |
| Proibida | Push para qualquer remoto e de qualquer branch, merge na branch principal, apagar branches, reescrever histórico publicado, enviar arquivos marcados como sensíveis, acesso do agente a segredos do repositório ou do ambiente |

**Fronteira de confiança.** O código executado pelo agente e o código do repositório (build, testes) são não confiáveis. A Forja distingue o que **impõe** do que **apenas detecta** e do que **deixa ao ambiente**, e declara isso (ADR 0018).

**Ambiente do usuário.** A Forja roda no terminal onde o usuário está e não cria sandbox. A infraestrutura e as permissões em volta (máquina, contêiner, máquina virtual, rede, credenciais) são responsabilidade do usuário ou da política da empresa. Nenhuma ferramenta usada pela Forja depende de WSL ou de Linux. Há um único nível, `environment`:

| Garantia | Estado |
| --- | --- |
| Ambiente filtrado por lista de permissões para a CLI | Imposta |
| Arquivos sensíveis ausentes da cópia de trabalho | Imposta |
| Ausência de `.git` na cópia (e `GIT_CEILING_DIRECTORIES`, `GIT_DIR` inexistente) | Imposta |
| Ferramentas de arquivo do agente confinadas à cópia | Imposta (pela CLI, verificado ao vivo) |
| Processos que não sobrevivem à Forja (Job Object no Windows) | Imposta |
| Uso de git em outro caminho | Detectada (fotografia antes e depois de cada chamada) |
| Leitura fora da cópia por código executado, rede e segredos do ambiente | Do ambiente |

Na preparação, a confirmação diz isso com todas as letras, o evento de confirmação registra o nível e o resumo da execução o declara. Quem precisa de contenção forte roda a Forja já dentro dela.

**Proteção do git.** O gestor de git é o único dono do índice, das refs e do histórico, e usa uma fachada restrita: só os comandos de uma lista permitida, com regras para branches protegidas e para histórico já publicado. Em ambos os níveis, o agente trabalha numa cópia sem `.git`, então um comando `git` falha por construção; no ambiente do agente, `GIT_CEILING_DIRECTORIES` e um `GIT_DIR` apontando para um caminho inexistente reforçam isso. Além disso, antes e depois de cada chamada, o gestor fotografa e compara as refs (incluindo as compartilhadas pelos worktrees), o HEAD, a branch e o índice de cada worktree, o `.git/config` e os hooks. Qualquer diferença rejeita a tentativa. No nível reduzido, essa verificação é a única defesa contra um agente que chegue ao repositório principal por caminho absoluto, e o resumo da execução registra isso.

**Credencial da CLI.** Nenhum segredo do repositório ou do ambiente chega ao agente. A credencial da própria CLI é outra coisa: é gerenciada pela CLI e pelo gateway ou provedor configurado nela, fora do escopo da Forja. A Forja não intermedia chamadas ao modelo nem cria proxy de credenciais. Em ambiente corporativo, essa credencial só serve para o gateway, que já faz autenticação, auditoria e cotas, e a rede de saída é do ambiente do usuário. A prova de vida confirma que a CLI, no ambiente em que vai rodar, está configurada e alcança o endpoint.

**Onde o código do repositório executa.** Build, testes, lint, mutação, E2E e teste diferencial executam código do repositório e, portanto, código não confiável. Tudo roda no ambiente do usuário, com o mesmo usuário, como declarado. O agente só roda comandos pela fachada `forja agente` (`lint`, `medir`, `testar`), um comando exato por vez, com ambiente reduzido; `agente_roda_testes = false` no `execucao.toml` retira o `testar`. Caches de build são do ambiente do usuário.


### Contrato 4: eventos, artefatos e evidências

São cinco registros em camadas, com papéis que não se sobrepõem:

| Formato | Responde | Usado para retomar? |
| --- | --- | --- |
| Evento | O que aconteceu, em que ordem | Sim, é a única fonte do estado |
| Artefato | O que um papel entregou ao seguinte | Não; é referenciado por eventos |
| Evidência | O que foi medido, por quem, em que contexto | Não; é referenciada por eventos e julgada por gates |
| Execução de ferramenta | Que comando rodou, com que resultado: código de saída, duração, log | Não; é a origem das evidências |
| Decisão de gate | Qual foi o julgamento, sobre quais evidências, com qual política | Não; é referenciada pelo evento de transição que ela causou |

Nenhuma informação é copiada entre formatos: o evento guarda o ID e o hash do artefato ou da evidência, nunca o conteúdo.

**Evento:** sequência, horário, ID da execução, nível (execução, tarefa, etapa, tentativa) e ID do alvo, tipo (`transicao`, `intencao`, `efetivacao`, `progresso`), estado de origem e destino quando for transição, referências a artefatos e evidências, checksum. Eventos de progresso alimentam a interface e são ignorados pela retomada.

**Cabeçalho comum de artefato:** ID, tipo, versão do esquema, papel produtor, executor (CLI e versão), ID da tentativa, commit base, hash do conteúdo, IDs e hashes dos artefatos de entrada, premissas declaradas.

**Tipos de artefato:**

| Tipo | Produzido por | Consumido por |
| --- | --- | --- |
| `achados` | Arqueólogo | Especificador, documentador |
| `criterios` | Especificador | Autor de testes, implementador, documentador |
| `manifesto_testes` | Autor de testes | Implementador, gates |
| `declaracao_mudanca` | Qualquer papel que edita arquivos | Verificador, documentador |
| `registro_mudanca` | Documentador | Resumo da execução, retomada de contexto em execuções futuras |
| `resumo_execucao` | Fechamento | Entrega (revisão humana) |

A `declaracao_mudanca` é a forma concreta do contrato de saída do LLM: afirmações com evidência, ações e premissas. Uma ação não é um comando executado pela Forja; o agente edita arquivos diretamente, e a ação é uma declaração conferida contra o diff real. Arquivos declarados diferentes dos arquivos alterados reprovam a tentativa.

**Evidência:** ID, tipo de medição, alvo (arquivo, símbolo, módulo), valor e unidade, ID da execução de ferramenta que a produziu, e o **contexto da medição**: ferramenta e versão, hash da configuração efetiva, toolchain e versão, plataforma, escopo medido, hash do commit e versão das políticas.

**Divisão dos adaptadores.** O adaptador de executor traduz a interface de um processo de agente (descoberta, argumentos, entrada, fluxo de saída, encerramento, capacidades) e não conhece linguagens. O adaptador de linguagem traduz operações do ecossistema (formatação, lint, build, testes, métricas) e não conhece CLIs de agente. Os dois só se encontram no orquestrador.

**Camadas sem circularidade.** Uma execução de ferramenta bem-sucedida produz evidências normalizadas; uma execução que falhou não produz evidência e leva o gate a `inconclusivo`. A decisão de gate referencia as evidências de entrada, o modo e a versão da política. A evidência nunca contém a decisão que a avaliou.

**Capacidades declaradas.** Cada adaptador declara as capacidades que implementa: teste filtrado por arquivo e por teste, cobertura, mutação, mutação incremental, E2E, progresso em tempo real, medidores estruturais. Uma operação não implementada devolve `nao_suportado`. O núcleo não conhece ferramentas; consome capacidades, classificações de arquivos e medições declaradas pelo adaptador, e os limiares por linguagem são valores de configuração.

**Versionamento.** Todo evento e todo artefato carregam a versão do seu esquema. Versões anteriores são convertidas no momento da leitura; uma versão posterior desconhecida impede a retomada com mensagem clara, em vez de ser interpretada errada.

**Artefato ausente ou inválido.** Artefatos ficam armazenados por hash no diretório da execução; os que viram arquivos do projeto (como os testes) também ficam nos commits. Se um artefato referenciado estiver ausente ou com hash diferente, a etapa que o produziu é invalidada e refeita como nova tentativa. O estado continua íntegro e nenhum pacote é montado sobre dados quebrados.

**Integridade do registro.** Cada linha do JSONL leva o hash da linha anterior, o que torna evidente qualquer edição posterior. Isso detecta corrupção e alteração acidental; não é prova inviolável nem mecanismo de autenticidade.

### Contrato 5: gate

Todo gate é código, recebe entradas declaradas e devolve um de quatro resultados.

**Entradas:** worktree no commit avaliado, evidências de base, evidências da tentativa, artefatos da etapa, políticas vigentes.

**Resultados:**

| Resultado | Quando | Efeito |
| --- | --- | --- |
| `aprovado` | Critério atendido | Próximo gate |
| `reprovado` | Critério violado, com motivo, números e localização | Tentativa rejeitada; motivo vai para a próxima tentativa |
| `inconclusivo` | A medição não pôde ser feita (ferramenta travou, tempo-limite, construção de linguagem não suportada) | Conforme a política do gate: bloqueia a tarefa, ou segue e vira premissa no resumo. Nunca é tratado como aprovação silenciosa nem conta como falha do agente |
| nao\_aplicavel | O adaptador não declara a capacidade exigida, ou a métrica não tem definição para aquele ecossistema | Reportado no resumo; não bloqueia nem vira premissa. Diferente de inconclusivo: não se esperava que funcionasse |

**Modo de operação por check.** O modo vale para cada *check*, a verificação individual de uma ferramenta ou detector; um gate agrupa checks, e um mesmo gate pode ter checks em modos diferentes.


- `bloqueante`: o resultado decide a tentativa.
- `observacao`: mede e reporta no resumo, mas não bloqueia. Todo check novo, ou com limiar ainda sem dados de calibração, começa neste modo e passa a bloqueante por decisão registrada na política.

**Combinação de modo e resultado.** Em `observacao`, nenhum resultado bloqueia. Em `bloqueante`, `reprovado` rejeita a tentativa, e a política para `inconclusivo` é um atributo obrigatório do check, com dois valores: `bloquear` ou `premissa`. A configuração é validada na carga; um check bloqueante sem essa política impede o início da execução.

**Padrões para detectores frágeis.** "Comentário que repete o código", "interface pública sem comentário" e os medidores de profundidade de módulo e *pass-through* começam em `observacao`, porque sua definição varia por linguagem e estilo e eles produzem falsos positivos. A voz (gate 12) **nunca bloqueia**, por decisão do dono em 31/10/2026: a Forja a mede e reporta, e busca controlá-la na origem, instruindo o agente (ver *Perfil de voz*).

**Chave de cache:** hash do commit, ferramenta e versão, hash da configuração efetiva, toolchain e versão, plataforma e escopo medido. Uma evidência só é reaproveitada se a chave inteira coincidir. A versão das políticas e o tipo de medição não entram na chave; a evidência registra a versão da política que a julgou. (Hoje só a chave e o índice existem em `core`; o reaproveitamento de medições foi adiado por decisão do dono em 29/10/2026, ver “Medições em cache” na lista de critérios.)

**Políticas e configuração.** Predicados e lógica dos gates são implementados em código. O TOML configura apenas valores: limiares por linguagem (que pertencem à política, não ao adaptador), modo de cada check, comandos e limites. Uma linguagem de regras só será considerada diante de um caso real que a justifique. O diretório em que o autor de testes escreve vem de `[tests] root` do TOML ou, na falta dele, do adaptador (Python: `tests`); é sempre um diretório dentro do repositório, nunca o próprio repositório. Linguagens com testes ao lado do código (Go, JavaScript/TypeScript) ainda precisam de congelamento por arquivo, e não por diretório, para o papel de autor de testes.

## Papéis e contratos de artefatos

Cada papel é uma chamada isolada a uma CLI, atribuída pelo orquestrador, com pacote, permissão de escrita e gate próprios. Os papéis não conversam entre si: a saída validada de um é a entrada do seguinte, sem humano no meio.

Isso não é "agentes coordenando agentes". Sub-agentes nativos das CLIs, em que o próprio modelo decide delegar, não são usados. Quem decide sequência, passagem de bastão e parada é sempre o código.

| Papel | Recebe | Só pode escrever | Artefato de saída | Gate de saída |
| --- | --- | --- | --- | --- |
| Arqueólogo | Código, evidências, histórico, fatos | Nada no código | `achados.json`: afirmações com citação e premissas | Esquema; toda citação existe e confere |
| Especificador | Ideia, especificação ou descrição; achados | Critérios de aceite | `criterios.json`: critérios numerados, verificáveis, com nível (unitário, integração, E2E) | Esquema; rastreabilidade: cada elemento da entrada vira critério, e nenhum critério fica sem origem |
| Autor de testes | Critérios congelados, interfaces | Arquivos de teste e fixtures | Testes + `manifesto-testes.json` (critério → teste, geradores de dados) | Caracterização passa no código atual; comportamento novo falha no código atual; todo critério coberto |
| Implementador | Testes congelados, evidências, pacote | Código de produção dentro do escopo | Diff + evidências re-medidas | Todos os gates do ciclo |
| Documentador | Diff, evidências, fatos, premissas | Registros de mudança, README, ADR | Documentos versionados | Citações, deriva, linter de voz, seções mínimas do README |

### Contrato de saída do LLM

Todo papel responde em formato estruturado com três tipos de conteúdo, e só esses três:

- **Afirmação com evidência:** cita arquivo:linha, métrica ou fato registrado. O verificador confere.
- **Ação:** o que foi alterado e por quê, conferido contra o diff real (contrato 4).
- **Premissa:** o que foi assumido por falta de evidência, por quê, e qual evidência faltou. Artefatos e commits que dependem dela ficam marcados.

### Diversidade de autor

A configuração por papel define qual CLI executa cada um. Autor de testes e implementador rodam em CLIs ou modelos diferentes sempre que houver mais de uma disponível, para reduzir erros correlacionados.

### Limites conhecidos

- A escrita é controlada deterministicamente pelo gate de escopo, com os padrões de arquivo de teste e de produção de cada adaptador. A leitura só é orientada pelo pacote, não bloqueada.
- Um eventual papel revisor feito por LLM só gera alertas para o resumo; nunca aprova.

## Ciclo por tarefa e gates

Cada tarefa tem uma intenção, gera um commit por etapa aprovada, é integrada inteira e passa por gates em ordem, do mais barato e eliminatório ao mais caro. Um gate só roda se o anterior passou.

### Fases de uma execução

1. **Detecção e ambiente** → perfil do repositório, versões das toolchains e das CLIs.
2. **Normalização** → formatação canônica, `.gitattributes` coerente e autofix de lint, num commit `style:` isolado, antes de qualquer tarefa.
3. **Medição de base** → build, testes executados duas vezes (testes intermitentes vão para quarentena), cobertura, mutação, lint, métricas estruturais, grafo de dependências, segurança. Gera as evidências de base.
4. **Avaliação de documentação** → README e documentos existentes, deriva. Gera as lacunas.
5. **Planejamento** → fila de tarefas atômicas (ver *Planejamento, premissas e critérios de parada*).
6. **Ciclo por tarefa** → papéis, gates, commits e registros de mudança.
7. **Fechamento** → re-medição completa, README, ADRs e resumo da execução.

### Gates, em ordem

| # | Gate | Reprova quando |
| --- | --- | --- |
| 1 | Escopo do diff | O papel alterou arquivo fora do escopo declarado ou fora do seu tipo permitido (teste/produção/documentação); o diff excede o limite de arquivos ou linhas da tarefa; arquivos declarados diferem dos alterados; HEAD, índice ou refs do git mudaram |
| 2 | Segredos | gitleaks encontra segredo no diff |
| 3 | Formatação e lint | Diff fora do formato canônico; violação nova de lint em relação à base (catraca) |
| 4 | Build | Não compila |
| 5 | Testes | Algum teste falha, exceto os em quarentena; testes congelados foram alterados |
| 6 | E2E | Falha, ou tentou acesso à rede (os E2E rodam com rede desligada) |
| 7 | Cobertura e mutação | Score de mutação do código tocado abaixo do limiar da linguagem, ou linha nova que nenhum teste executa (esta volta ao autor de testes, ver *Linha nova sem teste*) |
| 8 | Métricas estruturais | CRAP ou complexidade acima do limiar; métrica oposta piorou (*pass-through*, razão interface/implementação); duplicação nova em arquivo de produção alterado, medida pelo `jscpd` (modo observação por padrão; ADR 0038) |
| 9 | Arquitetura | Ciclo de dependência novo; violação de regra de camada; piora nas métricas de pacote |
| 10 | Segurança e dependências | Achado novo do Semgrep; vulnerabilidade ou licença proibida em dependência; dependência nova inexistente ou suspeita no registro oficial |
| 11 | Teste diferencial | Saídas da versão nova diferem da antiga para as mesmas entradas, em tarefa de refatoração; regressão de tempo ou memória acima do limite |
| 12 | Voz e comentários | Marcador de texto gerado por IA. Comentário que repete o código e interface pública sem comentário são reportados em modo observação por padrão |
| 13 | Citações | Afirmação sem evidência ou com evidência que não confere |
| 14 | Documentação | Registro de mudança ausente ou incompleto; deriva em documento existente |

Depois de integrar a tarefa na branch da execução, um **gate de integração** roda build e testes ali de novo, porque duas tarefas aprovadas isoladamente podem quebrar juntas.

### Linha nova sem teste

Quando a única razão da rejeição de uma tentativa do implementador no gate 7 é linha nova que nenhum teste executa, a tentativa não é descartada nem devolvida ao implementador: o código é commitado e um **estágio extra do autor de testes** recebe a lista das linhas. Esse estágio julga o código já commitado junto com os testes novos, mutação incluída, e, ao ser aprovado, encerra também o estágio do implementador. Ele só pode **acrescentar** arquivos de teste: alterar, mover ou apagar um teste que já existe (congelado pelo primeiro autor ou por um estágio extra anterior) reprova no gate 1. Se o estágio extra esgota as tentativas, a tarefa fica bloqueada com as linhas no motivo.

### Desfechos de uma tarefa

- **Aprovada:** etapas commitadas na branch da tarefa, registro de mudança no commit docs: da mesma tarefa, e a tarefa integrada inteira na branch da execução.
- **Rejeitada:** o worktree é descartado. A nova tentativa recebe o mesmo pacote mais o motivo exato: gate, números e localização.
- **Bloqueada:** após N tentativas, sai da fila com as evidências de cada tentativa e vai para o resumo. As demais tarefas seguem, exceto as dependentes.

**Código de saída do processo**, calculado do estado das tarefas e nunca de texto: 0 entregue com todas as tarefas integradas; 1 falhou; 2 configuração inválida; 3 pausada com estado salvo; 4 entregue, mas toda tarefa ficou bloqueada (a branch não tem mudanças); 5 entregue com pelo menos uma tarefa bloqueada e pelo menos uma integrada; 130 interrompida pelo usuário.

Falhas de infraestrutura (rede, limite de taxa) não contam como tentativa: o limite de tentativas mede qualidade, não instabilidade. Gates inconclusivos seguem a política do contrato 5.

## Adaptadores de linguagem, linters e medidores

Cada linguagem é um adaptador que traduz as ferramentas nativas para o esquema único de evidências. Adicionar uma linguagem é escrever um adaptador; o núcleo não muda.

### Contrato do adaptador

- **Detecção:** arquivos de manifesto (`pom.xml`, `build.gradle`, `go.mod`, `pyproject.toml`, `*.csproj`, `package.json` + `tsconfig.json`) e versão da toolchain.
- **Comandos:** formatar, lint (com autofix), build, testes (com filtro por arquivo e por teste), cobertura, mutação (incremental quando disponível), E2E.
- **Parsers:** saída de cada ferramenta → evidências normalizadas; progresso em tempo real para a interface.
- **Classificação de arquivos:** padrões de produção, teste, fixture e documentação, usados pelo gate de escopo.
- **Override por repositório:** arquivo de configuração da ferramenta com comandos fora do padrão. É lido por código e nunca enviado ao LLM.

### Ferramentas por linguagem

| Linguagem | Formatação | Lint | Build / testes | Cobertura | Mutação |
| --- | --- | --- | --- | --- | --- |
| Java | Spotless / google-java-format | PMD, Checkstyle, SpotBugs; autofix via OpenRewrite | Maven ou Gradle | JaCoCo | regras do mutator |
| Go | gofmt | golangci-lint | `go build` / `go test` | `-coverprofile` | regras do mutator |
| Python | ruff format | ruff, mypy | pytest | coverage.py | regras do mutator |
| C# | dotnet format | analisadores Roslyn | `dotnet build` / `dotnet test` | coverlet | regras do mutator |
| JavaScript / TypeScript | Prettier | ESLint, typescript-eslint, `tsc` | npm, jest ou vitest | c8 ou istanbul | regras do mutator |

Camadas transversais às seis linguagens: **lizard** (complexidade ciclomática), **jscpd** (duplicação), **tree-sitter** (medidores próprios), **Semgrep** (SAST), **OSV-Scanner** (vulnerabilidades em dependências) e **gitleaks** (segredos).

A mutação das seis linguagens segue o `unclebob/mutator`: sítios achados com tree-sitter pela mesma tabela de regras, um mutante por vez, o comando de testes inteiro, mutante morto quando os testes falham ou estouram o tempo (10× o baseline, no mínimo 2 s), nota = mortos / (mortos + sobreviventes), linha sem cobertura não é mutada. O mutator não tem C#; nele vale o conjunto de Java. Os limiares podem diferir por linguagem.

**O que é do mutator e o que é da Forja.** O mutator não tem limiar: ele sai com 3 quando qualquer mutante sobrevive (exige 100%), e a nota é só relatório. O limiar do gate é da Forja, por linguagem, e o check nasce em modo observação até haver dados de calibração (valores iniciais: 0,80; 0,70 para Python e Go, onde a mutação é mais frágil). Também é da Forja a regra de que **linha nova que nenhum teste executa reprova**: o mutator só a deixa fora da nota. O mutator é diferencial por função alterada; a Forja muta as linhas que a tentativa adicionou, e a mutação diferencial dos arquivos já existentes entra no incremento 7 (mutação incremental).

### Regras para linters e formatadores

1. **Formatação nunca passa pelo LLM.** Feita pela ferramenta, em commit `style:` isolado.
2. **Autofix antes do LLM.** O que é corrigível mecanicamente é corrigido; o LLM recebe só o resto.
3. **Catraca, não faxina.** Só violações novas em relação à base reprovam. Dívida antiga vira tarefa planejada.
4. **A configuração do repositório prevalece.** Perfil padrão só quando não houver nenhum.

### Medidores próprios

- **CRAP** calculado a partir de complexidade e cobertura por método, lidas dos relatórios nativos.
- **Profundidade de módulo:** razão entre interface pública e implementação.
- **Métodos *pass-through*** detectados por AST.
- **Grafo de dependências entre módulos** via imports: ciclos, instabilidade, abstração e distância da sequência principal (métricas de pacote de Robert C. Martin), conformidade de camadas.
- **Acoplamento de mudança e churn** extraídos do histórico do git.

Toda ferramenta gerada ou escrita internamente é validada contra uma ferramenta de referência num repositório conhecido antes de virar gate.

## Executores de CLI

A Forja não chama modelos diretamente: invoca CLIs de agentes em modo headless. Qual modelo, provedor ou gateway a CLI usa é configuração da própria CLI, e a ferramenta não depende disso.

### Descoberta e indicação

1. **Descoberta automática** no PATH (incluindo *shims* `.cmd` no Windows) e nos locais de instalação conhecidos de cada CLI, declarados no seu adaptador. Várias instalações → o usuário escolhe, vendo caminho e versão.
2. **Indicação manual** quando a descoberta falha, validada na hora.
3. **Precedência:** arquivo de execução ou flag → variável de ambiente (`FORJA_EXECUTOR_<NOME>`) → configuração do usuário → descoberta.
4. **Configuração do usuário fica no perfil dele** (`%APPDATA%\forja\` ou `~/.config/forja/`), nunca no repositório. Os dados das execuções (eventos, artefatos, locks) ficam em `%LOCALAPPDATA%\forja\` (ou `~/.local/share/forja/`), separados da configuração.

### Prova de vida

Antes do ponto sem retorno, cada CLI recebe uma tarefa trivial num diretório temporário, com tempo-limite. Verifica-se que ela roda sem interação, está autenticada e alcança o endpoint configurado (como o gateway corporativo), usa bash, edita arquivos e produz saída legível. O resultado fica em cache por caminho e versão; uma atualização da CLI dispara nova prova.

### Adaptador de executor

Declara: locais de descoberta, comando de versão, modelo de invocação headless, forma de entrega do pacote, leitura do fluxo de eventos, geração da configuração de permissões por papel e versão mínima suportada. Um **adaptador genérico**, configurado por modelo de comando, cobre CLIs não previstas.

### Entrega do pacote

O pacote vai **sempre por arquivo ou stdin, nunca como argumento**: a linha de comando do Windows tem limite de tamanho, e argumentos para `.cmd`/`.bat` têm histórico de falhas de escape (BatBadBut, 2024) que permitiriam injeção de comandos a partir de trechos de código.

### Shell e fachada

O LLM pensa em bash, e cada comando inválido custa tokens. A estratégia, em ordem de importância:

1. **O LLM quase não executa comandos.** Build, testes, lint, métricas e git são da ferramenta.
2. **Fachada única do agente:** `forja agente testar`, `forja agente testar --arquivo X`, `forja agente medir`, `forja agente lint`. Iguais em qualquer sistema; o Python resolve executáveis, caminhos Windows/POSIX, UTF-8, fins de linha e a conversão de caminhos do Git Bash (MSYS). A saída é compacta e estruturada: só o essencial, com arquivo:linha.
3. **Ambiente declarado no pacote:** shell, sistema operacional, comandos da fachada e diretório de trabalho.
4. **Permissões por papel** geradas para cada CLI, restringindo os comandos permitidos. A detecção falha cedo se o shell de alguma CLI não for bash.

Comandos que falharam são registrados como métrica por papel e por CLI. A correção para um erro recorrente é um comando novo na fachada, não mais instrução no prompt.

### Isolamento

A CLI roda no ambiente do usuário, no terminal onde ele está; a Forja não cria sandbox (ADR 0018) e declara o que impõe, o que detecta e o que fica com o ambiente. Todos os subprocessos ficam num Job Object do Windows para não sobreviverem à ferramenta. Os detalhes estão no contrato 3.

## Estratégia de testes

Testes são a principal prova da Forja, e por isso a estratégia ataca o maior risco: um teste errado que aprova um código errado.

### Problema do oráculo

- **Separação de funções:** testes de comportamento novo nascem numa tarefa própria, derivada dos critérios de aceite, e são congelados antes da implementação. O implementador não pode alterá-los (gate de escopo).
- **Vermelho/verde verificado:** teste de comportamento novo precisa falhar no código atual; teste de caracterização precisa passar. Senão, é rejeitado.
- **Diversidade de autor:** testes e implementação em CLIs ou modelos diferentes.
- **Rede de segurança antes de refatorar:** se o score de mutação do hotspot estiver abaixo do limiar, a primeira tarefa é escrever testes de caracterização.

### Níveis

O especificador atribui um nível a cada critério: unitário, integração ou E2E. Sempre que um critério descreve um fluxo de ponta a ponta, o E2E correspondente é obrigatório. Se o especificador declarar o E2E inviável, com justificativa, isso vira premissa no resumo.

### E2E sem ambiente real

- **Fakes antes de mocks:** implementações leves que funcionam (WireMock, WireMock.Net, `httptest`, respx/responses, msw; bancos em memória ou embarcados). Mocks só nas bordas.
- **Rede desligada:** os E2E rodam sem acesso externo. Dependência de serviço real faz o teste falhar, o que prova mecanicamente o isolamento.
- **Fakes honestos:** gerados ou validados a partir de contratos (OpenAPI, esquemas de mensagem) ou cobertos por teste de contrato (Pact). Sem contrato, a divergência possível vira premissa no resumo.
- **Determinismo:** relógio fake e sementes fixas.
- **Sem dados reais:** fixtures vêm de geradores com semente, declarados no manifesto de testes. Um scanner verifica padrões de dados pessoais (CPF, e-mail, telefone). Exigência de LGPD.

### Teste diferencial

Em refatoração e modernização, versão antiga e nova executam as mesmas entradas sintéticas, e as saídas (arquivos, tabelas, eventos) são comparadas byte a byte ou por regra declarada. O mesmo mecanismo mede tempo de execução, memória e I/O antes e depois, o que serve de gate de custo operacional (FinOps) para jobs batch. Usa os mesmos geradores e fakes dos E2E.

### Estabilidade

Na medição de base, a suíte roda duas vezes sem alterações. Testes que falham sem mudança vão para quarentena, registrados como evidência, para que o agente não seja responsabilizado por instabilidade preexistente.

## Planejamento, premissas e critérios de parada

O planejamento é determinístico: a fila de tarefas sai das medições, não de uma decisão do LLM.

### Prioridade das tarefas

- **Hotspot = complexidade × frequência de mudança** (análise de hotspots de Adam Tornhill), calculada com o histórico do git. Código complexo e estável é dívida de baixo retorno; código complexo e alterado com frequência é prioridade.
- **Lacunas de documentação** medidas na avaliação (README, deriva).
- **Violações de arquitetura** do modelo global.
- **Ordem obrigatória:** testes de caracterização antes de qualquer refatoração em código com rede fraca.

### Decomposição atômica

Uma tarefa tem uma intenção, um escopo declarado (lista de arquivos) e limites de arquivos e linhas. Dependências entre tarefas são explícitas. Tarefas com escopos disjuntos podem rodar em paralelo; as demais, em sequência.

### Critério de parada

Cada execução tem objetivos explícitos, por exemplo: nenhum hotspot acima do limiar X, README aprovado, zero violações novas. Atingidos os objetivos ou esgotado o orçamento (tokens, tentativas), a execução termina, mesmo que ainda existam melhorias possíveis.

### Premissas e fatos registrados

- Sem humano durante a execução, uma pergunta vira **premissa declarada**: o que foi assumido, por quê e que evidência faltou. Artefatos e commits dependentes ficam marcados.
- O resumo abre com a seção **Premissas assumidas**, cada uma com os commits afetados, para que o revisor rejeite só o que depende de uma premissa errada.
- Quando um humano confirma ou corrige uma premissa na revisão, isso vira **fato registrado**, com autor e data, e entra nos pacotes de execuções futuras como evidência.

### Modelo arquitetural global

A medição de base extrai o grafo de dependências entre módulos. Ele funciona como restrição para todas as tarefas: um diff que cria ciclo, viola camada ou aumenta acoplamento entre módulos é rejeitado mesmo que as métricas locais melhorem. Mudanças arquiteturais detectadas disparam ADR.

## Git, worktrees e entrega

Uma tarefa equivale a uma intenção; cada etapa aprovada equivale a um commit, e a tarefa é integrada inteira. A entrega de qualquer fluxo é a branch da execução, com o arquivo de resumo, para revisão humana; a Forja não abre pull request nem se integra a plataforma de hospedagem. A branch fica local: publicá-la é com o usuário, e a Forja só mostra, como texto, o comando `git push` que ele pode rodar. Nada é integrado automaticamente na branch principal.

### Commits atômicos

- **Conventional Commits**, validados deterministicamente.
- **Tipos não se misturam:** `test:` só toca testes e os testes novos passam no código atual; `refactor:` não toca testes e a suíte mantém o resultado; `feat:`/`fix:` exigem critério de aceite associado; `style:` só formatação; `docs:` só documentação.
- **O "porquê" é escrito pelo LLM; os fatos vêm da ferramenta em trailers:** `Task-Id`, `Evidence-Id`, métricas antes e depois, `Agent`, `Policy-Version` e, quando houver, `Assumption`.
- **Métricas detalhadas em `git notes`**, presas ao commit sem poluir a mensagem.
- **Tentativas rejeitadas nunca viram commit de código**; ficam registradas no JSONL. A Forja copia o diário da tarefa (o que cada tentativa disse e como terminou, rejeitadas inclusive) como texto em `docs/mudancas/<tarefa>-diario.md`, no commit `docs:` do documentador.

Todo commit carrega também `Stage-Id` e `Attempt-Id`, usados na reconciliação (contrato 2).

### Worktrees

A branch da execução chama-se `forja-run/<execução>` e a de cada tarefa `forja/<execução>/<tarefa>`; os nomes diferem porque o git não aceita um ref e um diretório de refs com o mesmo nome. Cada tarefa tem uma branch própria, criada a partir da ponta da branch da execução. Cada tentativa usa um worktree descartável criado a partir da ponta da branch da tarefa. O agente não trabalha no worktree: trabalha numa cópia sem `.git` (contrato 3), e a Forja aplica o diff resultante ao worktree. Aprovada, a etapa vira commit na branch da tarefa. Com todas as etapas commitadas, a tarefa é integrada inteira na branch da execução, depois do gate de integração. Worktrees de tentativas rejeitadas são removidos com `git worktree remove --force`.

- **Caminhos longos no Windows:** `core.longpaths=true` e worktrees num diretório raiz curto.
- **Fins de linha:** `.gitattributes` coerente verificado na detecção e normalizado no commit `style:`.
- **Caches de build:** os do Maven, Gradle e NuGet são compartilhados por usuário; `node_modules`, venvs e `bin/obj` são reaproveitados via cache de pacotes (pnpm, uv) ou links.
- **Paralelismo só com escopos disjuntos**, decidido deterministicamente. A versão inicial é sequencial.
- **Operações de git serializadas** por mutex baseado em criação de diretório, pois os worktrees compartilham o mesmo `.git`.

### Estrutura do resumo de entrega

1. Premissas assumidas, com os commits afetados.
2. Tarefas bloqueadas, com as evidências de cada tentativa.
3. Mudanças de comportamento (`feat`/`fix`).
4. Refatorações, com a prova (teste diferencial, mutação).
5. Documentação.
6. Métricas antes e depois, custo por tarefa e alertas.

Commits independentes e reversíveis permitem aceitar parte da execução e rejeitar o resto.

## Documentação gerada e perfil de voz

Toda criação, alteração ou exclusão feita pela Forja é documentada. A parte factual é gerada pela ferramenta; o LLM escreve só o porquê e o impacto, sob verificação de citações.

### Registro de mudança (toda tarefa)

Vai no commit docs: da mesma tarefa, integrado junto com o código. Contém: arquivos e símbolos criados, alterados ou removidos (extraídos do diff via tree-sitter), métricas antes e depois, gates aprovados, versões de toolchains e CLIs, premissas, e o hash para reverter.

O documentador é a **etapa final obrigatória** de toda tarefa que altera código: a tarefa só é integrada depois do commit `docs:` com o registro de mudança.

**Exclusões** exigem prova de que o código não é usado (busca de referências, grafo de chamadas, cobertura). Remoção de símbolo público é sinalizada explicitamente.

### README

- **Avaliação determinística:** existência; seções mínimas (o que é, como construir, testar, executar, configurar, dependências); comandos citados iguais aos que o adaptador executou com sucesso; links válidos; símbolos citados existentes.
- **LLM só onde falhou:** escreve ou reescreve apenas as seções reprovadas. README aprovado não é tocado.
- Commit próprio `docs(readme):`.

### ADR disparado por regra

Gerado quando o detector identifica mudança arquitetural: dependência externa nova, módulo novo, mudança no grafo entre módulos ou em regra de camada. O LLM redige alternativas e trade-offs; quem decide que o ADR é necessário é o detector.

### Resumo da execução

Agrega registros, tarefas rejeitadas e bloqueadas, premissas, custo e tempo por tarefa, e métricas antes e depois. É o texto da entrega e não é commitado: fica em `runs/<id>/resumo.md`, no diretório de dados da execução, para não gerar ruído no histórico.

### Deriva

Se um documento existente cita símbolo, comando ou caminho que deixou de existir, o gate reprova.

### Perfil de voz (sempre)

"Soar humano" não se prova; o que se faz é reprovar o que comprovadamente não é útil nem humano.

- **Regras de Ousterhout:** comentário que repete o código (comparação entre palavras do comentário e identificadores próximos) e interface pública sem comentário de interface são reportados, em modo observação por padrão; comentários explicam porquê, invariantes, unidades e efeitos colaterais.
- **Marcadores de texto gerado:** lista versionada e configurável de padrões proibidos (aberturas genéricas, vocabulário inflado, emojis, excesso de travessões, frases que reafirmam o óbvio). Servem para medir e reportar, nunca para reprovar uma tentativa.
- **Controle na origem:** a Forja instrui o agente a escrever sem esses padrões, pelo canal que cada CLI permitir (o pacote, para qualquer uma; arquivo de instruções, regras ou skill onde a CLI os carregar sem tocar o diff nem afrouxar o isolamento). O conteúdo da instrução segue a skill `humanizer`.
- **Mesmo perfil** para comentários, mensagens de commit e documentação; idioma configurado por repositório.

## Segurança e governança de dados

A segurança cobre três direções: o que a ferramenta envia ao LLM, o que o agente produz e o que o agente pode fazer.

### O que sai para o LLM: redação do pacote

Antes de qualquer envio, uma etapa determinística:

- remove ou mascara segredos (mesmos detectores do gitleaks);
- mascara dados pessoais em código, configuração e fixtures;
- exclui arquivos e diretórios marcados como sensíveis na configuração do repositório;
- registra nas evidências o pacote efetivamente enviado, com hash, para auditoria.

Se a redação remover algo essencial para a tarefa, isso vira premissa declarada.

A redação garante o que a Forja envia. O que a CLI consegue ler por conta própria depende do ambiente em que o usuário roda a Forja (contrato 3): as ferramentas de arquivo dela ficam confinadas à cópia, e o resto é do ambiente, como o resumo declara. Ela também não mascara o conteúdo dos arquivos da cópia de trabalho, porque o agente gravaria o marcador no código real.

### O que o agente produz

- Segredos no diff: gitleaks.
- SAST: Semgrep, com as regras que o usuário fornece; sem elas o check não executa e o resumo diz isso (ADR 0024).
- Dependências: OSV-Scanner (vulnerabilidades) e checagem de licença.
- **Dependência nova:** precisa existir no registro oficial, com idade e uso verificados, para conter pacotes inventados pelo modelo com nomes plausíveis. Dispara ADR.
- Dados reais em fixtures: scanner de padrões pessoais.

### O que o agente pode fazer

- Execução no nível de isolamento detectado na preparação (contrato 3).
- Permissões de comando por papel.
- **Injeção de prompt pelo repositório:** comentários ou documentos podem conter instruções dirigidas ao agente. O pacote separa explicitamente dado de instrução, e o gate de escopo é a rede final: diff fora do escopo é rejeitado mesmo que o agente tenha "obedecido".

## Resiliência e retomada

A perda máxima em qualquer falha (Windows, rede, energia) é uma tentativa de tarefa. Resiliência é requisito do núcleo desde a primeira versão.

### Gravação segura

- **JSONL append-only** com `fsync` e checksum por linha. Na recuperação, uma linha final truncada é descartada e o fato é registrado. Os eventos não guardam conteúdo: o payload tem até 4 KiB e cada linha de saída é cortada em 1 KiB (o texto completo fica no log bruto). O `fsync` vale para todo evento, menos os de progresso.
- **Escrita atômica** de artefatos: arquivo temporário, `fsync`, `os.replace` (atômico no NTFS no mesmo volume). Nova tentativa com espera curta quando antivírus ou indexador seguram o arquivo.

### Intenção, efeito, efetivação

Todo passo com efeito colateral grava a intenção, executa e grava a efetivação. Na retomada, intenção sem efetivação é **reconciliada com a realidade**:

- **Git é a fonte da verdade do código:** commit com o trailer `Task-Id` e Stage-Id existente comprova a etapa correspondente, e o registro é completado; a conclusão da tarefa só é comprovada pela ponta da branch da tarefa contida na branch da execução.
- Trabalho parcial em worktree é descartado, e a tentativa recomeça.
- `index.lock` órfão é removido quando não há processo git vivo; worktrees inexistentes são podados (`git worktree prune`).

### Classificação de falhas externas

| Tipo | Exemplos | Comportamento |
| --- | --- | --- |
| Transitória | Timeout, conexão recusada, limite de taxa, 5xx, falha ao baixar dependência | Nova tentativa com espera exponencial e jitter; não conta como tentativa da tarefa |
| Persistente | Sem rede por vários minutos | Execução pausada, estado gravado, verificação periódica e retomada automática |
| Bloqueante | Autenticação expirada, cota esgotada | Parada segura com mensagem clara; o próximo `forja` retoma |

Falhas de infraestrutura repetidas numa mesma etapa atingem um limite próprio de falhas consecutivas e pausam a execução (contrato 1). O consumo é registrado por chamada, para que repetições nunca fiquem invisíveis no orçamento.

### Particularidades do Windows

- **Job Object** próprio de cada chamada, para todos os subprocessos: a árvore inteira encerra junto com a ferramenta. Na retomada, procura processos remanescentes da execução anterior pela identidade gravada (PID e horário de criação).
- **Sem suspensão** durante a execução (`SetThreadExecutionState`), liberada ao final.
- **Espaço em disco** verificado no início e periodicamente; abaixo do limite, pausa.

### Exclusividade e retomada

- Lock por repositório com PID, máquina e horário; lock órfão é detectado e assumido com segurança. Há uma sessão da Forja por repositório: a segunda recebe recusa imediata ou espera com limite, conforme a política.
- Configuração congelada no arquivo da execução; atualização de CLI dispara nova prova de vida, registrada como evidência.
- Orçamento consumido persiste entre retomadas.
- Medições em cache não são refeitas quando a chave completa do contrato 5 coincide. (Adiado por decisão do dono em 29/10/2026: não construído até a Forja em uso mostrar que é necessário.)

### Prova de resiliência

A suíte da própria Forja inclui injeção de falhas: uma execução de referência é interrompida em cada fronteira de passo (antes da intenção, entre efeito e efetivação, no meio de uma gravação) e retomada. O estado final precisa ser idêntico ao de uma execução sem interrupção: mesmos commits, artefatos e registro.

## Interface de terminal e acompanhamento

A interface rica é obrigatória, e o usuário nunca fica diante de um terminal parado: sempre vê o que está acontecendo, inclusive dentro das CLIs.

### Arquitetura

A interface é consumidora de eventos e nunca decide nada; o núcleo é dono dos subprocessos e do seu ciclo de vida. Os mesmos eventos alimentam o JSONL. Consequências: interface rica na estação e texto simples no CI com o mesmo núcleo; interface testável com eventos gravados; a execução continua se a interface falhar.

### Bibliotecas

**Textual** para a interface completa, sobre **Rich** (cores truecolor, negrito e itálico, painéis, tabelas, barras de progresso, spinners, realce de sintaxe para diffs, atalhos de teclado). Ambas com licença MIT. A fonte é a do emulador de terminal; ícones de Nerd Fonts são usados quando disponíveis, com fallback para Unicode comum.

**Terminal no Windows:** o mintty do Git Bash tem problemas conhecidos com aplicações Python interativas. Recomendação: Git Bash como perfil do Windows Terminal. A detecção identifica o terminal e avisa no início se ele não suportar a interface rica.

### Conteúdo da tela

- Cabeçalho: objetivo, branch, tempo decorrido.
- Progresso da execução: tarefas concluídas sobre o total, estimativa de tempo restante.
- Tarefa atual: estado de cada papel (concluído, em andamento com tentativa, tempo e número de ações, pendente).
- Atividade do agente em tempo real: leitura e edição de arquivos (com linhas adicionadas e removidas), comandos da fachada e seus resultados.
- Última rejeição e o motivo.
- Atalhos: `d` detalhes, `l` log completo, `q` interromper com segurança.

### Percentuais só quando medidos

| Etapa | Progresso exibido |
| --- | --- |
| Execução | Tarefas concluídas sobre o total, ponderadas pela duração de execuções anteriores |
| Testes | Testes concluídos sobre o total, lidos da saída em tempo real |
| Mutação | Progresso por mutante reportado pela ferramenta |
| Build, lint, métricas | Fase atual sobre o total de fases do adaptador |
| Chamada à CLI | Sem percentual: tempo decorrido, tentativa, número de ações e atividade |

A estimativa de tempo vem do histórico do repositório; na primeira execução, a interface indica que ela é menos precisa.

### Ver dentro da CLI

1. **Fluxo estruturado:** o adaptador lê de forma assíncrona os eventos JSON que a CLI emite em modo headless (texto, chamadas de ferramenta, resultados). Os flags exatos ficam no adaptador e são verificados na prova de vida.
2. **Observação do worktree:** monitoramento do sistema de arquivos mostra cada arquivo criado ou alterado no momento em que acontece, independentemente da CLI. Mostra o que o agente fez, não o que diz ter feito.

As ferramentas determinísticas também têm saída lida em tempo real, com progresso extraído e log completo em arquivo.

## Métricas da ferramenta e ciclo de aprendizado

A Forja mede a si mesma com o mesmo rigor que aplica ao código, e o ciclo só fecha quando o resultado da revisão humana volta como evidência.

### Critérios de sucesso, definidos antes de construir

| Métrica | O que mostra |
| --- | --- |
| Taxa de commits aceitos na revisão | Qualidade percebida pelo revisor |
| Defeitos escapados após o merge | Qualidade real |
| Tempo de revisão por entrega | Custo humano da entrega |
| Custo por tarefa aceita (tempo, tentativas, tokens quando a CLI ou o gateway informarem) | Eficiência |
| Premissas confirmadas versus rejeitadas | Qualidade do julgamento sob incerteza |

Todas comparadas com uma linha de base sem a ferramenta. A percepção de ganho não serve como prova: um estudo controlado do METR (2025) encontrou desenvolvedores experientes mais lentos com ferramentas de IA, embora acreditassem estar mais rápidos.

### Métricas operacionais

Taxa de aceite, tentativas e custo por papel, por CLI e por tipo de tarefa; rejeições por gate; comandos que falharam na fachada. Com isso, a escolha entre CLIs é feita por dados.

### Coleta da revisão humana

Depois da revisão, a Forja coleta só o que o git mostra: após um `fetch`, quais commits da execução chegaram à branch principal e quais não, identificados por `Task-Id` e `Stage-Id`. Comentários do revisor e a confirmação ou correção de premissas não são lidos de nenhuma plataforma; podem entrar como um arquivo de texto que o usuário aponta, opcional. O resultado é agregado por papel, CLI, tipo de tarefa e gate, e alimenta a calibração dos limiares e a base de fatos registrados.

### Políticas versionadas

Cada evidência registra a versão das políticas que a julgaram. Mudança de limiar fica rastreável.

### Suíte de avaliação da ferramenta

Conjunto fixo de repositórios de exemplo com problemas conhecidos, nas linguagens suportadas. Toda mudança em gates, políticas, adaptadores ou versão de CLI roda contra ela antes do uso real.

## Construção da Forja

A ordem de construção e o andamento são registrados fora do repositório. O que está comprometido, aberto e do dono fica no `NOTAS.md` local, que o LLM relê antes de qualquer demanda. Em caso de divergência, vale esta especificação.

## Glossário e fora de escopo

| Termo | Definição |
| --- | --- |
| Execução | Uma invocação de `forja` do ponto sem retorno até a entrega, com branch e diretório próprios |
| Tarefa | Unidade de integração com uma intenção e escopo declarado; gera um commit por etapa aprovada e entra inteira na branch da execução |
| Tentativa | Uma passagem de um papel por uma tarefa; rejeitada, é descartada |
| Papel | Função atribuída a uma chamada de CLI: arqueólogo, especificador, autor de testes, implementador, documentador |
| Gate | Verificação determinística que aprova ou reprova uma entrega; agrupa checks |
| Check | Verificação individual de uma ferramenta ou detector, com modo e política próprios |
| Entrega | A branch da execução mais o arquivo de resumo; sem pull request nem integração com plataforma |
| Pacote de evidências | Entrada enxuta e redigida enviada ao LLM: objetivo, escopo, evidências, políticas acionadas, critérios de aceite, ambiente e contrato de saída |
| Evidência | Medição normalizada no esquema único, com ferramenta, versão e commit |
| Premissa | O que um papel assumiu por falta de evidência, declarado e rastreado até a entrega |
| Fato registrado | Premissa confirmada ou informação dada por humano, com autor e data |
| Fachada | Comandos `forja agente …` usados pelos agentes |
| Ponto sem retorno | A confirmação após a preparação guiada; daí em diante, sem perguntas |
| Catraca | Regra que reprova só violações novas em relação à base |
| Hotspot | Código com alta complexidade e alta frequência de mudança |
| Efetivação | Evento gravado após uma operação externa concluir; com a intenção, detecta operações incompletas (não é confirmação humana) |
| Inconclusivo | Resultado de gate quando a medição não pôde ser feita; tratado pela política do gate |
| Modo observação | Check que mede e reporta no resumo sem bloquear, até ter dados de calibração |
| Etapa | Um papel dentro de uma tarefa; aprovada, gera um commit na branch da tarefa |
| Nível de isolamento | `environment`; define o que a Forja impõe, o que apenas detecta e o que fica com o ambiente |
| Não aplicável | Resultado de gate quando o adaptador não declara a capacidade exigida; não bloqueia |

### Fora de escopo

- Merge automático na branch principal: a decisão é sempre humana.
- Push para qualquer remoto: publicar é sempre do usuário, nunca da Forja nem do agente.
- Intervenção humana entre papéis durante a execução.
- Chamada direta a APIs de modelos ou dependência de um gateway específico.
- Sub-agentes nativos das CLIs.
- Gates de infraestrutura como código, containers, pipelines de CI e manifestos Kubernetes (podem entrar no futuro como adaptadores do mesmo contrato).
- LLM como aprovador de qualquer gate.
