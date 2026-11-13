# CLIs de agente

Uma execução da Forja usa uma CLI de agente por papel. Esta página diz como escolher a CLI de cada papel, quais CLIs a Forja já conhece, onde ela procura o executável e como descrever uma CLI que ela não conhece.

Comandos e flags estão em [comandos.md](comandos.md). Variáveis de ambiente estão em [ambiente.md](ambiente.md). Todas as chaves do `execucao.toml` estão em [execucao-toml.md](execucao-toml.md).

## Escolher a CLI por papel

| Papel | CLI | Executável | Modelo |
| --- | --- | --- | --- |
| Implementador | `--cli` ou `cli` | `executavel` | `--modelo` ou `modelo` |
| Autor de testes (só em `corrigir` e `criar`) | `--cli-testes` ou `cli_testes` | `executavel_testes` | `--modelo-testes` ou `modelo_testes` |
| Documentador, especificador e arqueólogo | a do implementador | a do implementador | o do implementador |

O autor de testes só existe quando a execução escreve testes. Em `refatorar` e `investigar`, as chaves e flags `*_testes` não têm efeito. Os testes que uma refatoração escreve antes de mudar código pouco testado são escritos pela CLI do implementador.

### Implementador

A CLI do implementador vem da primeira fonte que existir:

1. `--cli`;
2. `cli` no `execucao.toml`;
3. `cli` no `config.toml` do perfil do usuário;
4. `claude-code`.

`--modelo` ou `modelo` é o nome do modelo que a Forja passa à CLI com `--model`. Sem nenhum dos dois, a Forja não passa modelo e a CLI usa o que o usuário configurou nela. Um nome de modelo não pode começar com `-`.

O `executavel` só existe como chave do `execucao.toml`; não há flag para ele.

### Autor de testes

- Se `--cli-testes` e `cli_testes` estão vazios, o autor de testes é o implementador, com a mesma CLI e o mesmo modelo.
- Se `cli_testes` nomeia uma CLI diferente da do implementador, a Forja procura o executável dela, confere a versão e prova que ela funciona, como faz com a do implementador. `executavel_testes` vale só para essa CLI.
- Se `cli_testes` é igual a `cli`, não há segundo autor: `executavel_testes` é ignorado.
- Se `modelo_testes` está preenchido e `cli_testes` não aponta para outra CLI, o autor de testes é a mesma CLI com o modelo de `modelo_testes`.

Exemplo de `execucao.toml` com implementador no Claude Code e autor de testes no Codex:

```toml
cli = "claude-code"
modelo = "opus"
cli_testes = "codex"
executavel_testes = 'C:\Users\voce\AppData\Roaming\npm\codex.cmd'
modelo_testes = "gpt-5"
```

Os valores de `modelo` e `modelo_testes` acima são exemplos: use o nome que a sua CLI aceita. Com aspas simples, o TOML não trata a barra invertida como escape, o que facilita caminhos do Windows.

Mesma CLI e outro modelo no autor de testes:

```toml
cli = "claude-code"
modelo_testes = "outro-modelo"
```

### Nomes recusados

- Uma CLI que não é conhecida nem descrita em `[clis.<nome>]` é recusada com `Não conheço a CLI` e a lista das que existem.
- Uma CLI sem instalação encontrada é recusada com `Não encontrei` e o nome dela.

## CLIs com adaptador próprio

A Forja traz adaptador para `claude-code`, `codex`, `cursor` e `grok`. `claude-code` é a padrão. A Gemini CLI está suspensa: não é registrada e não pode ser escolhida.

Tudo sobre a CLI em si (instalação, login, chave de API, modelo padrão, gateway) é do usuário e precisa estar pronto antes de rodar a Forja. A Forja só confere a versão e prova que a CLI funciona, com uma tarefa de teste: criar `probe.txt` com o conteúdo `forja-ok`. A prova é lembrada por caminho e versão do executável.

Só as variáveis de ambiente listadas abaixo chegam ao agente. As demais ficam de fora. `NO_COLOR=1` é definida em todos os adaptadores.

### `claude-code`

- Executável procurado: `claude`, no `PATH` e em `$HOME/.local/bin`, `$USERPROFILE/.local/bin` e `$APPDATA/npm`. Versão mínima: 2.0.0.
- Flags que a Forja usa: `-p`, `--output-format stream-json`, `--verbose`, `--permission-mode dontAsk`, `--setting-sources user`, `--settings` com um arquivo da Forja, `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`, `--model` (só se houver modelo), `--tools` e `--allowedTools` conforme o papel. O pacote vai pela entrada padrão.
- Só as configurações do usuário são lidas. As do repositório e os hooks não são. Hooks ficam desligados e nenhum servidor MCP é carregado.
- Variáveis repassadas: `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `FORJA_AGENT_PYTHON`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `AWS_PROFILE`, `AWS_REGION`, `AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`. As variáveis `CLAUDE_CODE_*` que um aplicativo hospedeiro define não são repassadas, exceto `CLAUDE_CODE_USE_BEDROCK` e `CLAUDE_CODE_USE_VERTEX`.
- Credenciais: a Forja repassa a chave e o token de API, o gateway, o provedor de nuvem e a pasta de configuração da CLI. Qual delas vale (login da conta ou chave) é configuração da própria CLI.
- Permissões: é a única CLI em que a Forja restringe o papel pelas ferramentas permitidas. Nas outras, o resumo da execução registra que a CLI não garante as permissões do papel.
- Consumo de tokens: reportado. O que veio do cache de entrada não é contado.

### `codex`

- Executável procurado: `codex`, no `PATH` e em `$HOME/.local/bin`, `$USERPROFILE/.local/bin` e `$APPDATA/npm`.
- Flags que a Forja usa: `exec`, `--json`, `--ephemeral`, `--ignore-rules`, `--color never`, `--cd` com o diretório de trabalho, `--model` (só se houver modelo), `--sandbox workspace-write` (papel que edita arquivos) ou `--sandbox read-only` (papel que não edita), e `-` no fim para ler o pacote da entrada padrão. Também passa quatro `-c`: `project_doc_max_bytes=0`, `features.hooks=false`, `sandbox_workspace_write.network_access=false` e `sandbox_workspace_write.writable_roots=[]`. Com isso, o `AGENTS.md` do repositório e os hooks não entram.
- Variáveis repassadas: `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `CODEX_HOME`, `CODEX_API_KEY`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`.
- Credenciais: `CODEX_HOME` é a pasta onde a CLI guarda o login do usuário, e `CODEX_API_KEY` é a chave de API. Ambas são repassadas.
- Permissões: o sandbox impede escrever fora da cópia de trabalho, mas o Codex não tem lista de comandos permitidos por papel. A Forja não impõe as permissões do papel.
- Consumo de tokens: reportado. A parte de entrada que veio do cache é descontada.

### `cursor`

- Executável procurado: `agent`, no `PATH` e em `$HOME/.local/bin` e `$USERPROFILE/.local/bin`.
- Flags que a Forja usa: `-p`, `--output-format stream-json`, `--sandbox enabled`, `--trust`, `--workspace` com o diretório de trabalho, `--model` (só se houver modelo), e `--force` (papel que edita) ou `--mode ask` (papel que não edita). O pedido é uma frase fixa que manda ler o arquivo do pacote.
- A Forja dá à CLI uma pasta de configuração própria (`CURSOR_CONFIG_DIR`, dentro de `.forja-agent`) com regras de negação: `WebFetch(*)` e `Mcp(*:*)` sempre, `Shell(*)` quando o papel não tem comandos permitidos e `Write(**)` quando o papel não edita.
- Variáveis repassadas: `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `CURSOR_API_KEY`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_USE_ENV_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`.
- Credenciais: o login do usuário não é levado, porque a pasta de configuração é a da Forja. É preciso definir `CURSOR_API_KEY`.
- Limites:
  - O Cursor não reporta tokens. O consumo aparece como desconhecido, não como zero.
  - Chamadas de shell e de edição não aparecem no que a Forja mostra. Só leitura e escrita de arquivo aparecem.
  - As regras de negação não formam lista de permissões. Um papel com comandos permitidos pode rodar qualquer comando dentro do sandbox, e a Forja não impõe as permissões do papel.
  - A configuração `.cursor/cli.json` e as regras do próprio projeto são lidas pela CLI. Não há como desligar isso.

### `grok`

- Executável procurado: `grok`, só no `PATH`. A versão é lida com o argumento `version`, não `--version`.
- Flags que a Forja usa: `--no-auto-update`, `--cwd` com o diretório de trabalho, `--output-format streaming-json`, `--no-subagents`, `--no-plan`, `--no-memory`, `--disable-web-search`, `--model` (só se houver modelo), `--permission-mode dontAsk`, `--sandbox workspace` (papel que edita) ou `--sandbox read-only` (papel que não edita) e `--prompt-file` com o arquivo do pacote.
- A Forja dá à CLI uma pasta própria (`GROK_HOME`, dentro de `.forja-agent`) com as regras do papel: leitura e busca sempre, edição se o papel edita e um `Bash` por comando permitido. Ficam negados `WebFetch`, `WebSearch`, `MCPTool` e edição da pasta `.forja-agent`. As leituras de servidores MCP e hooks de Claude e Cursor ficam desligadas.
- Variáveis repassadas: `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `XAI_API_KEY`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`.
- Credenciais: o login do usuário não é levado, porque a pasta própria é a da Forja. É preciso definir `XAI_API_KEY`.
- Limites:
  - O sandbox do Grok usa Landlock no Linux e Seatbelt no macOS. No Windows a CLI avisa que continua sem sandbox. A Forja passa o perfil mesmo assim.
  - As regras de `Bash` casam por prefixo (`Bash(pytest)` permite `pytest -x`) e tratam `*`, `?` e `[` como curinga.
  - `AGENTS.md` e `.grok/rules` do projeto são lidos. O `.grok/config.toml` do projeto pode acrescentar regras de permissão. A Forja não impõe as permissões do papel.
  - Uma chamada de shell não traz código de saída no que a Forja mostra.
  - A Forja leu o formato da saída pelo código-fonte da CLI, sem registro de uma execução real.

## Onde fica o executável

Para cada CLI, a Forja usa a primeira destas fontes que estiver preenchida:

1. `executavel` (ou `executavel_testes`) no `execucao.toml`;
2. a variável de ambiente `FORJA_EXECUTOR_<NOME>`, com o nome da CLI em maiúsculas e `_` no lugar de cada caractere que não é letra ou número (`claude-code` vira `FORJA_EXECUTOR_CLAUDE_CODE`);
3. a tabela `executaveis` do `config.toml` do perfil, que liga o nome da CLI ao caminho;
4. a descoberta: busca do nome do executável no `PATH` e nas pastas extras do adaptador, na ordem dada acima para cada CLI. No Windows, a busca tenta as extensões de `PATHEXT`.

Exemplo de `config.toml` do perfil:

```toml
cli = "codex"

[executaveis]
claude-code = 'C:\Users\voce\.local\bin\claude.exe'
codex = 'C:\Users\voce\AppData\Roaming\npm\codex.cmd'
```

O `config.toml` fica na pasta do perfil: `%APPDATA%\forja` no Windows e `$XDG_CONFIG_HOME/forja` (ou `~/.config/forja`) nos demais sistemas. `FORJA_CONFIG_HOME` troca a pasta. Sem o arquivo, vale um perfil vazio.

### Caminho dado e não encontrado

Um caminho dado pelas fontes 1, 2 ou 3 precisa apontar para um arquivo. Se não aponta, a Forja não cai para a fonte seguinte nem para a descoberta.

A Forja não transforma esse caso em mensagem. A execução termina com o erro interno do Python, que traz o texto `the executor given by <fonte> does not exist: <caminho>`, com `<fonte>` igual a `explicit` (`executavel` ou `executavel_testes`), `environment` ou `user_config`. Corrija ou remova o caminho. O mesmo vale para a sugestão de autor de testes (abaixo): uma variável `FORJA_EXECUTOR_<OUTRA>` com caminho inexistente derruba a preparação.

### Várias instalações

A descoberta pode achar mais de uma instalação da mesma CLI. Nesse caso:

- Com terminal, a Forja lista os caminhos e pergunta qual usar. Depois, para o implementador, ela oferece lembrar a escolha: se o usuário aceita, grava no `config.toml` a `cli` e o caminho em `executaveis`. O autor de testes não tem essa oferta.
- Sem terminal (por exemplo com `--config`), a Forja recusa com `Encontrei N instalações de <cli> e não há quem escolha` e lista os caminhos. A correção é dar o caminho em `executavel` (ou `executavel_testes`) no `execucao.toml`.

Um caminho dado nas fontes 1 a 3 acaba a busca: só esse caminho é considerado.

### Recusas na preparação

| Situação | O que a Forja diz |
| --- | --- |
| Nome de CLI desconhecido | `Não conheço a CLI` e a lista das conhecidas e das descritas |
| Nenhuma instalação encontrada | `Não encontrei` a CLI instalada; pede instalar e entrar na conta, e indica `forja doctor` |
| Versão abaixo da mínima ou sem resposta ao pedido de versão | recusa com o caminho do executável |
| Prova de vida falhou | `A prova de vida de <cli> falhou`, com o primeiro problema; a configuração da CLI precisa estar pronta antes |
| Várias instalações sem terminal | `Encontrei N instalações` e os caminhos |

## Sugestão de autor de testes diferente

Quando a execução escreve testes (`corrigir` ou `criar`) e o autor de testes não é outra CLI, o resumo da preparação traz uma linha como esta:

> sugestão: codex também está instalada; um agente de outra CLI nos testes dá mais diversidade de autor (`--cli-testes` ou `cli_testes` no execucao.toml)

- É só texto: não muda a CLI escolhida nem o `execucao.toml`.
- Lista as CLIs conhecidas, diferentes da do implementador, em que a Forja encontra um executável. Ela só procura o executável. A prova de vida roda apenas nas CLIs escolhidas.
- Não aparece em `refatorar` nem em `investigar`.
- Não aparece quando `cli_testes` já nomeia outra CLI.
- Aparece quando só `modelo_testes` está preenchido, porque o autor continua sendo a mesma CLI.
- Uma CLI descrita em `[clis.<nome>]` não entra na sugestão.
- Sem outra CLI conhecida instalada, não há sugestão.

## Adaptador genérico: `[clis.<nome>]`

Uma CLI que a Forja não conhece pode ser descrita só por dados: como chamá-la e de que ambiente ela precisa. Não é preciso escrever código.

### Onde declarar

- No `config.toml` do perfil.
- No `execucao.toml`.

Se o mesmo nome aparece nos dois, vale a descrição do `execucao.toml`, inteira. Os campos não se misturam entre os dois arquivos.

Um nome de CLI conhecida (`claude-code`, `codex`, `cursor`, `grok`) não pode ser usado: a Forja recusa e diz qual nome está em conflito. O nome `gemini` não está nessa lista, então é aceito.

Para usar a CLI descrita, escolha o nome dela em `cli` ou `cli_testes` (ou em `--cli` e `--cli-testes`).

### Campos

| Campo | Tipo | Padrão | O que faz |
| --- | --- | --- | --- |
| `comando` | lista de textos, com pelo menos 1 item | obrigatório | Argumentos da chamada, com os marcadores abaixo |
| `pacote_por_stdin` | `true` ou `false` | `false` | Com `true`, o pacote do papel vai pela entrada padrão em vez do marcador `{package}` |
| `executaveis` | lista de textos | vazia: usa o nome da CLI | Nomes de executável que a busca procura no `PATH` |
| `versao` | lista de textos | `["--version"]` | Argumentos que a Forja passa ao executável para ler a versão |
| `ambiente` | lista de textos | vazia | Variáveis de ambiente que a CLI pode ver, além das genéricas |

As variáveis genéricas que toda CLI descrita recebe são `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS` e `SSL_CERT_FILE`. O campo `ambiente` acrescenta outras a elas.

A tabela aceita só esses campos. Qualquer outro é erro de configuração.

### Marcadores em `comando`

| Marcador | Vira |
| --- | --- |
| `{executable}` | Caminho do executável escolhido |
| `{package}` | Caminho do arquivo com o pacote do papel |
| `{cwd}` | Diretório de trabalho do agente (a cópia dele) |
| `{role}` | Nome do papel |

Outro marcador é recusado. Um `comando` sem `{package}` e sem `pacote_por_stdin = true` também é recusado, porque o agente não receberia o pacote.

Como `comando` passa por formatação de texto, uma chave literal `{` ou `}` num argumento precisa ser escrita em dobro (`{{` e `}}`).

### Exemplo completo

Os nomes `minha-cli` e `outra-cli` são fictícios. Substitua pelos argumentos reais da sua CLI.

```toml
cli = "minha-cli"
cli_testes = "outra-cli"

# O pacote chega pelo caminho, no marcador {package}.
[clis.minha-cli]
comando = ["{executable}", "run", "--cwd", "{cwd}", "--role", "{role}", "--arquivo", "{package}"]
executaveis = ["minha-cli", "minha-cli-beta"]
versao = ["--version"]
ambiente = ["MINHA_CLI_HOME", "MINHA_CLI_API_KEY"]

# Sem {package}: o pacote chega pela entrada padrão.
[clis.outra-cli]
comando = ["{executable}", "exec", "--dir", "{cwd}"]
pacote_por_stdin = true
```

### Como a CLI recebe o pacote

O pacote do papel é um arquivo. Ele nunca vai como argumento de texto. No adaptador genérico há duas formas:

- `{package}` no `comando`: a CLI recebe o caminho do arquivo e lê por conta própria.
- `pacote_por_stdin = true`: a Forja envia o conteúdo do arquivo pela entrada padrão da CLI.

Não há outro canal. A CLI genérica recebe só o pacote.

### O que acontece na execução

- Prova de vida: a Forja pede à CLI que crie `probe.txt` com o conteúdo `forja-ok`. Só o arquivo é verificado. Nenhum tipo de evento é exigido da saída.
- Saída: cada linha não vazia da CLI aparece como texto. A Forja não lê eventos estruturados, erros nem consumo de tokens dessa CLI.
- Retomada: a descrição usada vai para o registro da execução. Uma execução retomada usa a descrição gravada, não a que está no perfil no momento.
- `--config`: a Forja grava no `execucao.toml` da execução a descrição das CLIs genéricas que essa execução usa, para que `--config` a repita mesmo sem o perfil.
- Descrição inválida: um `comando` vazio ou o campo com tipo errado é recusado ao ler o arquivo, com mensagem de configuração. Já um marcador desconhecido, ou um `comando` sem `{package}` e sem `pacote_por_stdin`, só é detectado quando o adaptador é montado, e a Forja não trata esse caso: a execução termina com o erro interno do Python (`ValueError`), com um texto como `unknown placeholders in the template: ['nome']` ou `the template must deliver the package: use {package} or stdin`.

### O que o genérico não faz

- Não passa o modelo. `--modelo`, `--modelo-testes`, `modelo` e `modelo_testes` não entram no `comando`. Se a CLI precisa de modelo, escreva-o direto no `comando`.
- Não restringe permissões por papel. A CLI pode fazer em cada papel mais do que o papel permite, e o resumo da execução registra isso. Os portões continuam olhando o que a tentativa mudou.
- Não define `NO_COLOR` nem outras variáveis. Só chegam à CLI as genéricas e as de `ambiente`.
- Não recebe instruções de voz por arquivo ou flag próprios. A seção de voz vai dentro do pacote, como para toda CLI.
- Não lê eventos, erros nem consumo de tokens.
- Não entra na sugestão de autor de testes.
- A prova de vida é mínima: uma CLI que cria `probe.txt` passa, mesmo que responda mal às outras tarefas.
- `--config` executa o `comando` do arquivo, que roda um programa qualquer. Use só um `execucao.toml` em que você confia.

## Instruções de voz por CLI

A seção `## Voice` entra no pacote de toda chamada que escreve texto (implementador, autor de testes, especificador, arqueólogo, documentador), em qualquer CLI, inclusive a genérica.

Além do pacote, só o Claude Code recebe a instrução por um segundo canal: o mesmo texto vai como prompt de sistema adicional, por `--append-system-prompt-file`, de um arquivo dentro de `.forja-agent`. O agente não pode editá-lo, e mudá-lo rejeita o trabalho. O arquivo acrescenta ao prompt de sistema padrão da CLI, não o substitui.

`codex`, `cursor`, `grok` e a CLI genérica recebem a voz só pelo pacote. A Forja não usa canal próprio dessas CLIs porque a documentação delas não confirma que o canal valha em modo headless, e passar uma opção desconhecida pode derrubar a chamada.
