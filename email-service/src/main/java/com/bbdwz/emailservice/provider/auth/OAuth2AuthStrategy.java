package com.bbdwz.emailservice.provider.auth;

import jakarta.mail.Authenticator;

import java.util.Properties;

public class OAuth2AuthStrategy implements MailAuthStrategy {

    @Override
    public void applyImap(Properties properties) {
        properties.put("mail.imap.auth.mechanisms", "XOAUTH2");
        properties.put("mail.imaps.auth.mechanisms", "XOAUTH2");
    }

    @Override
    public void applySmtp(Properties properties) {
        properties.put("mail.smtp.auth.mechanisms", "XOAUTH2");
    }

    @Override
    public Authenticator authenticator() {
        throw new UnsupportedOperationException("OAuth2 auth is reserved for a later provider implementation.");
    }
}
