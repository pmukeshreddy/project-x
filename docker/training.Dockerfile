# Linux x86_64 training image for the current feature-rl stack.
# Versions are the ones the training code and frozen SkyRL skyrl-v0.3.0 lock already require:
#   Python 3.12, Ray 2.56.0, Torch 2.11.0+cu128, vLLM 0.23.0+cu129,
#   Transformers 5.8.0, SkyRL f5bc3b78dfddfb352870d5d7430cd226e5785838,
#   Harbor 0.13.1 at 3de07a0e01f3368921766437fc7afece3ddec23d,
#   then the project overlay Pydantic 2.13.5 / pydantic-core 2.46.5.
# Model weights, DeepSWE artifacts, datasets, checkpoints, and training outputs are not copied in.
# The upstream SkyRL Dockerfile also installs the CUDA 12.8 toolkit for DeepSpeed source builds.
# This FSDP profile uses the prebuilt cu128/cu129 wheels on the Ray cu128 runtime.

FROM --platform=linux/amd64 anyscale/ray:2.56.0-slim-py312-cu128@sha256:668299e09552447461ceb120d88cd1ad26ce486f821a41398e132d3fb62f46c2

# Record the image Python before switching user. Login shell picks up the image PATH.
RUN bash -lc 'python -c "import sys; assert sys.version_info[:2]==(3, 12), sys.version; open(\"/tmp/feature-rl-python\", \"w\").write(sys.executable)"'

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/skyrl/.venv/bin:/usr/local/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git build-essential libnuma-dev libxml2 \
    && rm -rf /var/lib/apt/lists/*

# Client only. Task containers are started on the host daemon through the mounted socket.
RUN set -euxo pipefail \
    && curl -fsSL -o /tmp/uv.tar.gz https://github.com/astral-sh/uv/releases/download/0.9.4/uv-x86_64-unknown-linux-gnu.tar.gz \
    && echo "e02f7fc102d6a1ebfa3b260b788e9adf35802be28c8d85640e83246e61519c1e  /tmp/uv.tar.gz" | sha256sum -c - \
    && mkdir -p /tmp/uv-extract \
    && tar -xzf /tmp/uv.tar.gz -C /tmp/uv-extract \
    && install -m 755 "$(find /tmp/uv-extract -type f -name uv -print -quit)" /usr/local/bin/uv \
    && curl -fsSL -o /tmp/docker.tgz https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
    && echo "4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640  /tmp/docker.tgz" | sha256sum -c - \
    && tar -xOf /tmp/docker.tgz docker/docker > /usr/local/bin/docker \
    && chmod 755 /usr/local/bin/docker \
    && mkdir -p /usr/libexec/docker/cli-plugins \
    && curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-buildx https://github.com/docker/buildx/releases/download/v0.20.1/buildx-v0.20.1.linux-amd64 \
    && echo "8c38f60308a895fa570f1410e453c5de11aafd65a99fa99965d96d24b6225a78  /usr/libexec/docker/cli-plugins/docker-buildx" | sha256sum -c - \
    && chmod 755 /usr/libexec/docker/cli-plugins/docker-buildx \
    && rm -rf /tmp/uv.tar.gz /tmp/uv-extract /tmp/docker.tgz \
    && uv --version \
    && docker --version \
    && docker buildx version

RUN set -euxo pipefail \
    && git clone --depth 1 --branch skyrl-v0.3.0 https://github.com/NovaSky-AI/SkyRL.git /opt/skyrl \
    && git config --system --add safe.directory /opt/skyrl \
    && test "$(git -C /opt/skyrl rev-parse HEAD)" = "f5bc3b78dfddfb352870d5d7430cd226e5785838" \
    && python_bin="$(cat /tmp/feature-rl-python)" \
    && cd /opt/skyrl \
    && uv sync --frozen --no-dev --extra fsdp --extra harbor --python "$python_bin" \
    && git -C /opt/skyrl checkout -- . \
    && git -C /opt/skyrl diff --quiet HEAD -- \
    && mkdir -p /tmp/overlay \
    && curl -fsSL -o /tmp/overlay/pydantic-2.13.5-py3-none-any.whl \
        https://files.pythonhosted.org/packages/eb/47/c95ffc2009878c7aac0c5e08528022dcb885933252a88b5f170058014464/pydantic-2.13.5-py3-none-any.whl \
    && echo "346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73  /tmp/overlay/pydantic-2.13.5-py3-none-any.whl" | sha256sum -c - \
    && curl -fsSL -o /tmp/overlay/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl \
        https://files.pythonhosted.org/packages/c0/a4/eb9409ec0736e50aa70a412f16c204ed149516846912f7e6724d4c73ee53/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl \
    && echo "0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2  /tmp/overlay/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl" | sha256sum -c - \
    && printf '%s\n' \
        'pydantic==2.13.5 --hash=sha256:346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73' \
        'pydantic-core==2.46.5 --hash=sha256:0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2' \
        > /tmp/overlay/requirements.txt \
    && cd /tmp/overlay \
    && uv pip install --python /opt/skyrl/.venv/bin/python --no-deps --require-hashes --no-index --find-links /tmp/overlay -r /tmp/overlay/requirements.txt \
    && cd /opt/skyrl \
    && uv pip install --python /opt/skyrl/.venv/bin/python --no-deps --editable /opt/skyrl \
    && git -C /opt/skyrl checkout -- . \
    && rm -rf /tmp/overlay /tmp/feature-rl-python /root/.cache /tmp/uv-cache \
    && git -C /opt/skyrl diff --quiet HEAD --

COPY pyproject.toml /opt/feature-rl/pyproject.toml
COPY src /opt/feature-rl/src

# --no-deps keeps the frozen SkyRL closure. Only the Pydantic pair above is replaced.
# SkyRL stays an editable checkout: NativeSession requires skyrl.__file__ to live under skyrl_checkout.
RUN set -euxo pipefail \
    && uv pip install --python /opt/skyrl/.venv/bin/python --no-deps /opt/feature-rl \
    && /opt/skyrl/.venv/bin/python -c 'import importlib.metadata as metadata, json, shutil; from pathlib import Path; import feature_rl.cli, feature_rl.training.native, ray, skyrl, torch, transformers; assert Path(skyrl.__file__).resolve().is_relative_to("/opt/skyrl"); assert metadata.version("torch").split("+")[0]=="2.11.0"; assert metadata.version("vllm").split("+")[0]=="0.23.0"; assert metadata.version("transformers")=="5.8.0"; assert metadata.version("ray")=="2.56.0"; assert metadata.version("pydantic")=="2.13.5"; assert metadata.version("pydantic-core")=="2.46.5"; harbor=json.loads(metadata.distribution("harbor").read_text("direct_url.json")); assert harbor["vcs_info"]["commit_id"]=="3de07a0e01f3368921766437fc7afece3ddec23d"; assert shutil.which("docker"); assert shutil.which("feature-rl"); assert torch.__version__.split("+")[0]=="2.11.0"; assert transformers.__version__=="5.8.0"; assert ray.__version__.split("+")[0]=="2.56.0"' \
    && feature-rl --help >/dev/null \
    && feature-rl config-schema >/dev/null \
    && git -C /opt/skyrl rev-parse HEAD \
    && git -C /opt/skyrl diff --quiet HEAD -- \
    && rm -rf /root/.cache

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

RUN mkdir -p /artifacts /models /checkpoints /config /workspace

WORKDIR /workspace

CMD ["feature-rl", "--help"]
