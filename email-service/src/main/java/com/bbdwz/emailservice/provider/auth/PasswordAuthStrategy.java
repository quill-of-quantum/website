package com.bbdwz.emailservice.provider.auth;

import com.bbdwz.emailservice.config.MailAccountProperties;
import jakarta.mail.Authenticator;
import jakarta.mail.PasswordAuthentication;
import org.springframework.stereotype.Component;

import java.util.Properties;

@Component
public class PasswordAuthStrategy implements MailAuthStrategy {

    private final MailAccountProperties properties;

    public PasswordAuthStrategy(MailAccountProperties properties) {
        this.properties = properties;
    }

    @Override
    public void applyImap(Properties target) {
        target.put("mail.imap.auth", "true");
        target.put("mail.imaps.auth", "true");
    }

    @Override
    public void applySmtp(Properties target) {
        target.put("mail.smtp.auth", "true");
    }

    @Override
    public Authenticator authenticator() {
        return new Authenticator() {
            @Override
            protected PasswordAuthentication getPasswordAuthentication() {
                return new PasswordAuthentication(
                        properties.getAccount().getUsername(),
                        properties.getAccount().getPassword()
                );
            }
        };
    }
}
