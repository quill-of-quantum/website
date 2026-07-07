package com.bbdwz.emailservice.provider.auth;

import jakarta.mail.Authenticator;

import java.util.Properties;

public interface MailAuthStrategy {

    void applyImap(Properties properties);

    void applySmtp(Properties properties);

    Authenticator authenticator();
}
