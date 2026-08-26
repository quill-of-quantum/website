package com.bbdwz.emailservice.service;

import com.bbdwz.emailservice.config.MailAccountProperties;
import com.bbdwz.emailservice.event.MailReceivedEvent;
import com.bbdwz.emailservice.model.ReceivedMail;
import com.bbdwz.emailservice.model.SendMailRequest;
import com.sun.mail.imap.IMAPFolder;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import jakarta.mail.*;
import jakarta.mail.event.MessageCountAdapter;
import jakarta.mail.event.MessageCountEvent;
import jakarta.mail.internet.InternetAddress;
import jakarta.mail.internet.MimeBodyPart;
import jakarta.mail.internet.MimeMessage;
import jakarta.mail.internet.MimeMultipart;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;

@Service
public class MailAccountService {
    private final MailAccountProperties properties;
    private final ApplicationEventPublisher publisher;
    private final Map<String, ListenerState> listeners = new ConcurrentHashMap<>();
    private final Map<String, Instant> lastSent = new ConcurrentHashMap<>();

    public MailAccountService(MailAccountProperties properties, ApplicationEventPublisher publisher) {
        this.properties = properties;
        this.publisher = publisher;
    }

    @PostConstruct
    public void startListeners() {
        properties.getAccounts().forEach((id, account) -> {
            if (account.isEnabled() && configured(account) && account.getImap().isReceiverEnabled()) {
                ListenerState state = new ListenerState(id, account);
                listeners.put(id, state);
                state.start();
            }
        });
    }

    @PreDestroy
    public void stopListeners() { listeners.values().forEach(ListenerState::stop); }

    public Map<String, Object> accountStatuses() {
        Map<String, Object> result = new LinkedHashMap<>();
        properties.getAccounts().forEach((id, account) -> {
            ListenerState listener = listeners.get(id);
            Map<String, Object> status = new LinkedHashMap<>();
            status.put("id", id);
            status.put("displayName", account.getDisplayName());
            status.put("address", account.getAddress());
            status.put("enabled", account.isEnabled());
            status.put("configured", configured(account));
            status.put("imapHost", account.getImap().getHost());
            status.put("smtpHost", account.getSmtp().getHost());
            status.put("receiverEnabled", account.getImap().isReceiverEnabled());
            status.put("imapConnected", listener != null && listener.connected);
            status.put("lastError", listener == null ? null : listener.lastError);
            status.put("lastReceivedAt", listener == null ? null : listener.lastReceivedAt);
            status.put("lastSentAt", lastSent.get(id));
            result.put(id, status);
        });
        return result;
    }

    public boolean isAvailable(String accountId) {
        if (accountId == null) return false;
        MailAccountProperties.Account account = properties.getAccounts().get(accountId);
        return account != null && account.isEnabled() && configured(account);
    }

    public String firstAvailableAccountId() {
        return properties.getAccounts().entrySet().stream()
                .filter(entry -> entry.getValue().isEnabled() && configured(entry.getValue()))
                .map(Map.Entry::getKey)
                .findFirst()
                .orElseThrow(() -> new IllegalStateException("no available mail account"));
    }

    public void send(SendMailRequest request) { send(request, false); }

    public void send(SendMailRequest request, boolean locallyForwarded) {
        MailAccountProperties.Account account = requireAccount(request.getAccountId());
        if (request.getTo().isEmpty() && request.getCc().isEmpty() && request.getBcc().isEmpty()) {
            throw new IllegalArgumentException("at least one recipient is required");
        }
        try {
            Session session = Session.getInstance(smtpProperties(account), authenticator(account));
            MimeMessage message = new MimeMessage(session);
            message.setFrom(new InternetAddress(account.getAddress()));
            setRecipients(message, Message.RecipientType.TO, request.getTo());
            setRecipients(message, Message.RecipientType.CC, request.getCc());
            setRecipients(message, Message.RecipientType.BCC, request.getBcc());
            message.setSubject(request.getSubject(), StandardCharsets.UTF_8.name());
            if (locallyForwarded) message.setHeader("X-BBDWZ-Local-Forward", "1");
            if (request.getHtml() != null && !request.getHtml().isBlank()) {
                MimeBodyPart plain = new MimeBodyPart();
                plain.setText(request.getText(), StandardCharsets.UTF_8.name());
                MimeBodyPart html = new MimeBodyPart();
                html.setContent(request.getHtml(), "text/html; charset=UTF-8");
                MimeMultipart alternatives = new MimeMultipart("alternative");
                alternatives.addBodyPart(plain); alternatives.addBodyPart(html);
                message.setContent(alternatives);
            } else {
                message.setText(request.getText(), StandardCharsets.UTF_8.name());
            }
            Transport.send(message);
            lastSent.put(request.getAccountId(), Instant.now());
        } catch (MessagingException e) {
            throw new IllegalStateException("failed to send mail: " + rootMessage(e), e);
        }
    }

    public List<Map<String, Object>> listMessages(String accountId, int limit) {
        MailAccountProperties.Account account = requireAccount(accountId);
        try (Store store = connectStore(account)) {
            Folder folder = store.getFolder(account.getImap().getFolder());
            try {
                folder.open(Folder.READ_ONLY);
                int total = folder.getMessageCount();
                int start = Math.max(1, total - Math.max(1, Math.min(limit, 100)) + 1);
                Message[] messages = total == 0 ? new Message[0] : folder.getMessages(start, total);
                List<Map<String, Object>> result = new ArrayList<>();
                for (int i = messages.length - 1; i >= 0; i--) result.add(messageSummary(folder, messages[i]));
                return result;
            } finally { if (folder.isOpen()) folder.close(false); }
        } catch (MessagingException e) {
            throw new IllegalStateException("failed to read mailbox: " + rootMessage(e), e);
        }
    }

    public Map<String, Object> getMessage(String accountId, long uid) {
        MailAccountProperties.Account account = requireAccount(accountId);
        try (Store store = connectStore(account)) {
            Folder folder = store.getFolder(account.getImap().getFolder());
            try {
                folder.open(Folder.READ_ONLY);
                if (!(folder instanceof UIDFolder uidFolder)) throw new MessagingException("server does not support message UIDs");
                Message message = uidFolder.getMessageByUID(uid);
                if (message == null) throw new IllegalArgumentException("message not found");
                Map<String, Object> result = messageSummary(folder, message);
                String body = extractText(message);
                result.put("text", body.length() > 200_000 ? body.substring(0, 200_000) : body);
                result.put("cc", addresses(message.getRecipients(Message.RecipientType.CC)));
                result.put("messageId", Optional.ofNullable(message.getHeader("Message-ID")).filter(v -> v.length > 0).map(v -> v[0]).orElse(""));
                return result;
            } finally { if (folder.isOpen()) folder.close(false); }
        } catch (MessagingException | IOException e) {
            throw new IllegalStateException("failed to read message: " + rootMessage(e), e);
        }
    }

    public Map<String, String> testAccount(String accountId) {
        MailAccountProperties.Account account = requireAccount(accountId);
        String imap = "ok";
        String smtp = "ok";
        try (Store ignored = connectStore(account)) { /* authenticated */ }
        catch (Exception e) { imap = rootMessage(e); }
        try {
            Session session = Session.getInstance(smtpProperties(account), authenticator(account));
            try (Transport transport = session.getTransport("smtp")) {
                transport.connect(account.getSmtp().getHost(), account.getSmtp().getPort(), account.getUsername(), account.getPassword());
            }
        } catch (Exception e) { smtp = rootMessage(e); }
        return Map.of("imap", imap, "smtp", smtp);
    }

    private MailAccountProperties.Account requireAccount(String id) {
        MailAccountProperties.Account account = properties.getAccounts().get(id);
        if (account == null || !account.isEnabled()) throw new IllegalArgumentException("unknown or disabled account");
        if (!configured(account)) throw new IllegalArgumentException("account credentials are not configured");
        return account;
    }

    private boolean configured(MailAccountProperties.Account account) {
        return account.getAddress() != null && !account.getAddress().isBlank()
                && account.getUsername() != null && !account.getUsername().isBlank()
                && account.getPassword() != null && !account.getPassword().isBlank();
    }

    private Store connectStore(MailAccountProperties.Account account) throws MessagingException {
        String protocol = account.getImap().isSsl() ? "imaps" : "imap";
        Session session = Session.getInstance(imapProperties(account), authenticator(account));
        Store store = session.getStore(protocol);
        store.connect(account.getImap().getHost(), account.getImap().getPort(), account.getUsername(), account.getPassword());
        return store;
    }

    private Authenticator authenticator(MailAccountProperties.Account account) {
        return new Authenticator() {
            @Override protected PasswordAuthentication getPasswordAuthentication() {
                return new PasswordAuthentication(account.getUsername(), account.getPassword());
            }
        };
    }

    private Properties imapProperties(MailAccountProperties.Account account) {
        Properties p = new Properties();
        String prefix = account.getImap().isSsl() ? "mail.imaps" : "mail.imap";
        p.put(prefix + ".ssl.enable", String.valueOf(account.getImap().isSsl()));
        p.put(prefix + ".connectiontimeout", "10000");
        p.put(prefix + ".timeout", "20000");
        p.put(prefix + ".auth", "true");
        return p;
    }

    private Properties smtpProperties(MailAccountProperties.Account account) {
        Properties p = new Properties();
        String prefix = "mail.smtp";
        p.put(prefix + ".host", account.getSmtp().getHost());
        p.put(prefix + ".port", String.valueOf(account.getSmtp().getPort()));
        p.put(prefix + ".ssl.enable", String.valueOf(account.getSmtp().isSsl()));
        p.put(prefix + ".starttls.enable", String.valueOf(account.getSmtp().isStarttls()));
        p.put(prefix + ".starttls.required", String.valueOf(account.getSmtp().isStarttls()));
        p.put(prefix + ".auth", "true");
        p.put(prefix + ".auth.mechanisms", account.getSmtp().getAuthMechanisms());
        p.put(prefix + ".connectiontimeout", "10000");
        p.put(prefix + ".timeout", "20000");
        p.put(prefix + ".writetimeout", "20000");
        return p;
    }

    private void setRecipients(MimeMessage message, Message.RecipientType type, List<String> values) throws MessagingException {
        if (values != null && !values.isEmpty()) message.setRecipients(type, InternetAddress.parse(String.join(",", values)));
    }

    private Map<String, Object> messageSummary(Folder folder, Message message) throws MessagingException {
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("number", message.getMessageNumber());
        if (folder instanceof UIDFolder uidFolder) item.put("uid", uidFolder.getUID(message));
        item.put("from", addresses(message.getFrom()));
        item.put("to", addresses(message.getRecipients(Message.RecipientType.TO)));
        item.put("subject", Objects.toString(message.getSubject(), ""));
        item.put("receivedAt", message.getReceivedDate() == null ? null : message.getReceivedDate().toInstant());
        item.put("seen", message.isSet(Flags.Flag.SEEN));
        return item;
    }

    private List<String> addresses(Address[] values) {
        if (values == null) return List.of();
        return Arrays.stream(values).map(Object::toString).toList();
    }

    private String extractText(Part part) throws MessagingException, IOException {
        if (part.isMimeType("text/plain") || part.isMimeType("text/html")) return Objects.toString(part.getContent(), "");
        if (part.isMimeType("multipart/*") && part.getContent() instanceof Multipart multipart) {
            String fallback = "";
            for (int i = 0; i < multipart.getCount(); i++) {
                BodyPart body = multipart.getBodyPart(i);
                if (Part.ATTACHMENT.equalsIgnoreCase(body.getDisposition())) continue;
                String text = extractText(body);
                if (body.isMimeType("text/plain") && !text.isBlank()) return text;
                if (!text.isBlank()) fallback = text;
            }
            return fallback;
        }
        return "";
    }

    private ReceivedMail received(String accountId, Message message) throws MessagingException, IOException {
        String[] marker = message.getHeader("X-BBDWZ-Local-Forward");
        return new ReceivedMail(accountId, addresses(message.getFrom()).stream().findFirst().orElse(""),
                addresses(message.getRecipients(Message.RecipientType.TO)), Objects.toString(message.getSubject(), ""),
                extractText(message), message.getReceivedDate() == null ? Instant.now() : message.getReceivedDate().toInstant(),
                Optional.ofNullable(message.getHeader("Message-ID")).filter(v -> v.length > 0).map(v -> v[0]).orElse(""),
                marker != null && marker.length > 0);
    }

    private String rootMessage(Throwable e) {
        Throwable current = e;
        while (current.getCause() != null) current = current.getCause();
        return Objects.toString(current.getMessage(), current.getClass().getSimpleName());
    }

    private class ListenerState {
        private final String id;
        private final MailAccountProperties.Account account;
        private final ExecutorService executor;
        private volatile boolean running = true;
        private volatile boolean connected;
        private volatile String lastError;
        private volatile Instant lastReceivedAt;
        private volatile Store store;
        private volatile Folder folder;

        ListenerState(String id, MailAccountProperties.Account account) {
            this.id = id; this.account = account;
            this.executor = Executors.newSingleThreadExecutor(r -> new Thread(r, "imap-" + id));
        }
        void start() { executor.submit(this::run); }
        void run() {
            while (running) {
                try {
                    store = connectStore(account);
                    folder = store.getFolder(account.getImap().getFolder());
                    folder.open(Folder.READ_ONLY);
                    if (!(folder instanceof IMAPFolder imapFolder)) throw new MessagingException("server does not support IMAP IDLE");
                    imapFolder.addMessageCountListener(new MessageCountAdapter() {
                        @Override public void messagesAdded(MessageCountEvent event) {
                            for (Message message : event.getMessages()) try {
                                ReceivedMail mail = received(id, message);
                                lastReceivedAt = mail.receivedAt();
                                publisher.publishEvent(new MailReceivedEvent(MailAccountService.this, mail));
                            } catch (Exception e) { lastError = rootMessage(e); }
                        }
                    });
                    connected = true; lastError = null;
                    while (running && folder.isOpen()) imapFolder.idle();
                } catch (Exception e) {
                    connected = false; lastError = rootMessage(e);
                    if (running) try { TimeUnit.SECONDS.sleep(15); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
                } finally { close(); }
            }
        }
        void stop() { running = false; close(); executor.shutdownNow(); }
        void close() {
            connected = false;
            try { if (folder != null && folder.isOpen()) folder.close(false); } catch (Exception ignored) {}
            try { if (store != null && store.isConnected()) store.close(); } catch (Exception ignored) {}
        }
    }
}
