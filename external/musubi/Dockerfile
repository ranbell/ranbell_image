FROM nvidia/cuda:13.2.1-runtime-ubuntu24.04

# Time zone of the run folders' time stamps; set TZ in .env to change it.
ARG TZ=UTC
ENV TZ=${TZ}
ENV DEBIAN_FRONTEND=noninteractive

# Same base as docker-comfyui. The venv and musubi-tuner live in ./work
# (bind-mounted), so the image stays thin and a rebuild keeps them.
RUN apt-get update && \
    apt-get install -y python3.12 python3-pip python3.12-venv python3.12-dev git curl libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# `train <bucket>` from anywhere inside the container.
RUN ln -s /work/bin/train /usr/local/bin/train && \
    ln -s /work/bin/status /usr/local/bin/status && \
    ln -s /work/bin/publish /usr/local/bin/publish && \
    ln -s /work/bin/setup.sh /usr/local/bin/musubi-setup

WORKDIR "/work"

CMD ["sleep", "infinity"]
