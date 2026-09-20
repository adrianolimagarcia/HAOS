FROM rust:1.85-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 jq procps ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY packages /workspace/packages
COPY scripts /workspace/scripts
COPY hermes /workspace/hermes
RUN cargo build --manifest-path packages/haos-exec/Cargo.toml --release \
 && ln -s packages/haos-exec/target /workspace/target
CMD ["bash"]
