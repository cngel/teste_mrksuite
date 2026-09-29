.PHONY: help up down build logs restart test ci-build ci-up ci-smoke ci-down ci-validate ci

COMPOSE      := docker compose
CI_COMPOSE   := docker compose -f docker-compose.ci.yml --env-file .env.ci -p geasto_ci

help:
	@echo "Stack de desenvolvimento (docker-compose.yml, precisa de .env real):"
	@echo "  make up            sobe o stack de dev em segundo plano"
	@echo "  make down          desliga o stack de dev"
	@echo "  make build         reconstrói as imagens do stack de dev"
	@echo "  make logs          segue os logs de todos os serviços"
	@echo "  make restart       reinicia o app: make restart s=modular-monolith"
	@echo ""
	@echo "Testes:"
	@echo "  make test          corre os testes dos módulos e do monólito (sem stack)"
	@echo ""
	@echo "CI/CD — stack dedicado e efémero (docker-compose.ci.yml, .env.ci fictício):"
	@echo "  make ci-build      constrói a imagem do monólito modular"
	@echo "  make ci-up         sobe o stack de CI em segundo plano"
	@echo "  make ci-smoke      valida o monólito modular (ci-up primeiro)"
	@echo "  make ci-down       deita abaixo o stack de CI e limpa volumes"
	@echo "  make ci-validate   ci-up + ci-smoke + ci-down, sempre limpa no final"
	@echo "  make ci            ci-validate + test — o que corre no GitHub Actions"

# --------------------------------------------------------------------------
# Stack de desenvolvimento
# --------------------------------------------------------------------------

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

logs:
	$(COMPOSE) logs -f

restart:
	$(COMPOSE) restart $(s)

# --------------------------------------------------------------------------
# Testes
# --------------------------------------------------------------------------

TEST_SERVICES := erp-agent auth-service crm-service documents-service \
				  projects-service finance-service rh-service web-service accounting-service stock-service \
				  modular_monolith

test:
	@status=0; \
	for s in $(TEST_SERVICES); do \
		echo "=== Testes: $$s ==="; \
		( cd $$s && \
		  ( [ -d venv ] || python3 -m venv venv ) && \
		  venv/bin/python3 -m pip install -q -r requirements.txt && \
		  PYTHONPATH=.. venv/bin/python3 -m pytest tests/ -v ) || status=1; \
	done; \
	exit $$status

# --------------------------------------------------------------------------
# CI/CD — stack dedicado, efémero, sem tocar no stack de dev nem em
# segredos reais (ver docker-compose.ci.yml e .env.ci).
# --------------------------------------------------------------------------

ci-build:
	$(CI_COMPOSE) build

ci-up:
	$(CI_COMPOSE) up -d --build

ci-smoke:
	$(CI_COMPOSE) run --rm validator

ci-down:
	$(CI_COMPOSE) down -v --remove-orphans

ci-validate: ci-up
	@$(MAKE) ci-smoke; status=$$?; \
	$(MAKE) ci-down; \
	exit $$status

ci:
	@$(MAKE) ci-validate; status=$$?; \
	$(MAKE) test; test_status=$$?; \
	if [ $$status -ne 0 ] || [ $$test_status -ne 0 ]; then exit 1; fi
