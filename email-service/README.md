# email-service

Spring Boot 3 multi-account mail service. It provides SMTP sending, read-only IMAP mailbox access,
one IMAP IDLE listener per enabled account, connection status, and local copy-forwarding rules.
The HTTP server binds only to `127.0.0.1:8081`; the Flask website is the authenticated frontend.

## Secret handling

- The existing QQ account remains compatible with `.env` (`MAIL_ADDRESS`, `MAIL_USERNAME`,
  `MAIL_PASSWORD`, and the existing `MAIL_IMAP_*` / `MAIL_SMTP_*` variables).
- Additional accounts live in `config/accounts.yml`, which is ignored by Git.
- Copy `config/accounts.example.yml` as a starting point. Never commit the real file.
- The account/status APIs intentionally never return usernames or passwords.

```bash
cp config/accounts.example.yml config/accounts.yml
chmod 600 config/accounts.yml .env
```

## API

- `GET /api/mail/accounts`: account status, default account, forwarding rules, and recent executions
- `POST /api/mail/send`: send using an explicit `accountId`
- `POST /api/mail/send/default`: send using the administrator-selected default account
- `PUT /api/mail/default-account`: change the default account
- `GET /api/mail/accounts/{id}/messages`: read recent message headers without marking mail read
- `GET /api/mail/accounts/{id}/messages/{uid}`: read one message body without marking it read
- `POST /api/mail/accounts/{id}/test`: authenticate to IMAP and SMTP
- `POST /api/mail/forwarding`: add a local copy-forwarding rule
- `PUT /api/mail/forwarding/{ruleId}`: edit or enable/disable a rule
- `DELETE /api/mail/forwarding/{ruleId}`: delete a rule

Send requests include `accountId`, arrays for `to` / `cc` / `bcc`, `subject`, and `text`.
Forwarding is performed locally only while this service is running. The original message stays in
the source mailbox. Multiple rules are supported and the latest 200 execution results are retained
without message bodies. Settings and history live in `data/forwarding.json`, which is ignored by Git.

## Build

```bash
mvn test
mvn package
```
