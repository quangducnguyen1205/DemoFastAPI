DOCKER ?= docker
COMPOSE_FILE ?=
COMPOSE = $(DOCKER) compose $(if $(COMPOSE_FILE),-f $(COMPOSE_FILE),)
PROJECT3_COMPOSE = $(DOCKER) compose -f docker-compose.yml -f docker-compose.project3.yml
PROJECT3_SERVICES = db redis backend worker consumer result-relay

BACKEND_SERVICE ?= backend
WORKER_SERVICE ?= worker
CONSUMER_SERVICE ?= consumer

UP_FLAGS ?= -d
BUILD_ARGS ?=
LOGS_ARGS ?= -f --tail=100
VOLUMES ?= 0
VOLUME_FLAG = $(if $(filter 1 true TRUE yes YES y Y,$(VOLUMES)),-v,)
CONTAINER_SHELL ?= sh -lc 'if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi'

.DEFAULT_GOAL := help

.PHONY: help \
	up down restart build rebuild logs ps clean pull \
	standalone-up project3-config \
	backend-up backend-down backend-build backend-rebuild backend-logs backend-shell \
	worker-up worker-down worker-build worker-rebuild worker-logs worker-shell \
	consumer-up consumer-down consumer-build consumer-rebuild consumer-logs consumer-shell

help: ## Show available commands
	@printf "\nDocker Compose shortcuts\n\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / { printf "  %-18s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\nVariables:\n"
	@printf "  COMPOSE_FILE      Optional compose file override\n"
	@printf "  BACKEND_SERVICE   Backend service name (default: %s)\n" "$(BACKEND_SERVICE)"
	@printf "  WORKER_SERVICE    Worker service name (default: %s)\n" "$(WORKER_SERVICE)"
	@printf "  CONSUMER_SERVICE  Consumer service name (default: %s)\n" "$(CONSUMER_SERVICE)"
	@printf "  VOLUMES=1         Remove named volumes when running 'make clean'\n"
	@printf "  LOGS_ARGS         Override log options, example: LOGS_ARGS='--tail=200'\n\n"
	@printf "Integrated Project3 targets:\n"
	@printf "  up                Start the coherent backend/worker/consumer/result-relay topology without building\n"
	@printf "  project3-config   Render and validate the integrated Compose configuration\n\n"

up: ## Start the coherent Project3 topology without building or pulling images
	$(PROJECT3_COMPOSE) up $(UP_FLAGS) --no-build --pull never $(PROJECT3_SERVICES)

project3-config: ## Render the coherent Project3 Compose configuration without starting services
	$(PROJECT3_COMPOSE) config

standalone-up: ## Start all active services in detached mode
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
$(eval $(call SERVICE_TARGETS,consumer,$(CONSUMER_SERVICE)))
