.PHONY: help build release run stop logs clean

help:
	@echo "GoPro Backup Media — Docker Management"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  build      Build the Docker image for the current platform"
	@echo "  release    Build and push multi-platform Docker image to Docker Hub"
	@echo "  run        Run the Docker container"
	@echo "  stop       Stop the Docker container"
	@echo "  logs       Tail logs from the Docker container"
	@echo "  clean      Stop and remove the Docker container"
	@echo ""
	@echo "Required Environment Variables:"
	@echo "  AUTH_TOKEN   GoPro authentication token"
	@echo "  USER_ID      GoPro user ID"
	@echo ""
	@echo "Examples:"
	@echo "  make build"
	@echo "  make run AUTH_TOKEN=<token> USER_ID=<id>"
	@echo "  make release"

# Docker configuration
CONTAINER_NAME ?= gopro-backup
BUILD_PLATFORMS ?= linux/amd64,linux/arm64,linux/arm/v7
IMAGE := janghanbin/gopro-backup
VERSION := $(shell cat VERSION.txt)
IMAGE_WITH_VERSION = $(IMAGE):$(VERSION)

# Runtime configuration (from environment)
AUTH_TOKEN ?= $(shell echo $$AUTH_TOKEN)
USER_ID ?= $(shell echo $$USER_ID)
ACTION ?= download
DOWNLOAD_MODE ?= zip
WORKERS ?= 3
START_PAGE ?= 1
PAGES ?= 1000000
PER_PAGE ?= 30
DOWNLOAD_PATH ?= ./download
PROGRESS_MODE ?= noline
RETRY_COUNT ?= 5
RETRY_DELAY ?= 5
VERBOSE ?= false

build:
	@docker build -t $(IMAGE_WITH_VERSION) -t $(IMAGE):latest .

release:
	@docker buildx build \
		--platform $(BUILD_PLATFORMS) \
		-t $(IMAGE_WITH_VERSION) \
		-t $(IMAGE):latest \
		--push .

run: clean
	@docker run -d --name $(CONTAINER_NAME) \
		-v $(PWD)/download:/app/download \
		-e AUTH_TOKEN=$(AUTH_TOKEN) \
		-e USER_ID=$(USER_ID) \
		-e ACTION=$(ACTION) \
		-e DOWNLOAD_MODE=$(DOWNLOAD_MODE) \
		-e WORKERS=$(WORKERS) \
		-e START_PAGE=$(START_PAGE) \
		-e PAGES=$(PAGES) \
		-e PER_PAGE=$(PER_PAGE) \
		-e DOWNLOAD_PATH=$(DOWNLOAD_PATH) \
		-e PROGRESS_MODE=$(PROGRESS_MODE) \
		-e RETRY_COUNT=$(RETRY_COUNT) \
		-e RETRY_DELAY=$(RETRY_DELAY) \
		-e VERBOSE=$(VERBOSE) \
		$(IMAGE_WITH_VERSION)

stop:
	@docker stop $(CONTAINER_NAME) || true

logs:
	@docker logs -f $(CONTAINER_NAME)

clean: stop
	@docker rm $(CONTAINER_NAME) || true
