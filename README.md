# Script_Linux_Debian
Zabbix, GLPI, Docker, Kebernetes, PHP8, Apache2 e MySql em um click
#!/bin/bash

# Script de instalação completa para servidor Debian 12
# Recomendado executar como root

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# Função para log
log() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Função para verificar se o MySQL/MariaDB está funcionando
check_mysql() {
    local max_attempts=5
    local wait_time=10
    
    for ((i=1; i<=max_attempts; i++)); do
        log "Verificando conexão MySQL... tentativa $i de $max_attempts"
        if mysqladmin ping > /dev/null 2>&1; then
            log "MySQL está respondendo!"
            return 0
        fi
        sleep $wait_time
    done
    return 1
}

# Verificar se está rodando como root
if [ "$EUID" -ne 0 ]; then 
    error "Por favor, execute como root"
fi

# Atualização inicial do sistema
log "Atualizando o sistema..."
apt-get update && apt-get upgrade -y || error "Falha na atualização do sistema"

# Instalação de pacotes essenciais
log "Instalando pacotes essenciais..."
apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    software-properties-common \
    wget \
    git \
    net-tools \
    vim \
    htop \
    iptables \
    ufw \
    fail2ban \
    sudo || error "Falha na instalação dos pacotes essenciais"

# Apache2
log "Instalando Apache2..."
apt-get install -y apache2 || error "Falha na instalação do Apache2"

# PHP 8.2 e extensões
log "Instalando PHP e extensões..."
apt-get install -y \
    php8.2 \
    php8.2-common \
    php8.2-mysql \
    php8.2-cli \
    php8.2-common \
    php8.2-curl \
    php8.2-gd \
    php8.2-intl \
    php8.2-ldap \
    php8.2-mbstring \
    php8.2-xml \
    php8.2-zip \
    php8.2-opcache \
    libapache2-mod-php8.2 || error "Falha na instalação do PHP"

# Habilitar módulo PHP no Apache
a2enmod php8.2

# MySQL (MariaDB)
log "Instalando MySQL (MariaDB)..."
apt-get install -y mariadb-server mariadb-client || error "Falha na instalação do MySQL"

# Iniciar MySQL e verificar status
log "Iniciando serviço MySQL..."
service mariadb start

# Verificar se o MySQL está rodando
if ! check_mysql; then
    error "Falha ao iniciar o MySQL. Verifique os logs em /var/log/mysql/"
fi

# Configurar senha root do MySQL
MYSQL_ROOT_PASS=$(openssl rand -hex 12)
log "Configurando senha root do MySQL..."
mysqladmin -u root password "$MYSQL_ROOT_PASS"

# Docker
log "Instalando Docker..."
apt-get remove -y docker docker-engine docker.io containerd runc || true
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io || error "Falha na instalação do Docker"

# Docker Compose
log "Instalando Docker Compose..."
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Kubernetes
log "Instalando Kubernetes..."
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.28/deb/Release.key | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.28/deb/ /" | tee /etc/apt/sources.list.d/kubernetes.list
apt-get update
apt-get install -y kubelet kubeadm kubectl || error "Falha na instalação do Kubernetes"

# Zabbix
log "Instalando Zabbix..."
wget https://repo.zabbix.com/zabbix/6.4/debian/pool/main/z/zabbix-release/zabbix-release_6.4-1+debian12_all.deb
dpkg -i zabbix-release_6.4-1+debian12_all.deb
apt-get update
apt-get install -y \
    zabbix-server-mysql \
    zabbix-frontend-php \
    zabbix-apache-conf \
    zabbix-sql-scripts \
    zabbix-agent || error "Falha na instalação do Zabbix"

# Configurar bancos de dados
log "Configurando banco de dados do Zabbix..."
ZABBIX_DB_PASS=$(openssl rand -hex 12)
mysql -uroot -p"$MYSQL_ROOT_PASS" -e "
CREATE DATABASE zabbix character set utf8mb4 collate utf8mb4_bin;
CREATE USER 'zabbix'@'localhost' IDENTIFIED BY '$ZABBIX_DB_PASS';
GRANT ALL PRIVILEGES ON zabbix.* TO 'zabbix'@'localhost';
FLUSH PRIVILEGES;"

# Importar schema inicial do Zabbix
zcat /usr/share/zabbix-sql-scripts/mysql/server.sql.gz | mysql -uzabbix -p"$ZABBIX_DB_PASS" zabbix

# GLPI
log "Instalando GLPI..."
GLPI_VERSION="10.0.10"
wget https://github.com/glpi-project/glpi/releases/download/$GLPI_VERSION/glpi-$GLPI_VERSION.tgz
tar xzf glpi-$GLPI_VERSION.tgz -C /var/www/html/
chown -R www-data:www-data /var/www/html/glpi
chmod -R 755 /var/www/html/glpi

log "Configurando banco de dados do GLPI..."
GLPI_DB_PASS=$(openssl rand -hex 12)
mysql -uroot -p"$MYSQL_ROOT_PASS" -e "
CREATE DATABASE glpi CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'glpi'@'localhost' IDENTIFIED BY '$GLPI_DB_PASS';
GRANT ALL PRIVILEGES ON glpi.* TO 'glpi'@'localhost';
FLUSH PRIVILEGES;"

# Configurações de rede
log "Configurando rede..."
# Configurar UFW
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 10050/tcp # Zabbix Agent
ufw allow 10051/tcp # Zabbix Server
ufw --force enable

# Configurar Fail2ban
cat > /etc/fail2ban/jail.local <<EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = ssh
logpath = %(sshd_log)s
backend = %(sshd_backend)s
EOF

# Iniciar serviços
log "Iniciando serviços..."
for service in apache2 mariadb zabbix-server zabbix-agent docker; do
    log "Iniciando $service..."
    service $service start || log "Aviso: Falha ao iniciar $service"
done

# Limpeza
log "Realizando limpeza..."
apt-get autoremove -y
apt-get clean

# Salvar senhas em arquivo seguro
log "Salvando credenciais..."
cat > /root/credentials.txt <<EOF
MySQL root password: $MYSQL_ROOT_PASS
Zabbix Database Password: $ZABBIX_DB_PASS
GLPI Database Password: $GLPI_DB_PASS
EOF
chmod 600 /root/credentials.txt

# Configurar zabbix_server.conf
log "Configurando Zabbix Server..."
sed -i "s/# DBPassword=/DBPassword=$ZABBIX_DB_PASS/" /etc/zabbix/zabbix_server.conf

log "Instalação concluída!"
echo "-------------------------------------------"
echo "Informações importantes:"
echo "1. As senhas dos bancos de dados foram salvas em /root/credentials.txt"
echo "2. Configure o Zabbix em: http://seu_servidor/zabbix"
echo "3. Configure o GLPI em: http://seu_servidor/glpi"
echo "4. Execute 'kubeadm init' para iniciar o cluster Kubernetes"
echo "5. Verifique os logs em /var/log/ para possíveis erros"
echo "-------------------------------------------"

log "Verificando status dos serviços..."
for service in apache2 mariadb zabbix-server zabbix-agent docker; do
    service $service status || log "Aviso: $service pode não estar rodando"
done

log "Script finalizado! Verifique os logs para garantir que tudo foi instalado corretamente."
