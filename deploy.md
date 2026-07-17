echo "=== CREATING DEPLOY SCRIPT ===" && \
cat > /var/www/senovka_erp/deploy.sh << 'EOF'
#!/bin/bash
set -e

echo "======================================"
echo " Senovka ERP Deploy Script"
echo " $(date)"
echo "======================================"

PROJECT_DIR="/var/www/senovka_erp"
VENV="$PROJECT_DIR/venv/bin"

cd $PROJECT_DIR

echo ""
echo ">>> Pulling latest code from GitHub..."
git pull origin main

echo ""
echo ">>> Activating virtualenv and installing requirements..."
source $VENV/activate
pip install -r requirements.txt --quiet

echo ""
echo ">>> Running migrations..."
python manage.py migrate --noinput

echo ""
echo ">>> Collecting static files..."
python manage.py collectstatic --noinput

echo ""
echo ">>> Fixing permissions..."
chown -R www-data:www-data $PROJECT_DIR
chown www-data:www-data $PROJECT_DIR/db.sqlite3 2>/dev/null || true

echo ""
echo ">>> Restarting Gunicorn..."
systemctl restart senovka_erp
sleep 3

echo ""
echo ">>> Reloading Nginx..."
systemctl reload nginx

echo ""
echo ">>> Checking service status..."
systemctl status senovka_erp --no-pager

echo ""
echo "======================================"
echo " Deploy complete!"
echo "======================================"
EOF

chmod +x /var/www/senovka_erp/deploy.sh && \

echo "" && \
echo "=== FINAL SYSTEM CHECK ===" && \
echo "--- Gunicorn service ---" && \
systemctl status senovka_erp --no-pager && \
echo "" && \
echo "--- Nginx service ---" && \
systemctl status nginx --no-pager && \
echo "" && \
echo "--- Socket file ---" && \
ls -la /run/senovka/senovka_erp.sock && \
echo "" && \
echo "--- SSL Certificate ---" && \
certbot certificates 2>/dev/null | grep -E "Domains|Expiry" && \
echo "" && \
echo "--- Disk usage ---" && \
df -h / && \
echo "" && \
echo "=====================================" && \
echo " SENOVKA ERP IS FULLY DEPLOYED" && \
echo " https://senovkaplastics.cloud" && \
echo " Login: admin / admin123" && \
echo " Login: manager / manager123" && \
echo "=====================================" && \
echo "" && \
echo "=== TO DEPLOY FUTURE UPDATES RUN ===" && \
echo " bash /var/www/senovka_erp/deploy.sh" && \
echo ""



git pull origin main        
pip install -r requirements 
python manage.py migrate    
python manage.py collectstatic 
chown www-data             
systemctl restart senovka_erp 
systemctl reload nginx      



git config --global --add safe.directory /var/www/senovka_erp
cd /var/www/senovka_erp/senovka_erp# git config --global --add safe.directory /var/www/senovka_erp
cd /var/www/senovka_erp/senovka_erp# git pull
python manage.py seed_users
source /var/www/senovka_erp/venv/bin/activate
systemctl restart erp