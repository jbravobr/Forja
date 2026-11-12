# 0009. A redação cobre só o que a Forja envia

- Status: Aceito
- Data: 10/10/2026

## Contexto

O pacote enviado ao modelo pode conter segredos e dados pessoais. Mascarar também os arquivos da cópia de trabalho pareceria mais seguro, mas o agente leria `[REDACTED]` no código e o gravaria no arquivo real.

## Decisão

A redação age só no pacote que a Forja monta. Ela usa 21 regras próprias de segredos, mais dados pessoais (CPF e CNPJ com dígitos verificadores, e-mail, telefone, cartão com Luhn). Os marcadores numeram os valores distintos por categoria: `[REDACTED:<regra>:<n>]` e `[PII:<tipo>:<n>]`. Se a redação falha, nada é enviado. Se ela remove algo essencial, isso vira premissa. O registro do pacote enviado guarda hash, regras e contagens, nunca valores, e fixa a impressão digital das regras. Valores marcados como exemplo ou espaço reservado em documentação não são mascarados.

## Consequências

- O que a CLI lê por conta própria depende do nível de isolamento; no nível reduzido, o resto do disco é só monitorado.
- A redação não detecta nomes, endereços nem segredos codificados.
- Os padrões do repositório não são verificados contra ReDoS.
- O gate de segredos do incremento 1 vai sinalizar os valores falsos dos testes e precisará de uma lista de exceções.

Ver a especificação, seção "O que sai para o LLM: redação do pacote".
