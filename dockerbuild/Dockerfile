FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y sudo gcc vim python3 git gdb make htop # buildkit

RUN useradd -s /bin/bash -u 1000 dcuuser && echo "dcuuser:dcuuser" | chpasswd && adduser dcuuser sudo
RUN echo 'dcuuser ALL=(ALL) NOPASSWD:ALL' | tee -a /etc/sudoers

USER dcuuser

WORKDIR /home/dcuuser
RUN git config --global user.name "dcuuser" # buildkit
RUN git config --global user.email dcuuser@test.com # buildkit
RUN echo 'set nu' > /home/dcuuser/.vimrc # buildkit