FROM python:3.6.8-alpine

WORKDIR /dcusshk8s

COPY . /dcusshk8s

RUN apk add build-base
# RUN apk add pip
RUN apk add libffi-dev
RUN apk add openssh-keygen

#RUN apk update
#RUN apk add py-pip
RUN pip install --upgrade pip

RUN pip install --editable .

RUN pip install pycryptodome

RUN apk add vim

WORKDIR /dcusshk8s/kubessh

RUN ssh-keygen -f dummy-kubessh-host-key

WORKDIR /dcusshk8s

RUN apk add curl
RUN curl -L https://storage.googleapis.com/kubernetes-release/release/v1.16.0/bin/linux/amd64/kubectl > /usr/local/bin/kubectl
RUN chmod +x /usr/local/bin/kubectl # buildkit

# RUN export PYTHONPATH=/dcusshk8s/kubessh/
ENV PYTHONPATH=/dcusshk8s/

# CMD ["python3", "./kubessh/app.py --KubeSSH.config_file=kubessh_dummy_config.py"]
# CMD ["python3", "./:kubessh/app.py"]
