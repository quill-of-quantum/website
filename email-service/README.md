# email-service

Spring Boot 3 email service with SMTP sending, IMAP IDLE receiving, and a provider/auth abstraction for later OAuth or API-based implementations.

## Run locally

```bash
export MAIL_ADDRESS="your_email@example.com"
export MAIL_USERNAME="your_email@example.com"
export MAIL_PASSWORD="your_app_password"
export MAIL_IMAP_HOST="outlook.office365.com"
export MAIL_IMAP_PORT="993"
export MAIL_SMTP_HOST="smtp-mail.outlook.com"
export MAIL_SMTP_PORT="587"
export MAIL_SMTP_SSL="false"
export MAIL_RECEIVER_ENABLED="true"

mvn spring-boot:run
```

## Send mail

```bash
curl -X POST http://localhost:8081/api/mail/send \
  -H 'Content-Type: application/json' \
  -d '{"to":"yourself@example.com","subject":"测试通知","text":"这是一封测试邮件"}'
```

## Test account login

```bash
./scripts/test-mail-login.sh
```

## Outlook notes

Outlook.com IMAP/SMTP currently uses:

- IMAP: `outlook.office365.com:993`, SSL/TLS
- SMTP: `smtp-mail.outlook.com:587`, STARTTLS

Microsoft now requires Modern Auth / OAuth2 for many Outlook and Microsoft 365 scenarios. This first version keeps password/app-password auth isolated behind `MailAuthStrategy`, so it can be replaced later.
