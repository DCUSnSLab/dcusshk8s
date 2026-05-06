# path : dcusshk8s/dockerbuild
# docker build -f Dockerfile -t harbor.cu.ac.kr/k8s_dynamic_allocator/user_pod:{tag} .
# docker push harbor.cu.ac.kr/k8s_dynamic_allocator/user_pod:{tag}

FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y sudo gcc vim python3 git gdb make htop openssh-server curl pip

# SSH 설정: PAM 비활성화, 공개키 인증 허용, Root 로그인 허용
RUN mkdir -p /run/sshd && \
    sed -i 's/UsePAM yes/UsePAM no/g' /etc/ssh/sshd_config && \
    sed -i '/Subsystem/d' /etc/ssh/sshd_config && \
    echo "Subsystem sftp /usr/lib/openssh/sftp-server" >> /etc/ssh/sshd_config && \
    echo "PubkeyAuthentication yes" >> /etc/ssh/sshd_config && \
    echo "PermitRootLogin yes" >> /etc/ssh/sshd_config

RUN useradd -s /bin/bash -u 1000 dcuuser && echo "dcuuser:dcuuser" | chpasswd && adduser dcuuser sudo
RUN echo 'dcuuser ALL=(ALL) NOPASSWD:ALL' | tee -a /etc/sudoers

USER dcuuser

WORKDIR /home/dcuuser
RUN git config --global user.name "dcuuser" # buildkit
RUN git config --global user.email dcuuser@test.com # buildkit
RUN echo 'set nu' > /home/dcuuser/.vimrc # buildkit

# run script install (switch to root)
USER root
RUN apt-get update && apt-get install -y jq dos2unix netcat-openbsd python3-requests && rm -rf /var/lib/apt/lists/*
COPY run /usr/local/bin/run
RUN dos2unix /usr/local/bin/run && chmod +x /usr/local/bin/run

USER dcuuser