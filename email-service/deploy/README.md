# Deploy on Raspberry Pi

## Install runtime

```bash
sudo apt update
sudo apt install -y default-jdk maven
```

## Configure

```bash
cd /home/bbdwz/projects/email-service
cp .env.example .env
nano .env
```

For Outlook.com:

```env
MAIL_IMAP_HOST=outlook.office365.com
MAIL_IMAP_PORT=993
MAIL_IMAP_SSL=true
MAIL_SMTP_HOST=smtp-mail.outlook.com
MAIL_SMTP_PORT=587
MAIL_SMTP_STARTTLS=true
```

## Build

```bash
cd /home/bbdwz/projects/email-service
mvn clean package
```

## Install systemd service

```bash
sudo cp deploy/email-service.service /etc/systemd/system/email-service.service
sudo systemctl daemon-reload
sudo systemctl enable --now email-service
sudo systemctl status email-service
```

## Logs

```bash
journalctl -u email-service -f
```
