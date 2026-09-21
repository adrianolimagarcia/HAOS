FROM rust:1.85-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pytest jq procps ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY Cargo.toml Cargo.lock* /workspace/
COPY packages /workspace/packages
COPY scripts /workspace/scripts
COPY hermes /workspace/hermes
COPY hermes_cli /workspace/hermes_cli
COPY tools /workspace/tools
COPY hermes_constants.py /workspace/hermes_constants.py
CMD ["bash"]
