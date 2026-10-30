# Deploying Senovka ERP

Run the deployment as a user that can update the application checkout and
manage the `senovka_erp` and `nginx` services (for example, with `sudo`
configured for those commands).

Do not run `makemigrations` on the server. Create and review migrations
locally, commit them, and deploy them with `migrate`.

## Deployment script

Save this as `/var/www/senovka_erp/deploy.sh` and make it executable:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/var/www/senovka_erp"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE="senovka_erp"

cd "$PROJECT_DIR"

if [[ ! -d .git || ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Missing Git checkout or virtual environment in $PROJECT_DIR" >&2
    exit 1
fi

echo "Updating source..."
git pull --ff-only origin main

echo "Installing dependencies..."
"$VENV_DIR/bin/python" -m pip install --requirement requirements.txt

echo "Checking Django configuration..."
"$VENV_DIR/bin/python" manage.py check

echo "Applying committed migrations..."
"$VENV_DIR/bin/python" manage.py migrate --noinput

echo "Collecting static files..."
"$VENV_DIR/bin/python" manage.py collectstatic --noinput

echo "Restarting application service..."
sudo systemctl restart "$SERVICE"
sudo systemctl is-active --quiet "$SERVICE"

echo "Reloading Nginx..."
sudo systemctl reload nginx
sudo systemctl is-active --quiet nginx

echo "Deployment completed successfully."
```

```bash
sudo install --mode=0755 /path/to/deploy.sh /var/www/senovka_erp/deploy.sh
sudo /var/www/senovka_erp/deploy.sh
```

The script stops at the first failed command. It uses `git pull --ff-only` so
an unexpected server-side commit or branch divergence cannot be silently
overwritten. Keep the checkout and virtual environment writable by the
deployment user; do not use `chown -R` as part of every deployment.

## Preparing a schema change

From a development checkout:

```bash
python manage.py makemigrations
python manage.py migrate
git add core/migrations/
git commit -m "Add database migration"
git push origin main
```

The deployment script applies the committed migration after the new code and
dependencies are installed.
