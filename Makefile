DOCKER ?= docker
COMPOSE_FILE ?=
COMPOSE = $(DOCKER) compose $(if $(COMPOSE_FILE),-f $(COMPOSE_FILE),)

BACKEND_SERVICE ?= backend
WORKER_SERVICE ?= worker
TEST_SERVICE ?= test

UP_FLAGS ?= -d
BUILD_ARGS ?=
LOGS_ARGS ?= -f --tail=100
VOLUMES ?= 0
VOLUME_FLAG = $(if $(filter 1 true TRUE yes YES y Y,$(VOLUMES)),-v,)
CONTAINER_SHELL ?= sh -lc 'if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi'

.DEFAULT_GOAL := help

.PHONY: help \
	up down restart build rebuild logs ps clean pull \
	backend-up backend-down backend-build backend-rebuild backend-logs backend-shell \
	worker-up worker-down worker-build worker-rebuild worker-logs worker-shell \
	test-up test-down test-build test-rebuild test-logs test-shell

help: ## Show available commands
	@printf "\nDocker Compose shortcuts\n\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / { printf "  %-18s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\nVariables:\n"
	@printf "  COMPOSE_FILE      Optional compose file override\n"
	@printf "  BACKEND_SERVICE   Backend service name (default: %s)\n" "$(BACKEND_SERVICE)"
	@printf "  WORKER_SERVICE    Worker service name (default: %s)\n" "$(WORKER_SERVICE)"
	@printf "  TEST_SERVICE      Test service name (default: %s)\n" "$(TEST_SERVICE)"
	@printf "  VOLUMES=1         Remove named volumes when running 'make clean'\n"
	@printf "  LOGS_ARGS         Override log options, example: LOGS_ARGS='--tail=200'\n\n"

up: ## Start all active services in detached mode
	$(COMPOSE) up $(UP_FLAGS)

down: ## Stop and remove all services
	$(COMPOSE) down --remove-orphans

restart: ## Restart all running services
	$(COMPOSE) restart

build: ## Build all services
	$(COMPOSE) build $(BUILD_ARGS)

rebuild: ## Rebuild and restart all services
	$(COMPOSE) up -d --build --force-recreate

logs: ## Show logs for all services
	$(COMPOSE) logs $(LOGS_ARGS)

ps: ## Show running containers
	$(COMPOSE) ps

clean: ## Remove containers and networks; add VOLUMES=1 to remove volumes too
	$(COMPOSE) down --remove-orphans $(VOLUME_FLAG)

pull: ## Pull latest images when available
	$(COMPOSE) pull

define SERVICE_TARGETS
$(1)-up: ## Start the $(1) service in detached mode
	$(COMPOSE) up $(UP_FLAGS) $(2)

$(1)-down: ## Stop the $(1) service
	$(COMPOSE) stop $(2)

$(1)-build: ## Build the $(1) service
	$(COMPOSE) build $(BUILD_ARGS) $(2)

$(1)-rebuild: ## Rebuild and restart the $(1) service
	$(COMPOSE) up -d --build --force-recreate $(2)

$(1)-logs: ## Show logs for the $(1) service
	$(COMPOSE) logs $(LOGS_ARGS) $(2)

$(1)-shell: ## Open a shell inside the $(1) container
	$(COMPOSE) exec $(2) $(CONTAINER_SHELL)
endef

$(eval $(call SERVICE_TARGETS,backend,$(BACKEND_SERVICE)))
$(eval $(call SERVICE_TARGETS,worker,$(WORKER_SERVICE)))
$(eval $(call SERVICE_TARGETS,test,$(TEST_SERVICE)))
