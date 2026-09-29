# Monólito modular

A aplicação reúne Auth, CRM, RH, Documents, Accounting e Stock num único processo FastAPI. Cada domínio continua no seu diretório e mantém os próprios modelos, rotas, inicialização e pacote `core`; o proxy conserva os caminhos públicos `/api/{modulo}/...` e encaminha as chamadas localmente, sem rede entre containers.

## Executar

Configure o `.env` com as variáveis de `.env.example` e inicie o stack padrão:

```sh
make up
```

A aplicação fica em `http://localhost:5002`; `GET /health` informa os módulos carregados. PostgreSQL, MinIO e Redis continuam como dependências externas. Os serviços antigos podem ser iniciados para compatibilidade usando `docker compose --profile microservices up -d` (não junto ao monólito, pois compartilham portas).

## Escopo atual

Foram agregados os seis serviços que estavam ativos no Compose: Auth, CRM, RH, Documents, Accounting e Stock. Finance e Projects tinham as rotas desativadas; ERP Agent e WhatsApp também não faziam parte do stack ativo e permanecem fora desta primeira migração.

## Testes

```sh
cd modular_monolith
python -m pytest tests -q
```
