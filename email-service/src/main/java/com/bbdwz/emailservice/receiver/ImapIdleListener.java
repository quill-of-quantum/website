package com.bbdwz.emailservice.receiver;

import com.bbdwz.emailservice.config.MailAccountProperties;
import com.bbdwz.emailservice.model.ReceivedMail;
import com.bbdwz.emailservice.provider.auth.MailAuthStrategy;
import com.sun.mail.imap.IMAPFolder;
import jakarta.mail.Address;
import jakarta.mail.BodyPart;
import jakarta.mail.Flags;
import jakarta.mail.Folder;
import jakarta.mail.Message;
import jakarta.mail.MessagingException;
import jakarta.mail.Multipart;
import jakarta.mail.Part;
import jakarta.mail.Session;
import jakarta.mail.Store;
import jakarta.mail.event.MessageCountAdapter;
import jakarta.mail.event.MessageCountEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.time.Instant;
import java.util.Arrays;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

@Component
public class ImapIdleListener {

    private static final Logger log = LoggerFactory.getLogger(ImapIdleListener.class);

    private final MailAccountProperties properties;
    private final MailAuthStrategy authStrategy;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile boolean running;
    private volatile Store store;
    private volatile IMAPFolder folder;

    public ImapIdleListener(MailAccountProperties properties, MailAuthStrategy authStrategy) {
        this.properties = properties;
        this.authStrategy = authStrategy;
    }

    public void start(Consumer<ReceivedMail> mailConsumer) {
        if (running) {
            return;
        }
        running = true;
        executor.submit(() -> listen(mailConsumer));
    }

    public void stop() {
        running = false;
        closeQuietly(folder);
        closeQuietly(store);
        executor.shutdownNow();
        try {
            if (!executor.awaitTermination(5, TimeUnit.SECONDS)) {
                log.warn("IMAP IDLE listener did not stop within timeout");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void listen(Consumer<ReceivedMail> mailConsumer) {
        while (running) {
            try {
                connect(mailConsumer);
                while (running && folder != null && folder.isOpen()) {
                    folder.idle();
                }
            } catch (Exception e) {
                if (running) {
                    log.warn("IMAP IDLE connection failed; retrying in 15 seconds", e);
                    sleepBeforeReconnect();
                }
            } finally {
                closeQuietly(folder);
                closeQuietly(store);
            }
        }
    }

    private void connect(Consumer<ReceivedMail> mailConsumer) throws MessagingException {
        Session session = Session.getInstance(imapProperties(), authStrategy.authenticator());
        String protocol = properties.getImap().isSsl() ? "imaps" : "imap";
        store = session.getStore(protocol);
        store.connect(
                properties.getImap().getHost(),
                properties.getImap().getPort(),
                properties.getAccount().getUsername(),
                properties.getAccount().getPassword()
        );

        Folder openedFolder = store.getFolder(properties.getImap().getFolder());
        openedFolder.open(Folder.READ_WRITE);
        folder = (IMAPFolder) openedFolder;
        folder.addMessageCountListener(new MessageCountAdapter() {
            @Override
            public void messagesAdded(MessageCountEvent event) {
                for (Message message : event.getMessages()) {
                    try {
                        mailConsumer.accept(toReceivedMail(message));
                        message.setFlag(Flags.Flag.SEEN, true);
                    } catch (Exception e) {
                        log.warn("Failed to process received mail", e);
                    }
                }
            }
        });
        log.info("IMAP IDLE listener connected to {}:{}", properties.getImap().getHost(), properties.getImap().getPort());
    }

    private Properties imapProperties() {
        Properties target = new Properties();
        String prefix = properties.getImap().isSsl() ? "mail.imaps" : "mail.imap";
        target.put(prefix + ".host", properties.getImap().getHost());
        target.put(prefix + ".port", String.valueOf(properties.getImap().getPort()));
        target.put(prefix + ".ssl.enable", String.valueOf(properties.getImap().isSsl()));
        target.put(prefix + ".connectiontimeout", "10000");
        target.put(prefix + ".timeout", "20000");
        authStrategy.applyImap(target);
        return target;
    }

    private ReceivedMail toReceivedMail(Message message) throws MessagingException, IOException {
        return new ReceivedMail(
                addressesToString(message.getFrom()),
                addressesToList(message.getRecipients(Message.RecipientType.TO)),
                message.getSubject(),
                extractText(message),
                message.getReceivedDate() == null ? Instant.now() : message.getReceivedDate().toInstant(),
                firstHeader(message, "Message-ID")
        );
    }

    private String extractText(Part message) throws MessagingException, IOException {
        if (message.isMimeType("text/plain")) {
            Object content = message.getContent();
            return content == null ? "" : content.toString();
        }
        if (message.isMimeType("text/html")) {
            Object content = message.getContent();
            return content == null ? "" : content.toString();
        }
        if (!message.isMimeType("multipart/*")) {
            Object content = message.getContent();
            return content instanceof String text ? text : "";
        }

        Object content = message.getContent();
        if (content instanceof String text) {
            return text;
        }
        if (content instanceof Multipart multipart) {
            String fallbackHtml = "";
            for (int i = 0; i < multipart.getCount(); i++) {
                BodyPart bodyPart = multipart.getBodyPart(i);
                if (Part.ATTACHMENT.equalsIgnoreCase(bodyPart.getDisposition())) {
                    continue;
                }
                String text = extractText(bodyPart);
                if (bodyPart.isMimeType("text/plain") && !text.isBlank()) {
                    return text;
                }
                if (bodyPart.isMimeType("text/html") && !text.isBlank()) {
                    fallbackHtml = text;
                }
            }
            return fallbackHtml;
        }
        return "";
    }

    private String addressesToString(Address[] addresses) {
        if (addresses == null || addresses.length == 0) {
            return "";
        }
        return addresses[0].toString();
    }

    private List<String> addressesToList(Address[] addresses) {
        if (addresses == null) {
            return List.of();
        }
        return Arrays.stream(addresses).map(Address::toString).toList();
    }

    private String firstHeader(Message message, String name) throws MessagingException {
        String[] values = message.getHeader(name);
        return values == null || values.length == 0 ? "" : values[0];
    }

    private void sleepBeforeReconnect() {
        try {
            TimeUnit.SECONDS.sleep(15);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void closeQuietly(Folder target) {
        if (target == null) {
            return;
        }
        try {
            if (target.isOpen()) {
                target.close(false);
            }
        } catch (MessagingException e) {
            log.debug("Failed to close IMAP folder", e);
        }
    }

    private void closeQuietly(Store target) {
        if (target == null) {
            return;
        }
        try {
            if (target.isConnected()) {
                target.close();
            }
        } catch (MessagingException e) {
            log.debug("Failed to close IMAP store", e);
        }
    }
}
