FROM python:3.11.7-slim-bookworm

LABEL com.danswer.maintainer="founders@docubrain.app"
LABEL com.danswer.description="This image is the backend container of DocuBrain. \
Please visit https://github.com/docubrain-dot-app/docubrain"

# Build argument for Craft support (disabled by default)
# Use --build-arg ENABLE_CRAFT=true to include Node.js and opencode CLI
ARG ENABLE_CRAFT=false

# DO_NOT_TRACK is used to disable telemetry for Unstructured
ENV DANSWER_RUNNING_IN_DOCKER="true" \
    DO_NOT_TRACK="true" \
    PLAYWRIGHT_BROWSERS_PATH="/app/.cache/ms-playwright"

# Create non-root user for security best practices
RUN groupadd -g 1001 docubrain && \
    useradd -u 1001 -g docubrain -m -s /bin/bash docubrain && \
    mkdir -p /var/log/docubrain && \
    chmod 755 /var/log/docubrain && \
    chown docubrain:docubrain /var/log/docubrain

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /bin/

# Install system dependencies
# cmake needed for psycopg (postgres)
# libpq-dev needed for psycopg (postgres)
# curl included just for users' convenience
# zip for Vespa step futher down
# ca-certificates for HTTPS
RUN apt-get update && \
    apt-get install -y \
        cmake \
        curl \
        zip \
        ca-certificates \
        libgnutls30 \
        libblkid1 \
        libmount1 \
        libsmartcols1 \
        libuuid1 \
        libxmlsec1-dev \
        pkg-config \
        gcc \
        nano \
        vim \
        # Install procps so kubernetes exec sessions can use ps aux for debugging
        procps \
        libjemalloc2 \
        && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

# Conditionally install Node.js 20 for Craft (required for Next.js)
# Only installed when ENABLE_CRAFT=true
RUN if [ "$ENABLE_CRAFT" = "true" ]; then \
        echo "Installing Node.js 20 for Craft support..." && \
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
        apt-get install -y nodejs && \
        rm -rf /var/lib/apt/lists/*; \
    fi

# Conditionally install opencode CLI for Craft agent functionality
# Only installed when ENABLE_CRAFT=true
# TODO: download a specific, versioned release of the opencode CLI
RUN if [ "$ENABLE_CRAFT" = "true" ]; then \
        echo "Installing opencode CLI for Craft support..." && \
        curl -fsSL https://opencode.ai/install | bash; \
    fi
ENV PATH="/root/.opencode/bin:${PATH}"

# Install Python dependencies
# Remove py which is pulled in by retry, py is not needed and is a CVE
COPY ./requirements/default.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache-dir --upgrade \
        -r /tmp/requirements.txt && \
    pip uninstall -y py && \
    playwright install chromium && \
    playwright install-deps chromium && \
    chown -R docubrain:docubrain /app && \
    ln -s /usr/local/bin/supervisord /usr/bin/supervisord && \
    # Cleanup for CVEs and size reduction
    # https://github.com/tornadoweb/tornado/issues/3107
    # xserver-common and xvfb included by playwright installation but not needed after
    # perl-base is part of the base Python Debian image but not needed for DocuBrain functionality
    # perl-base could only be removed with --allow-remove-essential
    apt-get update && \
    apt-get remove -y --allow-remove-essential \
        perl-base \
        xserver-common \
        xvfb \
        cmake \
        libldap-2.5-0 \
        libxmlsec1-dev \
        pkg-config \
        gcc && \
    # Install here to avoid some packages being cleaned up above
    apt-get install -y \
        libxmlsec1-openssl \
        # Install postgresql-client for easy manual tests
        postgresql-client && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf ~/.cache/uv /tmp/requirements.txt && \
    rm -f /usr/local/lib/python3.11/site-packages/tornado/test/test.key

# Pre-downloading models for setups with limited egress
RUN python -c "from tokenizers import Tokenizer; \
Tokenizer.from_pretrained('nomic-ai/nomic-embed-text-v1')"

# Pre-downloading NLTK for setups with limited egress
RUN python -c "import nltk; \
    nltk.download('stopwords', quiet=True); \
    nltk.download('punkt_tab', quiet=True);"
# nltk.download('wordnet', quiet=True); introduce this back if lemmatization is needed

# Pre-downloading tiktoken for setups with limited egress
RUN python -c "import tiktoken; \
tiktoken.get_encoding('cl100k_base')"

# Set up application files
WORKDIR /app

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Set up application files
COPY --chown=docubrain:docubrain ./docubrain /app/docubrain
COPY --chown=docubrain:docubrain ./shared_configs /app/shared_configs
COPY --chown=docubrain:docubrain ./alembic /app/alembic
COPY --chown=docubrain:docubrain ./alembic_tenants /app/alembic_tenants
COPY --chown=docubrain:docubrain ./alembic.ini /app/alembic.ini
COPY supervisord.conf /usr/etc/supervisord.conf
COPY --chown=docubrain:docubrain ./static /app/static
COPY --chown=docubrain:docubrain ./keys /app/keys

# Escape hatch scripts
COPY --chown=docubrain:docubrain ./scripts/debugging /app/scripts/debugging
COPY --chown=docubrain:docubrain ./scripts/force_delete_connector_by_id.py /app/scripts/force_delete_connector_by_id.py
COPY --chown=docubrain:docubrain ./scripts/supervisord_entrypoint.sh /app/scripts/supervisord_entrypoint.sh
COPY --chown=docubrain:docubrain ./scripts/setup_craft_templates.sh /app/scripts/setup_craft_templates.sh
COPY --chown=docubrain:docubrain ./scripts/reencrypt_secrets.py /app/scripts/reencrypt_secrets.py
RUN chmod +x /app/scripts/supervisord_entrypoint.sh /app/scripts/setup_craft_templates.sh

# Run Craft template setup at build time when ENABLE_CRAFT=true
# This pre-bakes demo data, Python venv, and npm dependencies into the image
RUN if [ "$ENABLE_CRAFT" = "true" ]; then \
        echo "Running Craft template setup at build time..." && \
        ENABLE_CRAFT=true /app/scripts/setup_craft_templates.sh; \
    fi

# Set Craft template paths to the in-image locations
# These match the paths where setup_craft_templates.sh creates the templates
ENV OUTPUTS_TEMPLATE_PATH=/app/docubrain/server/features/build/sandbox/kubernetes/docker/templates/outputs
ENV VENV_TEMPLATE_PATH=/app/docubrain/server/features/build/sandbox/kubernetes/docker/templates/venv

# Ensure assets directory exists (some builds don't include backend/assets)
RUN mkdir -p /app/assets && chown -R docubrain:docubrain /app/assets

ENV PYTHONPATH=/app

# Default DOCUBRAIN_VERSION, typically overriden during builds by GitHub Actions.
ARG DOCUBRAIN_VERSION=0.0.0-dev
ENV DOCUBRAIN_VERSION=${DOCUBRAIN_VERSION}

# Use jemalloc instead of glibc malloc to reduce memory fragmentation
# in long-running Python processes (API server, Celery workers).
# The soname is architecture-independent; the dynamic linker resolves
# the correct path from standard library directories.
# Placed after all RUN steps so build-time processes are unaffected.
ENV LD_PRELOAD=libjemalloc.so.2

# Default command which does nothing
# This container is used by api server and background which specify their own CMD
CMD ["tail", "-f", "/dev/null"]
