# dev/clean-room/Makefile: build the clean-room image and run the check or a try-it shell in it.
#
# TARGETS
#   make gate    build the image, then run run.sh against TREE inside a container with no network,
#                a read-only root filesystem and TREE mounted read-only. Exit status is run.sh's.
#   make try     build the image, then open a shell as a fresh user with TREE mounted read-only at
#                /tree. Network is on (a Claude login needs it); nothing else from the host is mounted.
#   make build   build the image only.
#   make clean   remove the image.
#
# VARIABLES
#   TREE                 the tree to install: a public checkout or an `export.py stage` output
#                        (default: the checkout this Makefile sits in, two levels up)
#   IMAGE                image tag (default repacted-cleanroom:<CLAUDE_CODE_VERSION>)
#   CLAUDE_CODE_VERSION  CLI version baked into the image (default 2.1.288, as in public CI)
#   DOCKER               docker command (default docker)
#
# Every container is started with --rm, so none is left behind.

CLAUDE_CODE_VERSION ?= 2.1.288
IMAGE ?= repacted-cleanroom:$(CLAUDE_CODE_VERSION)
TREE ?= $(shell cd ../.. && pwd)
DOCKER ?= docker

.PHONY: build gate try clean check-tree

check-tree:
	@test -f "$(TREE)/.claude-plugin/marketplace.json" || \
	  { echo "TREE=$(TREE) has no .claude-plugin/marketplace.json; pass TREE=<staged tree or public checkout>"; exit 2; }

build:
	$(DOCKER) build --build-arg CLAUDE_CODE_VERSION=$(CLAUDE_CODE_VERSION) -t $(IMAGE) .

gate: check-tree build
	$(DOCKER) run --rm --network none --read-only --tmpfs /tmp:rw,exec,size=2g \
	  --mount type=bind,source="$(TREE)",target=/tree,readonly \
	  $(IMAGE) bash /opt/cleanroom/run.sh --expect-cli $(CLAUDE_CODE_VERSION) /tree

try: check-tree build
	@echo "Fresh user, no plugin. To install the tree under test:"
	@echo "  claude plugin marketplace add /tree && claude plugin install repacted@<name it prints>"
	@echo "Or run the check: bash /opt/cleanroom/run.sh /tree"
	$(DOCKER) run --rm -it --mount type=bind,source="$(TREE)",target=/tree,readonly $(IMAGE)

clean:
	$(DOCKER) image rm $(IMAGE)
