#!/bin/bash
# =============================================================================
# Internal Server Vulnerability Setup
# =============================================================================
# Called from start.sh when CONTAINER_ROLE=internal
# Sets up different vulnerabilities than the DMZ containers.
# =============================================================================

H=$(hostname)

# Vuln 1: Root password is "internal123"
echo "root:internal123" | chpasswd

# Vuln 2: Service user with sudo
useradd -m -s /bin/bash internaladmin
echo "internaladmin:admin456" | chpasswd
echo "internaladmin ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/internaladmin
chmod 0440 /etc/sudoers.d/internaladmin

# Vuln 3: SQL injection web service on port 8080
# Creates a Python HTTP server with a /search?q= endpoint that does unsanitized SQL
# The database has a "data" table with rows including one that mentions the flag path
mkdir -p /opt/webapp
# Create the SQLite database with hints
sqlite3 /opt/internal-app.db "CREATE TABLE data (id INTEGER, name TEXT, info TEXT); INSERT INTO data VALUES (1, 'config', 'Internal server configuration v2.1'); INSERT INTO data VALUES (2, 'secret_location', 'The flag is stored at /root/flag.txt'); INSERT INTO data VALUES (3, 'backup_key', 'AES key: not-the-real-key-12345');"
chmod 644 /opt/internal-app.db

# Write the vulnerable web service
cat > /opt/webapp/internal_server.py << 'WEBAPP'
#!/usr/bin/env python3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sqlite3
import json

class InternalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == '/search':
            q = params.get('q', [''])[0]
            try:
                conn = sqlite3.connect('/opt/internal-app.db')
                cursor = conn.cursor()
                # VULNERABILITY: SQL injection
                cursor.execute(f"SELECT * FROM data WHERE name LIKE '%{q}%'")
                rows = cursor.fetchall()
                conn.close()
                result = [{'id': r[0], 'name': r[1], 'info': r[2]} for r in rows]
            except Exception as e:
                result = {'error': str(e)}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        elif parsed.path == '/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'running', 'service': 'internal-data-api', 'version': '2.1'}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', 8080), InternalHandler)
    server.serve_forever()
WEBAPP
chmod +x /opt/webapp/internal_server.py

# Vuln 4: World-readable SSH private key
mkdir -p /root/.ssh /home/internaladmin/.ssh
ssh-keygen -t rsa -b 2048 -f /home/internaladmin/.ssh/id_rsa -N "" -q
cp /home/internaladmin/.ssh/id_rsa.pub /root/.ssh/authorized_keys
chmod 644 /home/internaladmin/.ssh/id_rsa  # World-readable!
chmod 600 /root/.ssh/authorized_keys

# Vuln 5: Writable /etc/crontab
chmod 666 /etc/crontab
echo "*/2 * * * * root /opt/scripts/maintenance.sh" >> /etc/crontab
mkdir -p /opt/scripts
echo '#!/bin/bash' > /opt/scripts/maintenance.sh
echo 'echo "maintenance ran" >> /tmp/maintenance.log' >> /opt/scripts/maintenance.sh
chmod 777 /opt/scripts/maintenance.sh

# Vuln 6: SUID on python3
chmod u+s /usr/bin/python3*

# Vuln 7: Readable /etc/shadow
chmod 644 /etc/shadow

echo "[internal] Vulnerabilities configured for ${H}"
