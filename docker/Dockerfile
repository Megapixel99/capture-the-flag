FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV CONTAINER_ROLE=flat

# Install base packages — SSH, networking tools, Python, cron, common utilities
RUN apt-get update && apt-get install -y \
    openssh-server \
    python3 \
    python3-pip \
    curl \
    wget \
    net-tools \
    iputils-ping \
    nmap \
    netcat-openbsd \
    tcpdump \
    vim \
    nano \
    cron \
    sudo \
    iproute2 \
    dnsutils \
    hydra \
    john \
    nikto \
    procps \
    lsof \
    iptables \
    sshpass \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Configure SSH
RUN mkdir /var/run/sshd
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
RUN sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Copy and run vulnerability setup
COPY setup-vulnerabilities.sh /tmp/setup-vulnerabilities.sh
COPY setup-internal.sh /tmp/setup-internal.sh
RUN chmod +x /tmp/setup-vulnerabilities.sh /tmp/setup-internal.sh && /tmp/setup-vulnerabilities.sh

EXPOSE 22 8080

CMD ["/opt/start.sh"]
